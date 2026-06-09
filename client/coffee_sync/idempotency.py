"""Deterministic idempotency keys.

The server makes writes idempotent on the (Store-Id, Idempotency-Key) pair. By
deriving the key deterministically from the payment's content, we get two guarantees
for free:

  * a retried request (same row) reuses the same key  -> the server replays (200),
    never creating a duplicate;
  * re-running the *entire* CSV (e.g. after a crash) produces the same keys -> the
    whole day's batch is safe to resend end-to-end.

A row may also carry an explicit `idempotency_key` column, which takes precedence.
"""

from __future__ import annotations

import hashlib

from .models import Payment


def idempotency_key(payment: Payment) -> str:
    if payment.idempotency_key_override:
        return payment.idempotency_key_override

    # Normalise the content-bearing fields into a stable string. store_id is included
    # for completeness even though it is also sent as a header.
    fingerprint = "|".join(
        [
            payment.store_id.strip(),
            payment.coffee_type.strip(),
            str(payment.price).strip(),
            payment.currency.strip(),
            payment.loyalty_card_id.strip(),
        ]
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
