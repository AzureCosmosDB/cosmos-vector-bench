"""Check a JSONL corpus for duplicate docid values."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find duplicate docid values in a JSONL file.")
    parser.add_argument("path", type=Path, help="Path to the JSONL data file.")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1_000,
        help="Print running findings after this many documents (default: 1,000).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.progress_every < 1:
        print("error: --progress-every must be at least 1", file=sys.stderr)
        return 1

    first_lines: dict[object, int] = {}
    duplicates: dict[object, list[int]] = {}
    document_count = 0
    missing_count = 0
    duplicate_document_count = 0

    try:
        with args.path.open("rt", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue

                document = json.loads(line)
                document_count += 1
                if "docid" not in document:
                    missing_count += 1
                else:
                    docid = document["docid"]
                    if docid in first_lines:
                        duplicates.setdefault(docid, [first_lines[docid]]).append(line_number)
                        duplicate_document_count += 1
                    else:
                        first_lines[docid] = line_number

                if document_count % args.progress_every == 0:
                    print(
                        f"Checked {document_count:,} documents: "
                        f"{len(duplicates):,} duplicate docids "
                        f"({duplicate_document_count:,} duplicate documents), "
                        f"{missing_count:,} missing docids."
                    )
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Documents checked: {document_count:,}")
    print(f"Documents missing docid: {missing_count:,}")
    print(f"Duplicate docids: {len(duplicates):,}")
    for docid, lines in duplicates.items():
        print(f"  {docid!r}: lines {', '.join(map(str, lines))}")

    return 2 if duplicates else 0


if __name__ == "__main__":
    raise SystemExit(main())