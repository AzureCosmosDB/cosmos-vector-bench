"""Add deterministic, non-consecutive session IDs to a JSONL corpus."""

from __future__ import annotations

import argparse
import json
import os
import random
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path


DEFAULT_MIN_SESSION_DOCS = 10
DEFAULT_MAX_SESSION_DOCS = 100
DEFAULT_SEED = 42
DEFAULT_PROGRESS_EVERY = 10_000
DEFAULT_SESSION_POOL_SIZE = 1_000


def _positive_int(value: str) -> int:
    try:
        parsed = int(value.replace("_", "").replace(",", ""))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"{value!r} must be >= 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a JSONL copy with deterministic sessionid values. Document order is preserved, "
            "existing sessionid values are replaced, and adjacent documents receive different IDs."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="Source uncompressed JSONL file.")
    parser.add_argument("--output", required=True, type=Path, help="Destination JSONL file (must differ from input).")
    parser.add_argument(
        "--min-session-docs",
        type=_positive_int,
        default=DEFAULT_MIN_SESSION_DOCS,
        help=f"Minimum uses per completed session; sessions active at EOF may be smaller (default: {DEFAULT_MIN_SESSION_DOCS}).",
    )
    parser.add_argument(
        "--max-session-docs",
        type=_positive_int,
        default=DEFAULT_MAX_SESSION_DOCS,
        help=f"Maximum uses per session (default: {DEFAULT_MAX_SESSION_DOCS}).",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"Deterministic random seed (default: {DEFAULT_SEED}).")
    parser.add_argument(
        "--session-pool-size",
        type=_positive_int,
        default=DEFAULT_SESSION_POOL_SIZE,
        help=f"Concurrently active session slots; must be >= 2 (default: {DEFAULT_SESSION_POOL_SIZE:,}).",
    )
    parser.add_argument(
        "--progress-every",
        type=_positive_int,
        default=DEFAULT_PROGRESS_EVERY,
        help=f"Report progress after this many documents (default: {DEFAULT_PROGRESS_EVERY:,}).",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing output file.")
    return parser.parse_args(argv)


def _load_json_object(raw: str, line_number: int) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSONL record at line {line_number}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSONL record at line {line_number} is {type(value).__name__}, expected an object")
    return value


def _deterministic_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


class RollingSessionAssigner:
    """Assign documents round-robin across a bounded pool of random-lifetime sessions."""

    def __init__(self, min_docs: int, max_docs: int, pool_size: int, seed: int) -> None:
        if min_docs < 1:
            raise ValueError("min_session_docs must be >= 1")
        if max_docs < min_docs:
            raise ValueError("max_session_docs must be greater than or equal to min_session_docs")
        if pool_size < 2:
            raise ValueError("session_pool_size must be >= 2")

        self._min_docs = min_docs
        self._max_docs = max_docs
        self._slots: list[list[object] | None] = [None] * pool_size
        self._active_ids: set[str] = set()
        self._rng = random.Random(seed)
        self._cursor = 0
        self.sessions_created = 0
        self.completed_sessions = 0
        self._completed_min: int | None = None
        self._completed_max = 0

    def next(self) -> str:
        slot_index = self._cursor
        self._cursor = (self._cursor + 1) % len(self._slots)
        session = self._slots[slot_index]
        if session is None:
            session_id = _deterministic_uuid(self._rng)
            while session_id in self._active_ids:
                session_id = _deterministic_uuid(self._rng)
            session = [session_id, self._rng.randint(self._min_docs, self._max_docs), 0]
            self._slots[slot_index] = session
            self._active_ids.add(session_id)
            self.sessions_created += 1

        session[2] = int(session[2]) + 1
        session_id = str(session[0])
        if session[2] == session[1]:
            count = int(session[2])
            self.completed_sessions += 1
            self._completed_min = count if self._completed_min is None else min(self._completed_min, count)
            self._completed_max = max(self._completed_max, count)
            self._active_ids.remove(session_id)
            self._slots[slot_index] = None
        return session_id

    @property
    def incomplete_sessions(self) -> int:
        return sum(session is not None for session in self._slots)

    @property
    def actual_min_docs(self) -> int:
        counts = [int(session[2]) for session in self._slots if session is not None]
        if self._completed_min is not None:
            counts.append(self._completed_min)
        return min(counts, default=0)

    @property
    def actual_max_docs(self) -> int:
        active_max = max((int(session[2]) for session in self._slots if session is not None), default=0)
        return max(active_max, self._completed_max)


def preprocess_jsonl(
    input_path: Path,
    output_path: Path,
    *,
    min_session_docs: int = DEFAULT_MIN_SESSION_DOCS,
    max_session_docs: int = DEFAULT_MAX_SESSION_DOCS,
    seed: int = DEFAULT_SEED,
    session_pool_size: int = DEFAULT_SESSION_POOL_SIZE,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
) -> dict[str, object]:
    input_path = input_path.resolve()
    output_path = output_path.resolve()

    if input_path == output_path:
        raise ValueError("Input and output paths must be different")
    if input_path.suffix.lower() == ".bz2":
        raise ValueError("Compressed input is not supported; decompress the JSONL file first")
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {output_path.parent}")
    if output_path.exists() and not force:
        raise FileExistsError(f"Output file already exists: {output_path}. Use --force to replace it.")
    if progress_every < 1:
        raise ValueError("progress_every must be >= 1")
    assigner = RollingSessionAssigner(min_session_docs, max_session_docs, session_pool_size, seed)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wt",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_stream:
            temp_path = Path(output_stream.name)
            document_count = 0
            overwritten_count = 0
            with input_path.open("rt", encoding="utf-8-sig") as input_stream:
                for line_number, line in enumerate(input_stream, start=1):
                    raw = line.strip()
                    if not raw:
                        continue
                    document = _load_json_object(raw, line_number)
                    if "sessionid" in document:
                        overwritten_count += 1
                    document["sessionid"] = assigner.next()
                    json.dump(document, output_stream, ensure_ascii=False, separators=(",", ":"))
                    output_stream.write("\n")
                    document_count += 1
                    if progress is not None and document_count % progress_every == 0:
                        progress(
                            f"Processed {document_count:,} JSON documents; added {document_count:,} session ID "
                            f"assignments; replaced {overwritten_count:,} existing session IDs..."
                        )

            output_stream.flush()
            os.fsync(output_stream.fileno())

        os.replace(temp_path, output_path)
        temp_path = None
        if progress is not None:
            progress(
                f"Completed: processed {document_count:,} JSON documents, added {document_count:,} session ID "
                f"assignments, and wrote {output_path}."
            )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return {
        "input": str(input_path),
        "output": str(output_path),
        "seed": seed,
        "documents_processed": document_count,
        "sessions_created": assigner.sessions_created,
        "session_pool_size": session_pool_size,
        "completed_sessions": assigner.completed_sessions,
        "incomplete_sessions": assigner.incomplete_sessions,
        "configured_min_session_docs": min_session_docs,
        "configured_max_session_docs": max_session_docs,
        "actual_min_session_docs": assigner.actual_min_docs,
        "actual_max_session_docs": assigner.actual_max_docs,
        "existing_sessionids_overwritten": overwritten_count,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = preprocess_jsonl(
            args.input,
            args.output,
            min_session_docs=args.min_session_docs,
            max_session_docs=args.max_session_docs,
            seed=args.seed,
            session_pool_size=args.session_pool_size,
            force=args.force,
            progress=lambda message: print(message, file=os.sys.stderr, flush=True),
            progress_every=args.progress_every,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1

    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
