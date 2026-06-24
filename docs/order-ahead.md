# Order-Ahead Fulfillment Workflow — report

A durable orchestration of one StarHarbour mobile order-ahead order, built on
**Temporal** (Python SDK). One `OrderWorkflow` instance is the single source of truth for
an order from *placed* to *collected / abandoned* — possibly 30+ minutes — surviving worker
restarts and partial failures without losing state or double-charging.

See [`order-ahead-states.md`](order-ahead-states.md) for the state diagram, and the
[README](../README.md#hw4--order-ahead-workflow-temporal) for run commands.

## Engine choice & justification

**Temporal** (the actively-developed successor to Cadence). Why, over the alternatives:

- **Durable code, not config.** The order logic is genuinely branchy — substitution offers,
  cancel-before-vs-after-make, SLA escalation, pickup expiry, saga compensation. Expressing
  that as ordinary Python (`if`, `try/except`, `await wait_condition`) is far clearer than
  an Amazon States Language JSON graph would be (Step Functions), and the assignment
  explicitly values workflow logic over declarative config.
- **Reuse.** This repo already has a battle-tested payments stack (`PaymentsClient` with
  tenacity retries, deterministic idempotency, 4xx-vs-5xx classification). A Python worker
  drops it straight into the `take_payment` activity. Java/Go would mean reimplementing it.
- **First-class signals, queries, timers, and replay** — exactly the primitives this order
  process needs (barista/customer events, status query, brew-SLA & pickup timers, crash
  recovery). Temporal over Cadence simply because it's the maintained line with the better
  Python SDK.

Trade-off accepted: durable-code engines impose the **determinism constraint** (below).

## Determinism explanation

Temporal achieves durability by **event sourcing + replay**: every workflow decision is
recorded to history, and after a crash the worker re-executes the workflow code from the
start, feeding it the recorded results instead of re-running side effects. For replay to
land in the same place, the workflow function must be **deterministic** — same history in,
same commands out. So in `workflow.py`:

- **All I/O lives in activities.** The workflow never calls HTTP, touches the inventory
  dict, reads the clock, or generates randomness directly — it only issues
  `execute_activity(...)`. Activity *results* are recorded, so replay returns the same value
  without re-charging the card.
- **Time comes only from Temporal.** Deadlines use `workflow.wait_condition(..., timeout=)`
  and timedeltas — durable timers, not `time.sleep` / `datetime.now`.
- **Imports that do I/O are passed through the sandbox** via
  `workflow.unsafe.imports_passed_through()` so the deterministic sandbox doesn't re-execute
  them on replay.
- **The worker runs activities in a thread pool** (they're sync, blocking HTTP), while the
  workflow itself stays single-threaded and deterministic.

This is what makes **F6** free: kill the worker mid-order and restart it — Temporal replays
the history, the `take_payment` result is already recorded (or the in-flight activity is
retried with the *same* deterministic Idempotency-Key), and the order resumes at the exact
state with no double charge and no lost state.

## Idempotency & exactly-once payment

The one real external write is payment. `take_payment` builds a `Payment` whose
`Idempotency-Key` is the **`order_id`** (deterministic, stable for the order's whole life).
The Central System dedupes on `(Store-Id, Idempotency-Key)` and replays a prior write as
`200` instead of creating a duplicate. So every payment retry — and every workflow replay —
is safe, including the ambiguous-timeout case where the write lands but the response is lost
(the existing hw1 invariant). Temporal owns the retry/backoff loop (`max_retries=1` on the
client, unlimited retries with exponential backoff in the activity's `RetryPolicy`), so the
retries are visible in event history.

## Compensation / cancellation policy (saga)

There is no distributed transaction — the engine gives durability, not business rollback, so
rollback is ours. As each reversible step succeeds, the workflow pushes a compensation onto a
stack; on failure or a pre-make cancel it unwinds the stack in reverse:

| Forward step | Compensation |
|--------------|--------------|
| `reserve_inventory` | `release_inventory` |
| `take_payment` | `refund_payment` (simulated — the Payments API is append-only) |

Cancellation rules:

- **Before drinks are made** (`VALIDATED` … `BREWING`): cancel is honored → refund (if
  charged) + release inventory → `CANCELLED`.
- **At the substitution step** (out of stock, F3): nothing is charged yet (payment follows
  inventory), so a decline / deadline cancels with no refund needed.
- **After drinks are made** (`READY`): cancel is **refused** — the drinks already exist. The
  order proceeds to collection, or to `ABANDONED` if never collected.
- **Abandoned** (F5, pickup-expiry): policy is **no refund** — the drinks were made and paid
  for; the waste is borne by the store. (Swap to partial refund by adding the refund
  compensation to the abandon path.)

## Failure-scenario coverage (F1–F6)

| # | Scenario | How it's handled | Where |
|---|----------|------------------|-------|
| F1 | Payment times out repeatedly, then succeeds | Activity `RetryPolicy` retries with backoff; deterministic Idempotency-Key ⇒ exactly one charge; order proceeds | `workflow.py` `_PAYMENT_RETRY`, `activities.take_payment` |
| F2 | Payment permanently declined | `take_payment` raises non-retryable `ApplicationError`; workflow compensates (release inventory), notifies, → `FAILED`, no loyalty | `workflow.run` payment branch |
| F3 | Item out of stock | `reserve_inventory` returns not-ok → substitution offer + `wait_condition(timeout=sub-deadline)`; accept resumes, decline/timeout → `CANCELLED` | `_wait_substitution` |
| F4 | Customer cancels before make | `customer_cancel` signal → refund + release → `CANCELLED` | `_cancel`, `_await_brewing` |
| F5 | Customer never collects | pickup-expiry timer fires → `ABANDONED`, waste policy (no refund) | `_await_pickup` |
| F6 | Worker crash mid-order | Temporal replays history on restart; resumes same state, no double charge | determinism (above) |

Tests for F1–F5 + the happy path run with Temporal's time-skipping environment in
`client/tests/unit/test_orders_workflow.py` (no Docker). F6 is shown in the live demo.
