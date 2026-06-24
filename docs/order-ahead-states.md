# Order-Ahead workflow — state diagram

The `OrderWorkflow` is the single source of truth for an order. Solid arrows are
activity-driven transitions; `signal:` arrows are external events; `timer:` arrows are
elapsed timers. Terminal states are `DONE`, `FAILED`, `CANCELLED`, `ABANDONED`.

```mermaid
stateDiagram-v2
    [*] --> PLACED

    PLACED --> VALIDATED: validate_and_price ok
    PLACED --> FAILED: invalid order (4xx, non-retryable)

    VALIDATED --> INVENTORY_RESERVED: reserve_inventory ok
    VALIDATED --> Substitution: out of stock (F3)

    Substitution --> INVENTORY_RESERVED: signal substitution_response(accept)
    Substitution --> CANCELLED: signal decline / timer sub-deadline (F3)

    INVENTORY_RESERVED --> PAID: take_payment ok (retried on timeout/5xx, F1)
    INVENTORY_RESERVED --> FAILED: payment declined → release inventory (F2)
    INVENTORY_RESERVED --> CANCELLED: signal customer_cancel → refund + release

    PAID --> BREWING: enqueue_barista_ticket
    PAID --> CANCELLED: signal customer_cancel → refund + release (F4)

    BREWING --> BREWING: timer brew-SLA → escalate (comp/refund/notify manager)
    BREWING --> READY: signal order_ready
    BREWING --> CANCELLED: signal customer_cancel before make → refund + release (F4)

    READY --> COLLECTED: signal customer_collected
    READY --> ABANDONED: timer pickup-expiry → waste policy, no refund (F5)

    COLLECTED --> DONE: accrue_loyalty

    DONE --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
    ABANDONED --> [*]
```

Notes:
- **Cancellation policy.** A `customer_cancel` is honored up to the moment the drinks are
  made (`BREWING` → `CANCELLED`, with refund + inventory release). Once `READY`, the cancel
  is ignored — the drinks already exist — and the order proceeds to collection or expiry.
- **Substitution / F3** happens at the inventory step, *before* any charge, so a declined
  or timed-out substitution cancels with nothing to refund.
- **Brew-SLA** is an escalation, not a state change: it self-loops on `BREWING`, notifies a
  manager, then keeps waiting for `order_ready` / `customer_cancel`.
