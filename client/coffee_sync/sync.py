"""Orchestrates a full notebook sync: load -> validate -> send -> report."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from .client import PaymentsClient, PermanentError
from .csv_loader import load_payments
from .reporting import RowOutcome, RunReport
from .validation import validate

log = logging.getLogger("coffee_sync")


def run_sync(
    csv_path: str | Path,
    *,
    client: PaymentsClient,
    default_store_id: str,
) -> RunReport:
    """Send every row of `csv_path` through `client`, returning a RunReport.

    Validation failures and permanent (4xx) server rejections are recorded as failed
    rows (dead-lettered) rather than aborting the whole batch. Transient failures are
    retried inside the client; if they exhaust retries the row is recorded as failed.
    """
    payments = load_payments(csv_path, default_store_id=default_store_id)
    report = RunReport()
    log.info("Loaded %d payment(s) from %s", len(payments), csv_path)

    for payment in payments:
        errors = validate(payment)
        if errors:
            detail = "; ".join(errors)
            log.warning("Row %d invalid, skipping network call: %s", payment.row_number, detail)
            report.record(RowOutcome(payment=payment, status="failed", detail=detail))
            continue

        try:
            result = client.send_payment(payment)
        except PermanentError as exc:
            log.warning("Row %d rejected by server (%d): %s", payment.row_number, exc.status_code, exc.detail)
            report.record(RowOutcome(payment=payment, status="failed", detail=str(exc)))
        except httpx.HTTPError as exc:
            log.error("Row %d failed after retries: %s", payment.row_number, exc)
            report.record(RowOutcome(payment=payment, status="failed", detail=f"transport error: {exc}"))
        except Exception as exc:  # noqa: BLE001 - last-resort guard so one bad row can't sink the batch
            log.error("Row %d failed after retries: %s", payment.row_number, exc)
            report.record(RowOutcome(payment=payment, status="failed", detail=str(exc)))
        else:
            status = "created" if result.created else "replayed"
            log.info(
                "Row %d %s (HTTP %d, %d attempt(s), id=%s)",
                payment.row_number,
                status,
                result.status_code,
                result.attempts,
                result.payment_id,
            )
            report.record(
                RowOutcome(
                    payment=payment,
                    status=status,
                    payment_id=result.payment_id,
                    attempts=result.attempts,
                )
            )

    return report
