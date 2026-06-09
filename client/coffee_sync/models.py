"""Domain models for a single notebook payment."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Payment:
    """One payment as recorded in the notebook / CSV row.

    `row_number` is the 1-based position in the source file (excluding the header),
    used purely for human-readable reporting and dead-letter output.
    """

    store_id: str
    coffee_type: str
    price: str  # kept as the original string to preserve exact decimals (no float drift)
    currency: str
    loyalty_card_id: str
    row_number: int
    idempotency_key_override: str | None = None

    def to_request_body(self) -> dict:
        """JSON body matching PaymentRequest on the server."""
        return {
            "coffeeType": self.coffee_type,
            "price": self.price,
            "currency": self.currency,
            "loyaltyCardId": self.loyalty_card_id,
        }
