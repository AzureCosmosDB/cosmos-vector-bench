"""Runtime document sources for the Cosmos DB write benchmark."""

from __future__ import annotations

import asyncio
import bz2
import json
import multiprocessing as mp
import random
import time
import traceback
from os import urandom
from pathlib import Path

from config import (
    DOCUMENT_ID_FALLBACK_FIELD,
    DOC_JSON_FORMAT,
    FAKE_DATA_VECTOR_DIM,
    PARTITION_KEY_FIELDS,
    READ_BATCH_SIZE,
    SESSION_ID_ENABLED,
    SESSION_ID_MAX_DOCS,
    SESSION_ID_MIN_DOCS,
)

try:
    import ijson
except ImportError:
    ijson = None


def _new_guid_id(_urandom=urandom) -> str:
    raw = bytearray(_urandom(16))
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    value = raw.hex()
    return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}"


class SessionIdAssigner:
    """Assign one random session id to a bounded run of consecutive documents."""

    __slots__ = ("_enabled", "_max_docs", "_min_docs", "_remaining", "_session_id")

    def __init__(
        self,
        enabled: bool = SESSION_ID_ENABLED,
        min_docs: int = SESSION_ID_MIN_DOCS,
        max_docs: int = SESSION_ID_MAX_DOCS,
    ) -> None:
        self._enabled = enabled
        self._min_docs = min_docs
        self._max_docs = max_docs
        self._remaining = 0
        self._session_id = ""

    def add_to(self, doc: dict) -> dict:
        if not self._enabled:
            return doc
        if self._remaining == 0:
            self._session_id = _new_guid_id()
            self._remaining = random.randint(self._min_docs, self._max_docs)
        doc["sessionid"] = self._session_id
        self._remaining -= 1
        return doc


def make_doc(i: int, payload: str, session_ids: SessionIdAssigner | None = None) -> dict:
    """Create one synthetic benchmark document.

    This is used in fake-data mode when the benchmark needs to exercise Cosmos writes without reading a source corpus.
    Each generated document has a unique id, a GUID ``docid`` partition key, a title, a text payload, and a randomly
    generated ``emb`` embedding vector of ``FAKE_DATA_VECTOR_DIM`` floats in [-1, 1].
    """
    doc = {
        "id": _new_guid_id(),
        "docid": _new_guid_id(),
        "title": f"Document {i}",
        "text": payload,
        "emb": [round(random.uniform(-1.0, 1.0), 8) for _ in range(FAKE_DATA_VECTOR_DIM)],
    }
    return session_ids.add_to(doc) if session_ids is not None else doc


def _prepare_loaded_doc(
    doc: object,
    record_number: int,
    session_ids: SessionIdAssigner | None = None,
) -> dict:
    """Validate a loaded source record and ensure it has a Cosmos id.

    This runs for every JSON record read from file input before the record enters the worker queue.
    Every file record must contain the configured partition key field. Missing IDs fall back to that partition key value.
    Failing with the record number and available fields makes bad corpus or partition-key configuration easier to diagnose.
    """
    if not isinstance(doc, dict):
        raise ValueError(f"Loaded record {record_number} is {type(doc).__name__}, expected a JSON object")

    prepared = doc
    if session_ids is not None:
        session_ids.add_to(prepared)

    missing_fields = [
        field for field in PARTITION_KEY_FIELDS
        if field not in prepared or prepared[field] is None or prepared[field] == ""
    ]
    if missing_fields:
        raise ValueError(
            f"Loaded record {record_number} is missing required partition key field(s) {missing_fields!r}. "
            f"Available fields: {', '.join(sorted(str(key) for key in prepared.keys())[:20])}"
        )

    if "id" in prepared and prepared["id"]:
        if isinstance(prepared["id"], str):
            return prepared
        prepared["id"] = str(prepared["id"])
        return prepared

    if DOCUMENT_ID_FALLBACK_FIELD not in prepared or prepared[DOCUMENT_ID_FALLBACK_FIELD] in (None, ""):
        raise ValueError(
            f"Loaded record {record_number} is missing id and fallback field {DOCUMENT_ID_FALLBACK_FIELD!r}"
        )
    prepared["id"] = str(prepared[DOCUMENT_ID_FALLBACK_FIELD])
    return prepared


async def _iter_generated_doc_bulks(start_i: int, end_i: int, bulk_size: int, payload: str):
    """Yield generated document bulks as an async iterator.

    The worker orchestration expects an async iterable of document batches, regardless of the source type.
    This wrapper adapts the fake-data generator to that interface without adding sleeps or extra buffering.
    It lets fake and file modes share the same `run_worker_with_batches` implementation.
    """
    session_ids = SessionIdAssigner()
    for bulk_start in range(start_i, end_i, bulk_size):
        bulk_end = min(bulk_start + bulk_size, end_i)
        yield [make_doc(i, payload, session_ids) for i in range(bulk_start, bulk_end)]


async def _iter_queue_doc_bulks(work_queue: mp.Queue, bulk_size: int):
    """Yield queued file-input document bulks as an async iterator.

    This is the file-mode counterpart to the generated-document async iterator.
    It repeatedly pulls blocking queue reads onto a worker thread and yields non-empty document bulks to the writer.
    The shared async interface lets the insert scheduler stay independent of how documents are sourced.
    """
    buffered_docs = []
    done = False
    while True:
        while len(buffered_docs) < bulk_size and not done:
            item = await asyncio.to_thread(work_queue.get)
            if item is None:
                done = True
                break
            if isinstance(item, list):
                buffered_docs.extend(item)
            else:
                buffered_docs.append(item)

        if buffered_docs:
            docs = buffered_docs[:bulk_size]
            del buffered_docs[:bulk_size]
            yield docs
            continue

        if done:
            return


def _open_json_text_stream(filepath: str):
    """Open a plain or bz2-compressed JSON text stream.

    The file loader uses this helper so `.json`, `.jsonl`, and `.bz2` paths can be consumed through the same text interface.
    Compressed files are decompressed as they are read, which avoids needing a separate decompressed copy on disk.
    Using UTF-8 with BOM handling keeps common exported corpus files readable without extra preprocessing.
    """
    if Path(filepath).suffix.lower() == ".bz2":
        return bz2.open(filepath, "rt", encoding="utf-8-sig")
    return open(filepath, "rt", encoding="utf-8-sig")


def stream_json_docs(filepath: str, work_queue: mp.Queue, worker_count: int, status: dict | None = None, max_docs: int | None = None) -> dict:
    """Stream JSON records from disk into the worker queue.

    This producer function reads the configured JSON shape, prepares each document, and feeds workers through a multiprocessing queue.
    It tracks producer-side throughput separately from insert throughput so data loading time is visible but not counted as request service time.
    It always enqueues one sentinel per worker in `finally`, ensuring consumers can shut down even when parsing fails.
    """
    if DOC_JSON_FORMAT in {"array", "multiple_values"} and ijson is None:
        raise RuntimeError("ijson is required for DOC_JSON_FORMAT=array or multiple_values; run: python -m pip install -r requirements.txt")

    docs_read = 0
    pending_docs = []
    session_ids = SessionIdAssigner()
    started = time.perf_counter()
    if status is not None:
        status["docs_read"] = 0
        status["started_epoch"] = time.time()
        status["finished"] = False
        status["max_docs"] = max_docs

    def enqueue_doc(doc: dict) -> None:
        pending_docs.append(doc)
        if len(pending_docs) >= READ_BATCH_SIZE:
            work_queue.put(list(pending_docs))
            pending_docs.clear()

    def flush_pending_docs() -> None:
        if pending_docs:
            work_queue.put(list(pending_docs))
            pending_docs.clear()

    try:
        with _open_json_text_stream(filepath) as stream:
            if DOC_JSON_FORMAT == "jsonl":
                for line_number, line in enumerate(stream, start=1):
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        doc = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Invalid JSONL record at line {line_number}: {exc}") from exc
                    enqueue_doc(_prepare_loaded_doc(doc, line_number, session_ids))
                    docs_read += 1
                    if status is not None and docs_read % 1000 == 0:
                        status["docs_read"] = docs_read
                    if max_docs is not None and docs_read >= max_docs:
                        break
            elif DOC_JSON_FORMAT == "multiple_values":
                for doc in ijson.items(stream, "", multiple_values=True):
                    enqueue_doc(_prepare_loaded_doc(doc, docs_read + 1, session_ids))
                    docs_read += 1
                    if status is not None and docs_read % 1000 == 0:
                        status["docs_read"] = docs_read
                    if max_docs is not None and docs_read >= max_docs:
                        break
            else:
                for doc in ijson.items(stream, "item"):
                    enqueue_doc(_prepare_loaded_doc(doc, docs_read + 1, session_ids))
                    docs_read += 1
                    if status is not None and docs_read % 1000 == 0:
                        status["docs_read"] = docs_read
                    if max_docs is not None and docs_read >= max_docs:
                        break

        flush_pending_docs()

        elapsed = max(time.perf_counter() - started, 0.000001)
        result = {
            "docs_read": docs_read,
            "elapsed_sec": elapsed,
            "docs_per_sec": docs_read / elapsed,
        }
        if status is not None:
            status.update(result)
            status["finished"] = True
        return result
    finally:
        for _ in range(worker_count):
            work_queue.put(None)
        if status is not None:
            status["docs_read"] = docs_read
            status["sentinels_enqueued"] = worker_count


def _stream_json_docs_thread(filepath: str, work_queue: mp.Queue, worker_count: int, max_docs: int | None, status: dict) -> None:
    """Run the JSON producer in a thread and capture errors in shared status.

    The file-mode parent starts this wrapper so document loading can proceed while worker processes upload documents.
    Any exception is stored in the shared status dictionary for the parent loop to detect and report.
    This avoids losing producer failures behind a background thread boundary.
    """
    try:
        stream_json_docs(filepath, work_queue, worker_count, status, max_docs)
    except BaseException as exc:
        status["error"] = f"{exc!r}\n{traceback.format_exc()}"
