"""Configuration and output path helpers for the Cosmos DB write benchmark."""

from __future__ import annotations

import csv
import math
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

def _require_env(name: str) -> str:
    """Return a required environment variable or raise a clear error.

    This is used while importing configuration for values that the benchmark cannot infer, such as the Cosmos endpoint, database, and container.
    Failing early keeps the benchmark from starting worker processes when the target resource is not configured.
    """
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    """Read an integer environment variable with default and minimum validation.

    This helper is used for workload knobs such as client count, bulk size, and concurrency limits.
    It accepts underscores in numeric strings so large values remain readable in `.env` files.
    Centralizing validation gives every numeric setting consistent error messages and bounds checking.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw.replace("_", ""))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _optional_int_env(name: str, minimum: int = 1) -> int | None:
    """Read an optional integer environment variable, returning None when blank.

    This is used for settings such as `MAX_TOTAL_DOCS`, where an empty value has semantic meaning.
    Returning `None` lets downstream code distinguish no cap from a configured numeric cap.
    The same minimum validation is applied when a value is present so invalid caps fail before a run starts.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw.replace("_", ""))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    """Read a float environment variable with default and minimum validation.

    This is used for interval-style configuration like live metrics refresh cadence.
    It keeps timing controls flexible while still rejecting negative or otherwise invalid values.
    Having one parser also keeps type conversion behavior predictable across the configuration module.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    """Read a boolean environment variable from common true and false strings.

    This is used for feature toggles such as CSV output enablement.
    It accepts common shell-friendly values like `true`, `yes`, `1`, `false`, `no`, and `0`.
    Explicit validation avoids silently treating misspelled values as truthy or falsey configuration.
    """
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value, got {raw!r}")


def _int_env_alias(primary: str, fallback: str, default: int, minimum: int = 1) -> int:
    """Read an integer from a primary environment variable or fallback alias.

    This supports newer setting names while keeping older names usable for existing scripts and `.env` files.
    The primary value wins when both are present, which makes migration behavior explicit.
    It is used for aliases such as `MAX_IN_FLIGHT`/`MAX_CONCURRENCY` and service-time sample interval compatibility.
    """
    if os.getenv(primary, "").strip():
        return _int_env(primary, default, minimum)
    return _int_env(fallback, default, minimum)


def _int_env_alias_or_auto(primary: str, fallback: str, default: int, auto_value: int) -> int:
    """Read an integer alias pair, using an auto value when the configured value is below one.

    This supports concurrency knobs where `0` or negative values mean "derive the value from bulk size" instead of failing validation.
    The primary value still wins when both names are present, matching `_int_env_alias` behavior.
    """
    env_name = primary if os.getenv(primary, "").strip() else fallback
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw.replace("_", ""))
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer, got {raw!r}") from exc
    return auto_value if value < 1 else value


def _run_started_at() -> datetime:
    """Return the stable timestamp for the current benchmark run.

    This value is created once and stored in the process environment so imports and worker code agree on the run timestamp.
    The timestamp is used when naming metrics CSV files under the results directory.
    Keeping it stable prevents multiple output filenames from being created during one benchmark invocation.
    """
    env_name = "BENCHMARK_RUN_STARTED_AT"
    raw = os.getenv(env_name, "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{env_name} must be an ISO datetime value, got {raw!r}") from exc

    value = datetime.now().replace(microsecond=0)
    os.environ[env_name] = value.isoformat()
    return value


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH, override=False)

ENDPOINT = _require_env("COSMOS_ENDPOINT")
DATABASE = _require_env("COSMOS_DATABASE_NAME")
CONTAINER = _require_env("COSMOS_CONTAINER_NAME")
COSMOS_KEY = os.getenv("COSMOS_KEY", "").strip()

TOTAL_DOCS = _int_env("TOTAL_DOCS", 1_000_000)
MAX_TOTAL_DOCS = _optional_int_env("MAX_TOTAL_DOCS")
CLIENT_PROCESSES = (
    _int_env("NUM_CLIENTS", 1)
    if os.getenv("NUM_CLIENTS", "").strip()
    else _int_env_alias("CLIENTS", "CLIENT_PROCESSES", 1)
)
BULK_SIZE = _int_env("BULK_SIZE", 100)
MAX_IN_FLIGHT_AUTO = math.ceil(BULK_SIZE * 1.5)
MAX_IN_FLIGHT = _int_env_alias_or_auto("MAX_IN_FLIGHT", "MAX_CONCURRENCY", max(BULK_SIZE * 2, 40), MAX_IN_FLIGHT_AUTO)
MAX_PENDING_BULKS = _int_env("MAX_PENDING_BULKS", max(1, min(8, ((MAX_IN_FLIGHT + BULK_SIZE - 1) // BULK_SIZE) * 2)))
MAX_INSERT_RETRIES = _int_env("MAX_INSERT_RETRIES", 5, minimum=0)
INSERT_RETRY_DELAY_MS = _int_env("INSERT_RETRY_DELAY_MS", 50, minimum=0)
CAPTURE_RU_CHARGES = _bool_env("CAPTURE_RU_CHARGES", True)
PARTITION_KEY_RANGE_RPS_ENABLED = _bool_env("PARTITION_KEY_RANGE_RPS_ENABLED", False)
PAYLOAD_BYTES = _int_env("PAYLOAD_BYTES", 5000, minimum=0)
FAKE_DATA_VECTOR_DIM = _int_env("FAKE_DATA_VECTOR_DIM", 1536, minimum=0)
SESSION_ID_ENABLED = _bool_env("SESSION_ID_ENABLED", False)
SESSION_ID_MIN_DOCS = _int_env("SESSION_ID_MIN_DOCS", 10, minimum=1)
SESSION_ID_MAX_DOCS = _int_env("SESSION_ID_MAX_DOCS", 1000, minimum=1)
LIVE_INTERVAL_SEC = _float_env("LIVE_INTERVAL_SEC", 1.0, minimum=0.1)
METRICS_SAMPLE_INTERVAL_SEC = _float_env("METRICS_SAMPLE_INTERVAL_SEC", LIVE_INTERVAL_SEC, minimum=0.1)
METRICS_TIMING_SAMPLE_INTERVAL = _int_env("METRICS_TIMING_SAMPLE_INTERVAL", 1)
METRICS_WARMUP_SEC = _float_env("METRICS_WARMUP_SEC", 0.0)
DATA_TYPE = os.getenv("DATA_TYPE", "fake").strip().lower()
DOC_JSON_PATH = os.getenv("DOC_JSON_PATH", os.getenv("DATA_FILE_PATH", "./data/open_ai_corpus-initial-indexing.json")).strip()
DOC_JSON_FORMAT = os.getenv("DOC_JSON_FORMAT", "jsonl").strip().lower()
READ_BATCH_SIZE = _int_env("READ_BATCH_SIZE", BULK_SIZE)
DOC_QUEUE_MULTIPLIER = _int_env("DOC_QUEUE_MULTIPLIER", 4)
PARTITION_KEY_FIELD = os.getenv("PARTITION_KEY_FIELD", "").strip()
_partition_key_fields_value = os.getenv("PARTITION_KEY_FIELDS", "").strip()
PARTITION_KEY_FIELDS = tuple(
    field.strip().lstrip("/") for field in _partition_key_fields_value.split(",") if field.strip().lstrip("/")
) or ((PARTITION_KEY_FIELD,) if PARTITION_KEY_FIELD else ())
DOCUMENT_ID_FALLBACK_FIELD = os.getenv(
    "DOCUMENT_ID_FALLBACK_FIELD", PARTITION_KEY_FIELD or "docid"
).strip().lstrip("/")
COSMOS_ERROR_SAMPLE_LIMIT = _int_env("COSMOS_ERROR_SAMPLE_LIMIT", 3, minimum=0)
EFFECTIVE_TOTAL_DOCS = min(TOTAL_DOCS, MAX_TOTAL_DOCS) if MAX_TOTAL_DOCS is not None else TOTAL_DOCS
CSV_OUTPUT_ENABLED = _bool_env("CSV_OUTPUT_ENABLED", True)
TEST_RESULTS_ROOT = Path(os.getenv("TEST_RESULTS_ROOT", "results").strip() or "results")
RUN_STARTED_AT = _run_started_at()
CSV_EXCLUDED_FIELDS = {
    "current_docs_per_sec",
    "current_docs_per_sec_per_client",
    "timing_sample_count",
    "create_item_failure_attempts_total",
    "create_item_attempts_total",
    "throughput_sample_count",
}

if DATA_TYPE not in {"fake", "file"}:
    raise ValueError("DATA_TYPE must be one of: fake, file")

if SESSION_ID_MAX_DOCS < SESSION_ID_MIN_DOCS:
    raise ValueError("SESSION_ID_MAX_DOCS must be greater than or equal to SESSION_ID_MIN_DOCS")

if DATA_TYPE == "file" and not DOC_JSON_PATH:
    raise ValueError("DOC_JSON_PATH is required when DATA_TYPE=file")

if DATA_TYPE == "file" and not PARTITION_KEY_FIELDS:
    raise ValueError("PARTITION_KEY_FIELDS or PARTITION_KEY_FIELD is required when DATA_TYPE=file")

if not DOCUMENT_ID_FALLBACK_FIELD:
    raise ValueError("DOCUMENT_ID_FALLBACK_FIELD must not be empty")

if DOC_JSON_FORMAT not in {"array", "jsonl", "multiple_values"}:
    raise ValueError("DOC_JSON_FORMAT must be one of: array, jsonl, multiple_values")


def _total_docs_label() -> str:
    """Build the document-count label used in metrics CSV filenames.

    This helper converts the configured upload cap or fake-document total into a compact string for result file names.
    In file mode, a missing cap becomes `all` so full-corpus runs are easy to identify later.
    Keeping this logic separate avoids duplicating filename rules in the CSV path builder.
    """
    if MAX_TOTAL_DOCS is not None:
        return str(MAX_TOTAL_DOCS)
    if DATA_TYPE == "fake":
        return str(EFFECTIVE_TOTAL_DOCS)
    return "all"


def _metrics_csv_path() -> Path | None:
    """Return the metrics CSV path, or None when CSV output is disabled.

    This constructs the final output file name from run timestamp, client count, bulk size, and document-count label.
    It is used by the metrics writer to decide whether and where to persist final results.
    Returning `None` keeps CSV disabling simple without special cases in the print path.
    """
    if not CSV_OUTPUT_ENABLED:
        return None

    csv_name = (
        f"{RUN_STARTED_AT:%m%d%y-%H%M%S}"
        f"-clients-{CLIENT_PROCESSES}"
        f"-bulk-{BULK_SIZE}"
        f"-maxdocs-{_total_docs_label()}"
        ".csv"
    )
    return TEST_RESULTS_ROOT / csv_name


def _write_metrics_csv(metrics: dict) -> None:
    """Append a metrics row to the configured CSV output file.

    This is called after final metrics are printed so every completed run can be compared later from `results/`.
    It creates the output directory as needed and writes a header only when starting a new CSV file.
    The function keeps persistence concerns out of the worker and aggregation code.
    """
    csv_path = _metrics_csv_path()
    if csv_path is None:
        return

    csv_metrics = {name: value for name, value in metrics.items() if name not in CSV_EXCLUDED_FIELDS}
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(csv_metrics.keys())
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(csv_metrics)
    print(f"metrics_csv_path={csv_path}")
