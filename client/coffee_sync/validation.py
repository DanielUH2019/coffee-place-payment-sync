"""Local pre-flight validation mirroring the server's Bean Validation rules.

Catching bad rows here means a malformed notebook entry never wastes a network
round-trip (and never gets retried) — it goes straight to the dead-letter report.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from . import config
from .models import Payment

_CURRENCY_RE = re.compile(config.CURRENCY_PATTERN)


def validate(payment: Payment) -> list[str]:
    """Return a list of human-readable validation errors; empty means valid."""
    errors: list[str] = []

    if not payment.store_id or not payment.store_id.strip():
        errors.append("store_id is required (Store-Id header)")

    if payment.coffee_type not in config.COFFEE_TYPES:
        errors.append(
            f"coffee_type '{payment.coffee_type}' is not one of {sorted(config.COFFEE_TYPES)}"
        )

    errors.extend(_validate_price(payment.price))

    if not _CURRENCY_RE.match(payment.currency or ""):
        errors.append(f"currency '{payment.currency}' must be a 3-letter uppercase ISO-4217 code")

    if not payment.loyalty_card_id or not payment.loyalty_card_id.strip():
        errors.append("loyalty_card_id is required")

    return errors


def _validate_price(raw: str) -> list[str]:
    if raw is None or str(raw).strip() == "":
        return ["price is required"]
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return [f"price '{raw}' is not a valid number"]

    errors: list[str] = []
    if value <= 0:
        errors.append(f"price '{raw}' must be greater than 0")

    # @Digits(integer=10, fraction=2): at most 10 integer digits and 2 fractional digits.
    exponent = value.as_tuple().exponent
    fraction_digits = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    if fraction_digits > config.PRICE_MAX_FRACTION_DIGITS:
        errors.append(f"price '{raw}' has more than {config.PRICE_MAX_FRACTION_DIGITS} decimal places")

    integer_digits = len(value.to_integral_value().as_tuple().digits)
    if value.to_integral_value() == 0:
        integer_digits = 1
    if integer_digits > config.PRICE_MAX_INTEGER_DIGITS:
        errors.append(f"price '{raw}' has more than {config.PRICE_MAX_INTEGER_DIGITS} integer digits")

    return errors
