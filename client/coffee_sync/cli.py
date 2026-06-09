"""Command-line entrypoint: `coffee-sync <file.csv>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config
from .client import PaymentsClient
from .csv_loader import CsvFormatError
from .sync import run_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coffee-sync",
        description="Reliably propagate Coffee Place notebook payments (CSV) to the Central System.",
    )
    parser.add_argument("csv", help="Path to the notebook CSV file")
    parser.add_argument(
        "--base-url",
        default=config.DEFAULT_BASE_URL,
        help=f"Central System base URL (default: {config.DEFAULT_BASE_URL}; "
        "points at Toxiproxy so faults are exercised)",
    )
    parser.add_argument(
        "--store-id",
        default=config.DEFAULT_STORE_ID,
        help=f"Store-Id for rows lacking a store_id column (default: {config.DEFAULT_STORE_ID})",
    )
    parser.add_argument(
        "--dead-letter",
        default="out/dead-letter.csv",
        help="Where to write rows that permanently failed (default: out/dead-letter.csv)",
    )
    parser.add_argument(
        "--timeout", type=float, default=config.DEFAULT_TIMEOUT_SECONDS, help="Per-request timeout (s)"
    )
    parser.add_argument(
        "--max-retries", type=int, default=config.DEFAULT_MAX_RETRIES, help="Max attempts per row"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (DEBUG) logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"error: CSV file not found: {csv_path}", file=sys.stderr)
        return 2

    print(f"Syncing {csv_path} -> {args.base_url} (store-id default: {args.store_id})")
    try:
        with PaymentsClient(
            base_url=args.base_url, timeout=args.timeout, max_retries=args.max_retries
        ) as client:
            report = run_sync(csv_path, client=client, default_store_id=args.store_id)
    except CsvFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for line in report.summary_lines():
        print(line)

    if report.failed:
        written = report.write_dead_letter(args.dead_letter)
        print(f"  ↳ wrote {written} failed row(s) to {args.dead_letter}")

    # Non-zero exit if anything permanently failed, so CI / scripts can detect it.
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
