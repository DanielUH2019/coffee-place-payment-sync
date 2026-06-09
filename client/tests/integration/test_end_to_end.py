"""Live end-to-end tests against the running Compose stack.

Run with: `make test-integration` (or `uv run pytest -m integration`).
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from coffee_sync.client import PaymentsClient
from coffee_sync.sync import run_sync

pytestmark = pytest.mark.integration

HEADER = "store_id,coffee_type,price,currency,loyalty_card_id\n"
VALID_ROWS = [
    "LATTE,3.50,EUR,{card}-1",
    "ESPRESSO,2.00,EUR,{card}-2",
    "CAPPUCCINO,3.20,EUR,{card}-3",
]


def write_batch(tmp_path, store_id):
    body = HEADER + "".join(
        f"{store_id},{row.format(card=store_id)}\n" for row in VALID_ROWS
    )
    path = tmp_path / "batch.csv"
    path.write_text(body, encoding="utf-8")
    return path


def fresh_store() -> str:
    return f"itest-{uuid.uuid4().hex[:12]}"


def test_happy_path_creates_all(tmp_path, base_url, direct_url, reset_toxics):
    store = fresh_store()
    csv = write_batch(tmp_path, store)

    with PaymentsClient(base_url=base_url) as c:
        report = run_sync(csv, client=c, default_store_id=store)

    assert len(report.created) == len(VALID_ROWS)
    assert not report.failed

    # Read back directly (bypassing the proxy) to confirm persistence.
    with PaymentsClient(base_url=direct_url) as reader:
        stored = reader.list_payments(store)
    assert len(stored) == len(VALID_ROWS)


def test_rerun_is_idempotent(tmp_path, base_url, direct_url, reset_toxics):
    store = fresh_store()
    csv = write_batch(tmp_path, store)

    with PaymentsClient(base_url=base_url) as c:
        first = run_sync(csv, client=c, default_store_id=store)
        second = run_sync(csv, client=c, default_store_id=store)

    assert len(first.created) == len(VALID_ROWS)
    # Second run must be all replays (200), no new creates.
    assert len(second.replayed) == len(VALID_ROWS)
    assert not second.created

    with PaymentsClient(base_url=direct_url) as reader:
        stored = reader.list_payments(store)
    assert len(stored) == len(VALID_ROWS)  # no duplicates


def test_delivers_under_injected_latency(
    tmp_path, base_url, direct_url, reset_toxics, add_latency_toxic
):
    store = fresh_store()
    csv = write_batch(tmp_path, store)

    # Slow but recoverable: 800ms+200ms jitter, well under the client's 10s timeout.
    add_latency_toxic(latency_ms=800, jitter_ms=200)

    with PaymentsClient(base_url=base_url) as c:
        report = run_sync(csv, client=c, default_store_id=store)

    assert len(report.created) == len(VALID_ROWS)
    assert not report.failed

    with PaymentsClient(base_url=direct_url) as reader:
        stored = reader.list_payments(store)
    assert len(stored) == len(VALID_ROWS)


def test_ambiguous_timeout_yields_exactly_once(
    tmp_path, base_url, direct_url, reset_toxics, add_timeout_toxic
):
    """The hardest distributed-systems case: an *ambiguous* failure.

    The timeout toxic blocks the response while the request still reaches the app, so
    each write actually lands server-side but the client never hears back. The client
    must NOT hang — it retries within a bounded budget then dead-letters the row. The
    payoff: because every attempt carries the same deterministic Idempotency-Key, the
    retries do NOT create duplicates. The server ends up with exactly one payment per
    row (exactly-once), and once the fault clears a re-run simply replays them (200)."""
    store = fresh_store()
    csv = write_batch(tmp_path, store)

    add_timeout_toxic(timeout_ms=0)  # block the response path

    # Tight timeout + a few retries so the retry path is exercised quickly.
    with PaymentsClient(
        base_url=base_url, timeout=0.5, max_retries=3, backoff_initial=0.02, backoff_max=0.1
    ) as c:
        report = run_sync(csv, client=c, default_store_id=store)

    # Client side: every row failed (no confirmation) and nothing hung.
    assert len(report.failed) == len(VALID_ROWS)
    assert all(o.attempts >= 1 for o in report.failed)

    # Server side: despite multiple retried attempts per row reaching the app, the
    # deterministic idempotency key collapses them to exactly one payment per row.
    clear_toxics()
    with PaymentsClient(base_url=direct_url) as reader:
        assert len(reader.list_payments(store)) == len(VALID_ROWS)  # no duplicates

    # Recovery: re-running now confirms delivery as idempotent replays, not new creates.
    with PaymentsClient(base_url=base_url) as c:
        recovered = run_sync(csv, client=c, default_store_id=store)
    assert len(recovered.replayed) == len(VALID_ROWS)
    assert not recovered.created
    with PaymentsClient(base_url=direct_url) as reader:
        assert len(reader.list_payments(store)) == len(VALID_ROWS)  # still exactly-once


def clear_toxics():
    admin = os.environ.get("TOXIPROXY_ADMIN", "http://localhost:8474")
    proxy = "spring-boot-app"
    resp = httpx.get(f"{admin}/proxies/{proxy}/toxics", timeout=3.0)
    for toxic in resp.json():
        httpx.delete(f"{admin}/proxies/{proxy}/toxics/{toxic['name']}", timeout=3.0)


def test_invalid_rows_never_persist(tmp_path, base_url, direct_url, reset_toxics):
    store = fresh_store()
    body = (
        HEADER
        + f"{store},LATTE,3.50,EUR,{store}-ok\n"
        + f"{store},UNICORN_FRAPPE,5.00,EUR,{store}-bad\n"  # invalid coffee type
        + f"{store},LATTE,-1.00,EUR,{store}-bad2\n"  # invalid price
    )
    csv = tmp_path / "mixed.csv"
    csv.write_text(body, encoding="utf-8")

    with PaymentsClient(base_url=base_url) as c:
        report = run_sync(csv, client=c, default_store_id=store)

    assert len(report.created) == 1
    assert len(report.failed) == 2

    with PaymentsClient(base_url=direct_url) as reader:
        stored = reader.list_payments(store)
    assert len(stored) == 1  # only the valid row landed
