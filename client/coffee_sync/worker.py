"""Background worker that drains pending payments from sharded Postgres."""

from __future__ import annotations

import argparse
import logging
import time

import httpx

from . import config
from .client import PaymentsClient, PermanentError, TransientError
from .sharding import ShardRouter, parse_shard_dsns
from .storage import ClaimedPayment, PostgresStorage

log = logging.getLogger("coffee_sync.worker")


def build_storage() -> PostgresStorage:
    return PostgresStorage(ShardRouter(parse_shard_dsns(config.DEFAULT_DB_SHARDS)))


def process_once(*, storage: PostgresStorage, client: PaymentsClient, batch_size: int) -> int:
    claimed = storage.claim_pending(batch_size=batch_size)
    for item in claimed:
        _process_item(storage, client, item)
    return len(claimed)


def run_worker(
    *,
    storage: PostgresStorage,
    client: PaymentsClient,
    poll_seconds: float,
    batch_size: int,
) -> None:
    storage.init_schema()
    log.info("Async worker started with %d shard(s)", storage.router.shard_count)
    while True:
        processed = process_once(storage=storage, client=client, batch_size=batch_size)
        if processed == 0:
            time.sleep(poll_seconds)


def _process_item(storage: PostgresStorage, client: PaymentsClient, item: ClaimedPayment) -> None:
    try:
        result = client.send_payment(item.payment)
    except PermanentError as exc:
        log.warning("Request %s row %d failed permanently: %s", item.request_id, item.payment.row_number, exc)
        storage.mark_failed(item, error=str(exc), attempts=item.attempts)
    except (httpx.HTTPError, TransientError) as exc:
        log.warning("Request %s row %d failed after retries: %s", item.request_id, item.payment.row_number, exc)
        storage.mark_failed(item, error=f"transient error: {exc}", attempts=item.attempts)
    except Exception as exc:  # noqa: BLE001 - one broken row must not stop the worker
        log.exception("Request %s row %d failed unexpectedly", item.request_id, item.payment.row_number)
        storage.mark_failed(item, error=str(exc), attempts=item.attempts)
    else:
        storage.mark_succeeded(item, remote_payment_id=result.payment_id, attempts=result.attempts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coffee-sync-worker")
    parser.add_argument("--base-url", default=config.DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=config.DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-retries", type=int, default=config.DEFAULT_MAX_RETRIES)
    parser.add_argument("--poll-seconds", type=float, default=config.DEFAULT_WORKER_POLL_SECONDS)
    parser.add_argument("--batch-size", type=int, default=config.DEFAULT_WORKER_BATCH_SIZE)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    storage = build_storage()
    with PaymentsClient(
        base_url=args.base_url,
        timeout=args.timeout,
        max_retries=args.max_retries,
    ) as client:
        run_worker(
            storage=storage,
            client=client,
            poll_seconds=args.poll_seconds,
            batch_size=args.batch_size,
        )


if __name__ == "__main__":
    main()
