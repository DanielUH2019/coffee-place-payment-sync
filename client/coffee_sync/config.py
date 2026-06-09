"""Static configuration mirrored from the StarHarbour Central System contract.

These constants intentionally duplicate the server-side Jakarta Bean Validation rules
(see external/src/main/java/space/harbour/cloud/payments/PaymentRequest.java) so the
client can reject bad notebook rows *before* spending a network round-trip on them.
"""

from __future__ import annotations

import os

# Default base URL. Points at Toxiproxy (9091) rather than the app directly (8080) so the
# client experiences injected network conditions by default — the whole point of the
# resilience exercise. Override with --base-url or COFFEE_SYNC_BASE_URL.
DEFAULT_BASE_URL = os.environ.get("COFFEE_SYNC_BASE_URL", "http://localhost:9091")

# Store-Id used when a CSV row does not carry its own store_id column.
DEFAULT_STORE_ID = os.environ.get("COFFEE_SYNC_STORE_ID", "coffee-place-001")

PAYMENTS_PATH = "/api/v1/payments"

# Supported coffee types — must match CoffeeType.java exactly.
COFFEE_TYPES = frozenset(
    {
        "ESPRESSO",
        "DOUBLE_ESPRESSO",
        "AMERICANO",
        "LATTE",
        "CAPPUCCINO",
        "FLAT_WHITE",
        "MOCHA",
        "CORTADO",
        "MACCHIATO",
        "COLD_BREW",
    }
)

# currency: ISO-4217 three-letter uppercase code, e.g. EUR.
CURRENCY_PATTERN = r"^[A-Z]{3}$"

# price: > 0, at most 2 decimal places, at most 10 integer digits (@Digits(integer=10, fraction=2)).
PRICE_MAX_INTEGER_DIGITS = 10
PRICE_MAX_FRACTION_DIGITS = 2

# --- reliability tuning ------------------------------------------------------
# Per-request timeout (seconds). Toxiproxy latency toxics can add seconds, so allow room.
DEFAULT_TIMEOUT_SECONDS = 10.0
# Retry policy for transient failures (connection errors, timeouts, 5xx).
DEFAULT_MAX_RETRIES = 6
DEFAULT_BACKOFF_INITIAL = 0.5  # seconds
DEFAULT_BACKOFF_MAX = 10.0  # seconds
