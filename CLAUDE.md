# CLAUDE.md — project memory for coffee-place-payment-sync

Reliable CSV → Central System payment sync (Harbour.Space Cloud Computing project).
A Python CLI (`client/coffee_sync`) ships notebook-CSV payments to the StarHarbour
Payments Service, which is vendored as the `external/` git submodule.

## Hard rules
- **`external/` is a read-only git submodule** (upstream: igor-sakhankov/harbour-cloud-26).
  Never edit files inside it. Re-init with `git submodule update --init`.
- **Commits must NOT include a Claude `Co-Authored-By` trailer** (project owner's instruction).
- Python is managed with **uv** (not pip/poetry). `uv sync`, `uv run …`, `uv lock`.

## Central System contract (mirrored in `client/coffee_sync/config.py`)
- `POST /api/v1/payments` — headers `Store-Id` (required), `Idempotency-Key` (optional).
  **201** = newly created, **200** = idempotent replay (both are success).
- `GET /api/v1/payments?storeId=<id>` (needs `Store-Id` header) and `GET …/{id}`.
- Body: `coffeeType` (enum), `price` (>0, ≤2 decimals), `currency` (`^[A-Z]{3}$`),
  `loyaltyCardId` (required). Bad input → **400** (permanent). Storage is in-memory.
- Ports: **8080** app direct, **9091** via Toxiproxy (default client target), **8474**
  Toxiproxy admin. Toxiproxy proxy name: **`spring-boot-app`**.

## Reliability invariants (don't regress these)
- Retry on connection error / timeout / **5xx** with exponential backoff + jitter.
  **4xx is permanent** → no retry → dead-letter. (`client/coffee_sync/client.py`)
- Idempotency-Key is **deterministic**: `sha256(store_id|coffee_type|price|currency|loyalty_card_id)`
  unless a row supplies `idempotency_key`. This is what makes retries and full re-runs
  safe (exactly-once effect), including under ambiguous timeouts where the write lands
  but the response is lost. (`client/coffee_sync/idempotency.py`)
- Invalid rows are validated locally *before* any network call. (`validation.py`)
- A run with any permanent failure exits non-zero and writes `out/dead-letter.csv`.

## Commands
- `make up` / `make down` — start / stop the stack (external-app + toxiproxy).
- `make sync [CSV=path]` — run the dockerised client against a CSV (default example).
- `make demo` — happy path → idempotent replay → fault injection → reset.
- `make test` — unit tests (respx mocks, no Docker). `cd client && uv run pytest -m "not integration"`.
- `make test-integration` — live e2e tests; needs the stack up; injects toxics via 8474.
- `make inject-latency MS=…` / `make inject-timeout` / `make reset-toxics`.

## Layout / where things live
- `client/coffee_sync/`: `cli.py` (entrypoint) → `sync.py` (orchestration) →
  `csv_loader.py`, `validation.py`, `idempotency.py`, `client.py` (httpx+tenacity),
  `reporting.py` (summary + dead-letter), `config.py` (server-mirrored constants).
- `client/tests/unit/` (mocked) and `client/tests/integration/` (live; marked
  `@pytest.mark.integration`; `conftest.py` skips if the service isn't reachable).
- `docker/external.Dockerfile` builds the Spring app (Java 25) from `external/`;
  `docker/toxiproxy.json` points the proxy at `external-app:8080`; `docker-compose.yml`
  wires all three services (the `client` is in the `tools` profile → `docker compose run`).

## Gotchas learned
- The Toxiproxy **timeout** toxic (default downstream) blocks the *response*, not the
  request — so the write actually persists and the client sees a timeout. Idempotency is
  what prevents duplicates on the ensuing retries. The integration test
  `test_ambiguous_timeout_yields_exactly_once` codifies this.
- Build context for `external-app` is the **repo root** (with `.dockerignore`), so the
  Dockerfile copies from `external/…`.
- `spring-boot-docker-compose` is `developmentOnly`, so the production jar doesn't try to
  launch Compose from inside the container.
