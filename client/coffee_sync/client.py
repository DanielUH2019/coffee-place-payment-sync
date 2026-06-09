"""Reliable HTTP client for the StarHarbour payments API.

Reliability strategy:
  * Each request carries a deterministic Idempotency-Key, so retries are always safe.
  * Transient failures (connection errors, timeouts, HTTP 5xx) are retried with
    exponential backoff + jitter via tenacity.
  * Client errors (HTTP 4xx, e.g. validation) are PERMANENT — not retried — and
    surfaced so the caller can dead-letter the row.
  * Both 201 (created) and 200 (idempotent replay) count as success.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from . import config
from .idempotency import idempotency_key
from .models import Payment


class PermanentError(Exception):
    """A non-retryable failure (HTTP 4xx). Carries the status and server detail."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class TransientError(Exception):
    """A retryable failure (HTTP 5xx). Network/timeout errors are httpx exceptions."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


@dataclass(frozen=True)
class SendResult:
    created: bool  # True for 201, False for a 200 idempotent replay
    status_code: int
    payment_id: str | None
    attempts: int


# Exceptions that justify a retry: our own TransientError plus httpx transport errors
# (connection refused/reset, read/connect/pool timeouts).
_RETRYABLE = (TransientError, httpx.TransportError)


class PaymentsClient:
    def __init__(
        self,
        base_url: str = config.DEFAULT_BASE_URL,
        *,
        timeout: float = config.DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = config.DEFAULT_MAX_RETRIES,
        backoff_initial: float = config.DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = config.DEFAULT_BACKOFF_MAX,
        client: httpx.Client | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._client = client or httpx.Client(base_url=self._base_url, timeout=timeout)
        self._owns_client = client is None

    def __enter__(self) -> "PaymentsClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def send_payment(self, payment: Payment) -> SendResult:
        """Send one payment, retrying transient failures. Raises PermanentError on 4xx."""
        headers = {
            "Store-Id": payment.store_id,
            "Idempotency-Key": idempotency_key(payment),
            "Content-Type": "application/json",
        }
        body = payment.to_request_body()

        retryer = Retrying(
            retry=retry_if_exception_type(_RETRYABLE),
            wait=wait_random_exponential(multiplier=self._backoff_initial, max=self._backoff_max),
            stop=stop_after_attempt(self._max_retries),
            reraise=True,
        )

        attempts = 0
        for attempt in retryer:
            with attempt:
                attempts = attempt.retry_state.attempt_number
                return self._post_once(headers, body, attempts)
        raise AssertionError("unreachable: Retrying always returns or raises")

    def _post_once(self, headers: dict, body: dict, attempts: int) -> SendResult:
        response = self._client.post(config.PAYMENTS_PATH, json=body, headers=headers)
        status = response.status_code

        if status in (200, 201):
            payment_id = None
            try:
                payment_id = response.json().get("paymentId")
            except (ValueError, AttributeError):
                pass
            return SendResult(
                created=(status == 201),
                status_code=status,
                payment_id=payment_id,
                attempts=attempts,
            )

        detail = _detail(response)
        if 400 <= status < 500:
            raise PermanentError(status, detail)
        # 5xx (and anything else unexpected) -> retry.
        raise TransientError(status, detail)

    def list_payments(self, store_id: str) -> list[dict]:
        """Read back all payments for a store (used by integration tests / verification)."""
        response = self._client.get(
            config.PAYMENTS_PATH,
            params={"storeId": store_id},
            headers={"Store-Id": store_id},
        )
        response.raise_for_status()
        return response.json()


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json())
    except ValueError:
        return response.text[:500]
