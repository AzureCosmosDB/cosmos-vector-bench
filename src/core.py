"""Cosmos write hot path and worker orchestration for the benchmark."""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import queue
import threading
import time
import traceback
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from azure.cosmos import exceptions
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential

from config import (
    BULK_SIZE,
    CAPTURE_RU_CHARGES,
    CLIENT_PROCESSES,
    CONTAINER,
    COSMOS_ERROR_SAMPLE_LIMIT,
    COSMOS_KEY,
    DATABASE,
    DOC_JSON_FORMAT,
    DOC_JSON_PATH,
    DOC_QUEUE_MULTIPLIER,
    DATA_TYPE,
    EFFECTIVE_TOTAL_DOCS,
    ENDPOINT,
    INSERT_RETRY_DELAY_MS,
    MAX_IN_FLIGHT,
    MAX_INSERT_RETRIES,
    MAX_PENDING_BULKS,
    MAX_TOTAL_DOCS,
    METRICS_SAMPLE_INTERVAL_SEC,
    PARTITION_KEY_RANGE_RPS_ENABLED,
    PARTITION_KEY_FIELDS,
    PAYLOAD_BYTES,
    READ_BATCH_SIZE,
)
from data import _iter_generated_doc_bulks, _iter_queue_doc_bulks, _stream_json_docs_thread
from metrics import (
    _aggregate_line,
    _live_line,
    _metric_snapshot,
    _new_metrics,
    _print_live_line,
    _print_parent_result,
    _print_result,
    _record_bulk_sample,
    _record_create_item_failure,
    _record_create_item_attempt,
    _record_partition_key_range_request,
    _record_request_charge,
    _record_throughput_sample,
    _record_upload_finished,
    _record_upload_started,
    _result_snapshot,
)

RETRYABLE_COSMOS_STATUS_CODES = {408, 429, 449, 500, 502, 503, 504}


def _endpoint_host() -> str:
    parsed = urlparse(ENDPOINT)
    return parsed.hostname or ENDPOINT.replace("https://", "").replace("http://", "").split("/", 1)[0].split(":", 1)[0]


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _is_dns_failure(exc: BaseException) -> bool:
    for item in _exception_chain(exc):
        item_type = type(item).__name__.lower()
        item_text = str(item).lower()
        if "gaierror" in item_type or "clientconnectordnserror" in item_type:
            return True
        if "getaddrinfo failed" in item_text or ("cannot connect to host" in item_text and "getaddrinfo" in item_text):
            return True
    return False


def _format_worker_exception(exc: BaseException) -> str:
    host = _endpoint_host()
    lines = [
        "[worker failure diagnostics]",
        f"endpoint={ENDPOINT}",
        f"endpoint_host={host}",
        f"database={DATABASE}",
        f"container={CONTAINER}",
        f"exception_type={type(exc).__name__}",
        f"exception={exc!r}",
        "exception_chain=",
    ]
    for index, item in enumerate(_exception_chain(exc), start=1):
        lines.append(f"  {index}. {type(item).__module__}.{type(item).__name__}: {item}")

    if _is_dns_failure(exc):
        lines.extend(
            [
                "diagnosis=DNS lookup failed before Cosmos authentication, database lookup, container lookup, or writes.",
                f"dns_check_powershell=Resolve-DnsName {host}",
                f"connectivity_check_powershell=Test-NetConnection {host} -Port 443",
                "likely_causes=wrong COSMOS_ENDPOINT, missing VPN/private DNS for staging/PPE endpoints, or process env vars overriding .env.",
                "env_override_note=This repo loads .env with override=False, so existing PowerShell env vars win over .env values.",
                "clear_endpoint_override=Remove-Item Env:\\COSMOS_ENDPOINT -ErrorAction SilentlyContinue",
            ]
        )

    lines.extend(["traceback=", traceback.format_exc()])
    return "\n".join(lines)

def _request_charge_from_headers(headers: object) -> float:
    """Extract the Cosmos request charge from response headers.

    Cosmos returns RU charge as an `x-ms-request-charge` header on successful and some failed operations.
    The write path uses this helper from response hooks and exception handling to keep RU accounting consistent.
    Returning `0.0` for missing or malformed headers lets metrics continue even when an SDK response shape lacks charge data.
    """
    if not headers:
        return 0.0

    get_header = getattr(headers, "get", None)
    if get_header is None:
        return 0.0

    raw = get_header("x-ms-request-charge")
    if raw is None:
        raw = get_header("X-MS-REQUEST-CHARGE")
    if raw is None:
        return 0.0

    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _partition_key_range_id_from_headers(headers: object) -> str | None:
    return _header_value(
        headers,
        "x-ms-partition-key-range-id",
        "X-MS-PARTITION-KEY-RANGE-ID",
        "x-ms-documentdb-partitionkeyrangeid",
        "x-ms-documentdb-partition-key-range-id",
    )


def _request_charge_from_exception(exc: exceptions.CosmosHttpResponseError) -> float:
    """Extract the Cosmos request charge from an exception response.

    Failed Cosmos writes can expose headers through different attributes depending on SDK internals and response type.
    Error handling calls this after catching `CosmosHttpResponseError` so throttles and conflicts still contribute any reported RU charge.
    Checking multiple response locations makes the metrics more robust across SDK versions.
    """
    charge = _request_charge_from_headers(getattr(exc, "headers", None))
    if charge:
        return charge

    response = getattr(exc, "response", None)
    charge = _request_charge_from_headers(getattr(response, "headers", None))
    if charge:
        return charge

    internal_response = getattr(response, "internal_response", None)
    return _request_charge_from_headers(getattr(internal_response, "headers", None))

def _headers_from_exception(exc: exceptions.CosmosHttpResponseError) -> object:
    """Return the best available headers object from a Cosmos exception.

    Retry handling and diagnostics both need access to response headers, but the SDK can expose them in several places.
    This helper mirrors the request-charge extraction path and returns the first header-like object it can find.
    Keeping header discovery in one place avoids repeating SDK response-shape checks in retry code.
    """
    response = getattr(exc, "response", None)
    internal_response = getattr(response, "internal_response", None)
    return getattr(exc, "headers", None) or getattr(response, "headers", None) or getattr(internal_response, "headers", None)


def _header_value(headers: object, *names: str) -> str | None:
    """Read a response header by name with case-insensitive fallback.

    Retry-after headers may arrive with different casing depending on SDK and service path.
    The retry delay helper uses this to check direct `get` lookups first and then scan iterable header items.
    Returning `None` for missing headers lets callers fall back to configured retry delay values.
    """
    if not headers:
        return None

    get_header = getattr(headers, "get", None)
    if get_header is not None:
        for name in names:
            value = get_header(name)
            if value is not None:
                return str(value)

    items = getattr(headers, "items", None)
    if items is not None:
        expected = {name.lower() for name in names}
        for key, value in items():
            if str(key).lower() in expected:
                return str(value)

    return None


def _retry_after_seconds(exc: exceptions.CosmosHttpResponseError, attempt_index: int) -> float:
    """Choose a quick retry delay for a retryable Cosmos write failure.

    Cosmos 429 responses often include `x-ms-retry-after-ms`, which should be honored when present.
    Other transient errors use the configured base delay with a small linear increase by attempt number.
    The delay is intentionally short because this benchmark is trying to keep pressure on the service while avoiding hot retry loops.
    """
    raw_retry_after_ms = _header_value(_headers_from_exception(exc), "x-ms-retry-after-ms", "retry-after-ms")
    if raw_retry_after_ms is not None:
        try:
            return max(float(raw_retry_after_ms), 0.0) / 1000
        except ValueError:
            pass

    raw_retry_after_sec = _header_value(_headers_from_exception(exc), "retry-after")
    if raw_retry_after_sec is not None:
        try:
            return max(float(raw_retry_after_sec), 0.0)
        except ValueError:
            pass

    return (INSERT_RETRY_DELAY_MS / 1000) * max(attempt_index, 1)


def _is_retryable_cosmos_error(exc: exceptions.CosmosHttpResponseError) -> bool:
    """Return whether a Cosmos write failure should be retried by the benchmark.

    The retry loop handles throttles and common transient service/network status codes.
    Non-transient failures such as duplicate item conflicts, validation errors, or vector policy mismatches fail fast.
    This keeps retries useful for throughput pressure without hiding configuration or data problems.
    """
    return getattr(exc, "status_code", None) in RETRYABLE_COSMOS_STATUS_CODES

def _safe_json_preview(value: object, max_chars: int = 2000) -> str:
    """Serialize a value to JSON and truncate long previews.

    Diagnostic output uses this when printing headers, documents, and other failure context.
    It keeps rich debug information available without flooding the terminal with full vectors or large payloads.
    The truncation suffix preserves enough context to know that data was shortened intentionally.
    """
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...<truncated {len(text) - max_chars} chars>"


def _preview_doc_value(value: object) -> object:
    """Create a compact preview for one document field value.

    Error samples call this for each field in the failed document.
    Lists and nested objects are summarized by type, size, and representative content instead of printed in full.
    This is especially important for vector documents, where embedding arrays can be large enough to obscure the real error.
    """
    if isinstance(value, list):
        preview = value[:5]
        return {"type": "list", "length": len(value), "preview": preview}
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(str(key) for key in value.keys())[:20]}
    if isinstance(value, str) and len(value) > 300:
        return f"{value[:300]}...<truncated {len(value) - 300} chars>"
    return value


def _doc_preview(doc: dict) -> dict:
    """Create a compact preview of a document for diagnostics.

    Cosmos error samples include this preview to help identify bad IDs, partition values, or schema mismatches.
    It applies field-level summarization consistently across all document fields.
    Keeping previews compact makes failure logs usable during high-throughput runs.
    """
    return {str(key): _preview_doc_value(value) for key, value in doc.items()}


def _headers_preview(headers: object) -> dict:
    """Filter response headers to Cosmos diagnostic values.

    Error printing uses this to retain useful Cosmos headers such as activity id, request charge, retry-after, and substatus.
    It avoids dumping unrelated HTTP header noise while still preserving support details needed for troubleshooting.
    The helper accepts generic header-like objects because SDK response wrappers are not always plain dictionaries.
    """
    if not headers:
        return {}
    items = getattr(headers, "items", None)
    if items is None:
        return {}
    interesting = (
        "x-ms-activity-id",
        "x-ms-request-charge",
        "x-ms-substatus",
        "x-ms-retry-after-ms",
        "x-ms-serviceversion",
        "content-location",
        "content-type",
    )
    preview = {}
    for key, value in items():
        lower_key = str(key).lower()
        if lower_key in interesting or lower_key.startswith("x-ms-"):
            preview[str(key)] = str(value)
    return preview


def _response_body_preview(response: object) -> str:
    """Extract a short text preview from a response object.

    Cosmos exceptions sometimes carry useful server error details in response body, content, or text attributes.
    The error sampler uses this helper to include those details when available.
    It keeps only the first portion of the body so repeated failures do not overwhelm terminal output.
    """
    if response is None:
        return ""
    for attr in ("text", "content", "body"):
        value = getattr(response, attr, None)
        if value:
            if callable(value):
                continue
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            return str(value)[:2000]
    return ""


def _print_cosmos_error_sample(exc: exceptions.CosmosHttpResponseError, doc: dict, metrics: dict) -> None:
    """Print a detailed sample for a Cosmos write failure.

    `insert_doc` calls this for the first configured number of Cosmos write failures per worker.
    It prints worker, status, partition, RU, headers, response body, and document preview to make failures actionable.
    Sampling keeps diagnostics available without turning large benchmark runs into error-log floods.
    """
    response = getattr(exc, "response", None)
    internal_response = getattr(response, "internal_response", None)
    headers = getattr(exc, "headers", None) or getattr(response, "headers", None) or getattr(internal_response, "headers", None)
    partition_value = {field: doc.get(field) for field in PARTITION_KEY_FIELDS}
    print("\n[cosmos error sample]", flush=True)
    print(f"worker={metrics.get('worker_index')}", flush=True)
    print(f"status={getattr(exc, 'status_code', None)}", flush=True)
    print(f"sub_status={getattr(exc, 'sub_status', None)}", flush=True)
    print(f"reason={getattr(exc, 'reason', None)!r}", flush=True)
    print(f"message={getattr(exc, 'message', None)!r}", flush=True)
    print(f"id={doc.get('id')!r}", flush=True)
    print(f"partition_key_fields={PARTITION_KEY_FIELDS!r}", flush=True)
    print(f"partition_key_value={partition_value!r}", flush=True)
    print(f"request_charge={_request_charge_from_headers(headers):.2f}", flush=True)
    print(f"headers={_safe_json_preview(_headers_preview(headers), max_chars=3000)}", flush=True)
    body = _response_body_preview(response) or _response_body_preview(internal_response)
    if body:
        print(f"response_body={body}", flush=True)
    print(f"doc_preview={_safe_json_preview(_doc_preview(doc), max_chars=4000)}", flush=True)
    print(f"exception={exc!r}", flush=True)

async def insert_doc(
    container,
    sem: asyncio.Semaphore,
    doc: dict,
    metrics: dict,
) -> tuple[float, float, list[tuple[float, float]]]:
    """Create one Cosmos item and return its request timing window.

    This is the hot path that issues `container.create_item` under the worker concurrency semaphore.
    It records success, error, throttle, request-charge, and throughput timing state for every attempted create.
    The returned attempt windows let `insert_bulk` compute service time from each `create_item` send to its response.
    """
    async with sem:
        start = time.perf_counter()
        start_epoch = time.time()
        finish = start
        finish_epoch = start_epoch
        attempt_windows = []
        _record_upload_started(metrics, start, start_epoch)
        request_charge_total = 0.0

        try:
            for attempt in range(MAX_INSERT_RETRIES + 1):
                attempt_request_charge = 0.0
                attempt_partition_key_range_id = None
                response_hook = None

                if CAPTURE_RU_CHARGES or PARTITION_KEY_RANGE_RPS_ENABLED:
                    def capture_response_headers(headers, _body) -> None:
                        """Capture selected values from one Cosmos response hook invocation.

                        The Azure Cosmos async SDK invokes this hook after receiving a create response.
                        The enclosing retry loop uses these per-attempt values for RU accounting and optional range diagnostics.
                        """
                        nonlocal attempt_request_charge, attempt_partition_key_range_id
                        if CAPTURE_RU_CHARGES:
                            attempt_request_charge = _request_charge_from_headers(headers)
                        if PARTITION_KEY_RANGE_RPS_ENABLED:
                            attempt_partition_key_range_id = _partition_key_range_id_from_headers(headers)

                    response_hook = capture_response_headers

                try:
                    attempt_start = time.perf_counter()
                    _record_create_item_attempt(metrics)
                    if response_hook is None:
                        await container.create_item(doc)
                    else:
                        await container.create_item(doc, response_hook=response_hook)
                    finish = time.perf_counter()
                    finish_epoch = time.time()
                    attempt_windows.append((attempt_start, finish))
                    _record_partition_key_range_request(metrics, attempt_partition_key_range_id)
                    if CAPTURE_RU_CHARGES:
                        request_charge_total += attempt_request_charge

                    metrics["success"] += 1
                    _record_request_charge(metrics, request_charge_total)
                    break

                except exceptions.CosmosHttpResponseError as exc:
                    finish = time.perf_counter()
                    finish_epoch = time.time()
                    attempt_windows.append((attempt_start, finish))
                    _record_create_item_failure(metrics)
                    if PARTITION_KEY_RANGE_RPS_ENABLED and not attempt_partition_key_range_id:
                        attempt_partition_key_range_id = _partition_key_range_id_from_headers(_headers_from_exception(exc))
                    if CAPTURE_RU_CHARGES:
                        request_charge_total += attempt_request_charge or _request_charge_from_exception(exc)
                    _record_partition_key_range_request(metrics, attempt_partition_key_range_id)

                    if getattr(exc, "status_code", None) == 429:
                        metrics["throttles_w_retry"] += 1

                    if attempt < MAX_INSERT_RETRIES and _is_retryable_cosmos_error(exc):
                        await asyncio.sleep(_retry_after_seconds(exc, attempt + 1))
                        continue

                    metrics["errors"] += 1
                    _record_request_charge(metrics, request_charge_total)

                    if metrics["cosmos_error_samples_logged"] < COSMOS_ERROR_SAMPLE_LIMIT:
                        metrics["cosmos_error_samples_logged"] += 1
                        _print_cosmos_error_sample(exc, doc, metrics)
                    break
        finally:
            _record_upload_finished(metrics, finish, finish_epoch)

        return start, finish, attempt_windows


def _unpack_doc_batch(doc_batch: object) -> list[dict]:
    if not isinstance(doc_batch, list):
        raise TypeError(f"Expected document batch list, got {type(doc_batch).__name__}")
    return doc_batch


async def insert_bulk(
    container,
    sem: asyncio.Semaphore,
    docs: list[dict],
    metrics: dict,
) -> None:
    """Schedule one local bulk of item creates and record bulk service time.

    The benchmark treats `BULK_SIZE` as a local operation grouping, not as a Cosmos bulk API request.
    This function schedules all document creates in the group and records service time from the first request issue to the last response or error.
    It also classifies the bulk as successful or errored so bulk throughput and failure rates can be reported separately from document counts.
    """
    if not docs:
        return

    metrics["bulks_started"] += 1
    metrics["bulk_docs_attempted"] += len(docs)
    errors_before = metrics["errors"]
    request_windows = await asyncio.gather(*(insert_doc(container, sem, doc, metrics) for doc in docs))
    last_response_finish = max(finish for _, finish, _ in request_windows)
    service_time_ms_samples = [
        (attempt_finish - attempt_start) * 1000
        for _, _, attempt_windows in request_windows
        for attempt_start, attempt_finish in attempt_windows
    ]
    metrics["bulks_completed"] += 1
    if metrics["errors"] > errors_before:
        metrics["bulk_errors"] += 1
    else:
        metrics["bulk_success"] += 1
    _record_bulk_sample(
        metrics,
        len(docs),
        service_time_ms_samples,
        last_response_finish,
    )


async def insert_doc_batches(
    container,
    sem: asyncio.Semaphore,
    doc_batches: AsyncIterable[object],
    metrics: dict,
) -> None:
    """Consume document batches while bounding pending bulk tasks.

    Workers use this scheduler for both generated and file-backed document sources.
    It creates asynchronous bulk tasks while limiting how many bulks can be pending at once through `MAX_PENDING_BULKS`.
    Bounding pending work prevents the producer side from queueing unbounded tasks and consuming unnecessary memory.
    """
    pending: set[asyncio.Task] = set()

    async def drain_completed() -> None:
        """Wait for at least one pending bulk task and settle completed tasks.

        This nested helper is used whenever the pending bulk-task set reaches its configured limit.
        Waiting for the first completed task keeps the pipeline moving while applying backpressure to batch scheduling.
        Gathering completed tasks also surfaces exceptions promptly instead of leaving them hidden in task objects.
        """
        nonlocal pending
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        await asyncio.gather(*done)

    async for doc_batch in doc_batches:
        docs = _unpack_doc_batch(doc_batch)
        pending.add(asyncio.create_task(insert_bulk(container, sem, docs, metrics)))
        if len(pending) >= MAX_PENDING_BULKS:
            await drain_completed()

    if pending:
        await asyncio.gather(*pending)

def _queue_message(metric_queue: mp.Queue, message_type: str, worker_index: int, data: dict | None = None, error: str | None = None) -> None:
    """Send a worker metric, result, or error message to the parent process.

    Multiprocess workers cannot update parent memory directly, so they communicate through this queue message format.
    The parent drain loop uses the message type to update live metrics, final results, or worker error state.
    Adding a timestamp on every message gives future diagnostics a consistent event marker.
    """
    message = {
        "type": message_type,
        "worker_index": worker_index,
        "timestamp": time.time(),
    }
    if data is not None:
        message["data"] = data
    if error is not None:
        message["error"] = error
    metric_queue.put(message)


async def live_reporter(
    metrics: dict,
    done: asyncio.Event,
    total_docs: int | None,
    client_count: int,
    worker_index: int,
    metric_queue: mp.Queue | None = None,
) -> None:
    """Emit live worker metrics locally or through the parent metric queue.

    Every worker starts this coroutine while it is issuing writes.
    In single-process mode it refreshes the terminal directly, while multiprocess workers send snapshots to the parent.
    The reporter runs until the worker signals completion so the final worker snapshot is emitted even after the last insert finishes.
    """
    last_len = 0
    while not done.is_set():
        _record_throughput_sample(metrics)
        if metric_queue is not None:
            _queue_message(metric_queue, "METRIC", worker_index, _metric_snapshot(metrics, total_docs, client_count, worker_index))
        else:
            line = _live_line(metrics, total_docs, client_count, worker_index)
            last_len = _print_live_line(line, last_len)
        try:
            await asyncio.wait_for(done.wait(), timeout=METRICS_SAMPLE_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass

    _record_throughput_sample(metrics, force=True)
    if metric_queue is not None:
        _queue_message(metric_queue, "METRIC", worker_index, _metric_snapshot(metrics, total_docs, client_count, worker_index))
    else:
        line = _live_line(metrics, total_docs, client_count, worker_index)
        _print_live_line(line, last_len, final=True)

def _worker_slices(total_docs: int, client_processes: int) -> list[tuple[int, int, int]]:
    """Split a fake-document workload across client processes.

    Fake mode needs each worker process to generate a distinct range of synthetic document values.
    This helper divides the total count as evenly as possible and returns worker index, start offset, and count tuples.
    Balanced slices keep workers from finishing at wildly different times during generated-data runs.
    """
    base = total_docs // client_processes
    remainder = total_docs % client_processes
    start = 0
    slices = []
    for index in range(client_processes):
        count = base + (1 if index < remainder else 0)
        slices.append((index, start, count))
        start += count
    return slices


@asynccontextmanager
async def _cosmos_client_context():
    """Open a Cosmos client using key auth when configured, otherwise Entra ID.

    Workers call this once per process before resolving the target database and container.
    A non-empty `COSMOS_KEY` value is passed directly to the Cosmos SDK as the account key.
    When the key is blank, the benchmark keeps the existing `DefaultAzureCredential` path.
    """
    if COSMOS_KEY:
        async with CosmosClient(ENDPOINT, credential=COSMOS_KEY) as client:
            yield client
        return

    async with DefaultAzureCredential() as credential:
        async with CosmosClient(ENDPOINT, credential=credential) as client:
            yield client


async def run_worker_with_batches(
    worker_index: int,
    total_docs: int | None,
    client_count: int,
    doc_batches: AsyncIterable[list[dict]],
    metric_queue: mp.Queue | None = None,
) -> dict:
    """Run a worker against an async stream of document batches.

    This shared worker implementation opens the Cosmos client, database, and container used by one client process.
    It starts live reporting, sends all provided batches through the insert scheduler, and returns the final result snapshot.
    Sharing this function keeps fake and file-input modes aligned in their write path and metrics behavior.
    """
    metrics = _new_metrics()
    metrics["worker_index"] = worker_index

    sem = asyncio.Semaphore(MAX_IN_FLIGHT)

    async with _cosmos_client_context() as client:
        db = client.get_database_client(DATABASE)
        container = db.get_container_client(CONTAINER)

        done = asyncio.Event()
        reporter = asyncio.create_task(
            live_reporter(metrics, done, total_docs, client_count, worker_index, metric_queue)
        )

        try:
            await insert_doc_batches(container, sem, doc_batches, metrics)
        finally:
            done.set()
            await reporter

        result = _result_snapshot(metrics, total_docs, client_count, worker_index)
        if metric_queue is not None:
            _queue_message(metric_queue, "RESULT", worker_index, result)
        else:
            _print_result(result)
        return result


async def run_worker(
    worker_index: int,
    total_docs: int,
    start_index: int,
    client_count: int,
    metric_queue: mp.Queue | None = None,
) -> dict:
    """Run a fake-document worker for a generated document range.

    Multiprocess fake mode calls this with the range assigned by `_worker_slices`.
    It builds a payload string once and exposes generated local bulks through the shared worker runner.
    This keeps synthetic-data generation cheap relative to the Cosmos write work being measured.
    """
    payload = "x" * PAYLOAD_BYTES
    return await run_worker_with_batches(
        worker_index,
        total_docs,
        client_count,
        _iter_generated_doc_bulks(start_index, start_index + total_docs, BULK_SIZE, payload),
        metric_queue,
    )


async def run_queue_worker(
    worker_index: int,
    client_count: int,
    work_queue: mp.Queue,
    metric_queue: mp.Queue | None = None,
) -> dict:
    """Run a file-input worker that consumes documents from a queue.

    File mode calls this in each worker process while a producer thread reads and enqueues source documents.
    The queue iterator converts blocking queue reads into the same async batch interface used by fake mode.
    This separation lets file loading and Cosmos writing overlap without counting producer timing as service time.
    """
    return await run_worker_with_batches(
        worker_index,
        None,
        client_count,
        _iter_queue_doc_bulks(work_queue, BULK_SIZE),
        metric_queue,
    )


def worker_main(worker_index: int, client_count: int, start_index: int, total_docs: int, metric_queue: mp.Queue) -> None:
    """Multiprocessing entrypoint for a fake-document worker.

    `run_parent` uses this as the target function when spawning fake-data worker processes.
    It runs the async worker and converts any exception into an error queue message before re-raising.
    Reporting before re-raising lets the parent include useful traceback text in benchmark output.
    """
    try:
        asyncio.run(run_worker(worker_index, total_docs, start_index, client_count, metric_queue))
    except Exception as exc:
        _queue_message(
            metric_queue,
            "ERROR",
            worker_index,
            error=_format_worker_exception(exc),
        )
        raise


def queue_worker_main(worker_index: int, client_count: int, work_queue: mp.Queue, metric_queue: mp.Queue) -> None:
    """Multiprocessing entrypoint for a queued file-input worker.

    `run_json_parent` uses this as the target function for workers that consume file-loaded documents.
    It bridges the process target API into the async queue worker and reports any exception back to the parent queue.
    This keeps parent orchestration aware of worker crashes instead of relying only on exit codes.
    """
    try:
        asyncio.run(run_queue_worker(worker_index, client_count, work_queue, metric_queue))
    except Exception as exc:
        _queue_message(
            metric_queue,
            "ERROR",
            worker_index,
            error=_format_worker_exception(exc),
        )
        raise

def _drain_metric_queue(metric_queue: mp.Queue, latest_metrics: dict[int, dict], results: dict[int, dict], errors: dict[int, str]) -> int:
    """Drain worker messages into parent-side metrics, results, and errors.

    Parent orchestration calls this repeatedly while worker processes are alive and once more after they exit.
    It updates the latest live snapshot per worker, stores final results, and collects tracebacks or unexpected message types.
    Returning the drain count is useful for debugging or future loop tuning even though the current callers do not need it.
    """
    drained = 0
    while True:
        try:
            message = metric_queue.get_nowait()
        except queue.Empty:
            return drained

        drained += 1
        worker_index = message["worker_index"]
        message_type = message["type"]
        if message_type == "METRIC":
            latest_metrics[worker_index] = message["data"]
        elif message_type == "RESULT":
            result = message["data"]
            latest_metrics[worker_index] = result
            results[worker_index] = result
        elif message_type == "ERROR":
            errors[worker_index] = message.get("error", "unknown worker error")
        else:
            errors[worker_index] = f"unknown worker message type: {message_type!r}"

async def run_parent() -> None:
    """Run and aggregate a multi-process fake-document benchmark.

    This is the parent path for generated data when more than one client process is configured.
    It spawns workers, refreshes aggregate live metrics from their queue messages, joins them, and prints final aggregate results.
    The parent owns process lifecycle so failures can terminate cleanly and report which workers failed.
    """
    latest_metrics: dict[int, dict] = {}
    results: dict[int, dict] = {}
    errors: dict[int, str] = {}
    aggregate_throughput_samples: list[float] = []
    metric_queue = mp.Queue()
    processes: dict[int, mp.Process] = {}
    total_started_at = time.perf_counter()

    for index, start_index, doc_count in _worker_slices(EFFECTIVE_TOTAL_DOCS, CLIENT_PROCESSES):
        process = mp.Process(
            target=worker_main,
            args=(index, CLIENT_PROCESSES, start_index, doc_count, metric_queue),
            name=f"test-new-client-{index}",
        )
        process.start()
        processes[index] = process

    last_len = 0
    try:
        while True:
            _drain_metric_queue(metric_queue, latest_metrics, results, errors)
            line = _aggregate_line(latest_metrics, EFFECTIVE_TOTAL_DOCS, CLIENT_PROCESSES, aggregate_throughput_samples)
            last_len = _print_live_line(line, last_len)

            if not any(process.is_alive() for process in processes.values()):
                break

            await asyncio.sleep(METRICS_SAMPLE_INTERVAL_SEC)
    except BaseException:
        for process in processes.values():
            if process.is_alive():
                process.terminate()
        raise

    for process in processes.values():
        process.join()

    _drain_metric_queue(metric_queue, latest_metrics, results, errors)
    line = _aggregate_line(latest_metrics, EFFECTIVE_TOTAL_DOCS, CLIENT_PROCESSES, aggregate_throughput_samples)
    _print_live_line(line, last_len, final=True)

    return_codes = [process.exitcode if process.exitcode is not None else -1 for process in processes.values()]

    total_elapsed_time_sec = max(time.perf_counter() - total_started_at, 0.000001)
    _print_parent_result(
        results,
        {"max_total_docs": MAX_TOTAL_DOCS or ""},
        total_elapsed_time_sec=total_elapsed_time_sec,
    )

    for worker_index, error in sorted(errors.items()):
        print(f"worker_error[{worker_index}]={error}")

    failed = [code for code in return_codes if code != 0]
    if failed or errors:
        raise SystemExit(1)


async def run_json_parent() -> None:
    """Run and aggregate a file-input benchmark with producer and workers.

    This is the parent path when `DATA_TYPE=file` is configured.
    It starts worker processes plus a producer thread that streams JSON records into a bounded multiprocessing queue.
    The function reports both aggregate write metrics and producer throughput so users can tell whether disk loading or Cosmos writes are the bottleneck.
    """
    latest_metrics: dict[int, dict] = {}
    results: dict[int, dict] = {}
    errors: dict[int, str] = {}
    aggregate_throughput_samples: list[float] = []
    metric_queue = mp.Queue()
    queue_max_docs = CLIENT_PROCESSES * BULK_SIZE * DOC_QUEUE_MULTIPLIER
    queue_maxsize = max(1, (queue_max_docs + READ_BATCH_SIZE - 1) // READ_BATCH_SIZE)
    work_queue = mp.Queue(maxsize=queue_maxsize)
    processes: dict[int, mp.Process] = {}
    producer_status: dict = {}
    total_started_at = time.perf_counter()

    for index in range(CLIENT_PROCESSES):
        process = mp.Process(
            target=queue_worker_main,
            args=(index, CLIENT_PROCESSES, work_queue, metric_queue),
            name=f"test-new-client-{index}",
        )
        process.start()
        processes[index] = process

    producer_thread = threading.Thread(
        target=_stream_json_docs_thread,
        args=(DOC_JSON_PATH, work_queue, CLIENT_PROCESSES, MAX_TOTAL_DOCS, producer_status),
        name="json-producer",
        daemon=True,
    )
    producer_thread.start()

    last_len = 0
    try:
        while producer_thread.is_alive() or any(process.is_alive() for process in processes.values()):
            _drain_metric_queue(metric_queue, latest_metrics, results, errors)
            line = _aggregate_line(
                latest_metrics,
                MAX_TOTAL_DOCS or (producer_status.get("docs_read") if producer_status.get("finished") else None),
                CLIENT_PROCESSES,
                aggregate_throughput_samples,
            )
            last_len = _print_live_line(line, last_len)

            if producer_status.get("error"):
                errors[-1] = producer_status["error"]
                break

            await asyncio.sleep(METRICS_SAMPLE_INTERVAL_SEC)
    except BaseException:
        for process in processes.values():
            if process.is_alive():
                process.terminate()
        raise

    producer_thread.join()

    for process in processes.values():
        process.join()

    _drain_metric_queue(metric_queue, latest_metrics, results, errors)
    line = _aggregate_line(
        latest_metrics,
        producer_status.get("docs_read"),
        CLIENT_PROCESSES,
        aggregate_throughput_samples,
    )
    _print_live_line(line, last_len, final=True)

    return_codes = [process.exitcode if process.exitcode is not None else -1 for process in processes.values()]
    total_elapsed_time_sec = max(time.perf_counter() - total_started_at, 0.000001)
    _print_parent_result(
        results,
        {
            "max_total_docs": MAX_TOTAL_DOCS or "",
        },
        total_elapsed_time_sec=total_elapsed_time_sec,
    )

    for worker_index, error in sorted(errors.items()):
        print(f"worker_error[{worker_index}]={error}")

    failed = [code for code in return_codes if code != 0]
    if failed or errors:
        raise SystemExit(1)
