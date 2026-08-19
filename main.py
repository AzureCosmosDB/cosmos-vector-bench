"""Root CLI wrapper for the Cosmos DB write benchmark."""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_PATH = PROJECT_ROOT / "src"


def _positive_int(value: str) -> int:
    try:
        parsed = int(value.replace("_", "").replace(",", ""))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"{value!r} must be >= 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Cosmos DB write benchmark.")
    parser.add_argument(
        "--num-clients",
        "--num_clients",
        type=_positive_int,
        dest="num_clients",
        help="Override NUM_CLIENTS from .env.",
    )
    parser.add_argument(
        "--bulk-size",
        "--bulk_size",
        type=_positive_int,
        dest="bulk_size",
        help="Override BULK_SIZE from .env.",
    )
    parser.add_argument(
        "--total-docs",
        "--total_docs",
        type=_positive_int,
        dest="total_docs",
        help="Override TOTAL_DOCS and MAX_TOTAL_DOCS from .env.",
    )
    parser.add_argument(
        "--data-path",
        "--data_path",
        dest="data_path",
        help="Override DOC_JSON_PATH from .env and run with DATA_TYPE=file.",
    )
    parser.add_argument(
        "--container-name",
        "--container_name",
        dest="container_name",
        help="Override COSMOS_CONTAINER_NAME from .env.",
    )
    parser.add_argument(
        "--partition-key-mode",
        "--partition_key_mode",
        choices=("hpk", "docid", "sessionid"),
        dest="partition_key_mode",
        help=(
            "Select partition keys for this run: hpk (sessionid,docid), "
            "docid, or sessionid."
        ),
    )
    return parser.parse_args(argv)


def apply_overrides(args: argparse.Namespace) -> None:
    if args.num_clients is not None:
        value = str(args.num_clients)
        os.environ["NUM_CLIENTS"] = value
        os.environ["CLIENTS"] = value
        os.environ["CLIENT_PROCESSES"] = value

    if args.bulk_size is not None:
        value = str(args.bulk_size)
        os.environ["BULK_SIZE"] = value

    if args.total_docs is not None:
        value = str(args.total_docs)
        os.environ["TOTAL_DOCS"] = value
        os.environ["MAX_TOTAL_DOCS"] = value

    if args.data_path:
        os.environ["DATA_TYPE"] = "file"
        os.environ["DOC_JSON_PATH"] = args.data_path

    if args.container_name:
        os.environ["COSMOS_CONTAINER_NAME"] = args.container_name

    if args.partition_key_mode:
        partition_key_fields = {
            "hpk": "sessionid,docid",
            "docid": "docid",
            "sessionid": "sessionid",
        }[args.partition_key_mode]
        os.environ["SESSION_ID_ENABLED"] = str(
            args.partition_key_mode != "docid"
        ).lower()
        os.environ["PARTITION_KEY_FIELDS"] = partition_key_fields
        os.environ["DOCUMENT_ID_FALLBACK_FIELD"] = "docid"


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    apply_overrides(args)

    src_path = str(SRC_PATH)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from benchmark import main as benchmark_main

    asyncio.run(benchmark_main())


if __name__ == "__main__":
    mp.freeze_support()
    main()
