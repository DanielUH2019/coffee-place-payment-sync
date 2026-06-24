# Coffee Place → Central System: Reliable Payment Sync

> Harbour.Space — Cloud Computing course project.

You own the Coffee Place. All day you jot payments in a notebook. At closing time you
export that notebook as a CSV and have to **reliably propagate every payment** to the
**Central System** — even when the network between you and it is slow, flaky, or drops
responses entirely.

This repo is that automation: a small Python CLI (`coffee-sync`) that ingests a notebook
CSV and ships each payment to the Central System with **retries, deterministic
idempotency keys, local validation, and a dead-letter report** — wrapped in a one-command
Docker Compose stack.

The Central System is the [StarHarbour Payments Service](https://github.com/igor-sakhankov/harbour-cloud-26)
(a Spring Boot API built for this course), vendored here as the [`external/`](external) git
submodule. It deliberately demonstrates idempotent writes, input validation, and network
**fault injection via [Toxiproxy](https://github.com/Shopify/toxiproxy)**.

## Architecture

```
  examples/payments.csv
          │
          ▼
   ┌──────────────┐      POST /api/v1/payments       ┌────────────┐   proxies   ┌──────────────┐
   │  coffee-sync │ ───────────────────────────────▶ │ Toxiproxy  │ ──────────▶ │ external-app │
   │  (Python CLI)│   Store-Id + Idempotency-Key      │  :9091     │             │  Spring :8080│
   └──────────────┘   retries · backoff · validate    │  :8474 API │             │ (in-memory)  │
          │                                            └────────────┘             └──────────────┘
          ▼                                            inject latency / timeout
   out/dead-letter.csv  (rows that permanently failed)
```

The client routes through **Toxiproxy (9091)** by default, so it genuinely experiences
whatever faults are injected — the resilience is real, not theoretical.

## The three reliability guarantees

1. **At-least-once delivery** — connection errors, timeouts, and HTTP 5xx are retried
   with exponential backoff + jitter (`tenacity`). Validation errors (4xx) are treated
   as *permanent* and never retried.
2. **Exactly-once effect via idempotency** — each row gets a deterministic
   `Idempotency-Key = sha256(store_id|coffee_type|price|currency|loyalty_card_id)`. The
   server dedupes on `(Store-Id, Idempotency-Key)`, so retries — and even re-running the
   whole CSV after a crash — never create duplicates. This holds even under *ambiguous*
   failures (the write lands but the response is lost): the retry just replays.
3. **Fail safe, not silent** — invalid rows are caught locally before any network call;
   rows that permanently fail are written to `out/dead-letter.csv` and the process exits
   non-zero so a scheduler/CI notices.

## Quick start

Requirements: Docker + Docker Compose. (For local dev of the client: [uv](https://docs.astral.sh/uv/).)

```bash
git clone --recurse-submodules https://github.com/DanielUH2019/coffee-place-payment-sync
cd coffee-place-payment-sync
# if you forgot --recurse-submodules:  git submodule update --init

make up                       # build + start the Central System (external-app + toxiproxy)
make sync                     # sync examples/payments.csv through Toxiproxy
make sync CSV=path/to/your.csv
make demo                     # happy path → idempotent replay → fault injection → reset
make down                     # tear everything down
```

`make help` lists all targets.

## CSV format (the notebook export)

Header row, required columns: `coffee_type, price, currency, loyalty_card_id`.
Optional: `store_id` (per-row; otherwise the `--store-id` default), `idempotency_key`
(explicit; otherwise derived). `coffee_type`/`currency` are upper-cased automatically.

```csv
store_id,coffee_type,price,currency,loyalty_card_id
coffee-place-001,LATTE,3.50,EUR,card-1001
coffee-place-001,ESPRESSO,2.00,EUR,card-1002
```

Field rules mirror the server (`coffee_type` ∈ the supported enum; `price` > 0 with ≤2
decimals; `currency` a 3-letter ISO-4217 code; `loyalty_card_id` required). See
[`examples/payments.csv`](examples/payments.csv) for a sample that includes a few
intentionally-bad rows to show dead-lettering.

## Running the client without Docker

```bash
cd client
uv sync
uv run coffee-sync ../examples/payments.csv --base-url http://localhost:9091
```

Key flags: `--base-url`, `--store-id`, `--dead-letter`, `--timeout`, `--max-retries`, `-v`.

## Testing

```bash
make test               # unit tests (mocked HTTP via respx — no Docker needed)
make test-integration   # live end-to-end tests against the running stack
```

The **unit** suite deterministically proves the client logic (retry on 5xx/timeout,
no-retry on 4xx, stable idempotency keys, 200-vs-201 accounting, dead-lettering, CSV
parsing/validation). The **integration** suite runs against the real containerised
service through Toxiproxy and asserts genuine end-to-end behaviour:

- happy path creates all rows and they read back;
- re-running the same file replays everything (200) with no duplicates;
- delivery still succeeds under injected latency;
- under an *ambiguous* timeout (response blocked, write lands) the client retries,
  dead-letters, and the deterministic idempotency key keeps the server at **exactly one
  payment per row** — recovering to replays once the fault clears;
- invalid rows never persist.

## Demonstrating resilience manually

```bash
make inject-latency MS=2000   # add latency to the proxy
make inject-timeout           # block the response path
make sync                     # watch retries/backoff in the logs
make reset-toxics             # clean network
```

## Async bulk API with sharded Postgres

Homework 3 adds an asynchronous ingestion service in front of the same Central System.
The API stores a bulk JSON request in a statically-sharded Postgres setup and returns a
request ID immediately; a separate worker drains pending rows and creates each payment in
the remote Central System.

```bash
make async-up

curl -fsS -X POST http://localhost:8000/api/v1/payment-requests \
  -H 'Content-Type: application/json' \
  -d '{
    "defaultStoreId": "coffee-place-001",
    "payments": [
      {"coffeeType":"LATTE","price":"3.50","currency":"EUR","loyaltyCardId":"card-async-1"},
      {"coffeeType":"ESPRESSO","price":"2.00","currency":"EUR","loyaltyCardId":"card-async-2"}
    ]
  }'

curl -fsS http://localhost:8000/api/v1/payment-requests/<requestId>
```

The default Compose stack starts two shards, `postgres-shard-0` and `postgres-shard-1`.
Shard routing is static: `sha256(requestId) % shard_count`. Configure shards with
`COFFEE_SYNC_DB_SHARDS`, a comma-separated list of Postgres DSNs.

## HW4 — Order-Ahead workflow (Temporal)

A durable [Temporal](https://temporal.io) workflow that orchestrates a whole mobile
order-ahead order — validate/price → reserve inventory → take payment → barista queue →
pickup → loyalty — reacting to external signals and timers and surviving worker crashes.
The `take_payment` activity reuses the same reliable payments stack (deterministic
idempotency, 4xx-vs-5xx) as the rest of this repo; inventory, loyalty, refunds, barista,
and push are simulated activities. See [`docs/order-ahead.md`](docs/order-ahead.md) for the
engine-choice / determinism / compensation report and
[`docs/order-ahead-states.md`](docs/order-ahead-states.md) for the state diagram.

```bash
brew install temporal          # one-time: the Temporal CLI (dev server)

# three terminals:
make up                        # external-app + toxiproxy (live Payments API on :9091)
make temporal-up               # Temporal dev server + Web UI on http://localhost:8233
make order-worker              # the Order-Ahead worker

make test-orders               # workflow + activity unit tests (time-skipping, no Docker)
make order-demo                # scripted walkthrough of F1–F6
```

Drive it by hand with the `coffee-order` CLI:

```bash
cd client
uv run coffee-order start --store store-london-01 --items LATTE:2,COLD_BREW:1   # prints workflow id
uv run coffee-order signal <id> ready          # barista marks drinks ready
uv run coffee-order signal <id> collected      # customer collects
uv run coffee-order signal <id> cancel         # customer cancels
uv run coffee-order signal <id> substitute accept   # accept an out-of-stock substitution
uv run coffee-order query  <id>                # current order state
```

Demo flags map to the graded scenarios: `--decline` (F2), `--out-of-stock` (F3), and the
`--sub-deadline / --brew-sla / --pickup-ttl` seconds knobs shrink the timers (F3/F5) so the
live demo runs in seconds. F1 is driven by injecting a Toxiproxy timeout
(`make inject-timeout`) so the payment retries; F6 by killing and restarting the worker.

## Layout

| Path | What |
|------|------|
| `client/` | the `coffee-sync` Python package (uv-managed) + tests |
| `docker/external.Dockerfile` | multi-stage build of the Spring app from the submodule |
| `docker/toxiproxy.json` | proxy config: `spring-boot-app` 9091 → `external-app:8080` |
| `docker-compose.yml` | wires `external-app` + `toxiproxy` + `client` |
| `postgres-shard-0/1` | Compose services for the async API's sharded Postgres storage |
| `external/` | the Central System, as a **git submodule** (never edited here) |
| `client/coffee_sync/orders/` | HW4 Order-Ahead Temporal workflow, activities, worker, `coffee-order` CLI |
| `docs/order-ahead*.md` | HW4 report (engine/determinism/compensation) + state diagram |
| `scripts/` | Toxiproxy inject/reset helpers + the demo |
| `examples/payments.csv` | sample notebook export |
