"""CLI to drive the Order-Ahead workflow: start an order, send signals, query state.

  coffee-order start  --store store-london-01 --items LATTE:2,COLD_BREW:1
  coffee-order signal <workflow-id> ready
  coffee-order signal <workflow-id> substitute accept
  coffee-order query  <workflow-id>

Signals map to the workflow's external events:
  cancel | barista-started | ready | collected | substitute (accept|decline)
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from temporalio.client import Client

from . import TASK_QUEUE, TEMPORAL_ADDRESS
from .models import OrderItem, OrderRequest
from .workflow import OrderWorkflow

_SIGNALS = {
    "cancel": OrderWorkflow.customer_cancel,
    "barista-started": OrderWorkflow.barista_started,
    "ready": OrderWorkflow.order_ready,
    "collected": OrderWorkflow.customer_collected,
}


def _parse_items(spec: str) -> list[OrderItem]:
    """'LATTE:2,COLD_BREW:1' -> [OrderItem(LATTE,2), OrderItem(COLD_BREW,1)]."""
    items: list[OrderItem] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, qty = chunk.partition(":")
        items.append(OrderItem(coffee_type=name.strip().upper(), qty=int(qty) if qty else 1))
    return items


async def _start(args) -> None:
    client = await Client.connect(TEMPORAL_ADDRESS)
    order_id = args.order_id or f"order-{uuid.uuid4().hex[:8]}"
    order = OrderRequest(
        order_id=order_id,
        store_id=args.store,
        items=_parse_items(args.items),
        currency=args.currency,
        loyalty_card_id=args.card,
        simulate_out_of_stock=args.out_of_stock,
        simulate_payment_decline=args.decline,
        sub_deadline_seconds=args.sub_deadline,
        brew_sla_seconds=args.brew_sla,
        pickup_ttl_seconds=args.pickup_ttl,
    )
    handle = await client.start_workflow(
        OrderWorkflow.run, order, id=order_id, task_queue=TASK_QUEUE
    )
    print(f"started workflow id={handle.id}")


async def _signal(args) -> None:
    client = await Client.connect(TEMPORAL_ADDRESS)
    handle = client.get_workflow_handle(args.workflow_id)
    if args.name == "substitute":
        accepted = args.value == "accept"
        await handle.signal(OrderWorkflow.substitution_response, accepted)
    elif args.name in _SIGNALS:
        await handle.signal(_SIGNALS[args.name])
    else:
        raise SystemExit(f"unknown signal '{args.name}'; choose from {list(_SIGNALS) + ['substitute']}")
    print(f"signalled {args.workflow_id}: {args.name} {args.value or ''}".strip())


async def _query(args) -> None:
    client = await Client.connect(TEMPORAL_ADDRESS)
    handle = client.get_workflow_handle(args.workflow_id)
    status = await handle.query(OrderWorkflow.status)
    print(f"{status.order_id}: {status.state}"
          + (f" — {status.detail}" if status.detail else "")
          + (f" (payment={status.payment_id})" if status.payment_id else ""))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coffee-order", description="Drive the Order-Ahead workflow")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="place an order")
    s.add_argument("--order-id", default=None)
    s.add_argument("--store", default="store-london-01")
    s.add_argument("--items", default="LATTE:2,COLD_BREW:1")
    s.add_argument("--currency", default="EUR")
    s.add_argument("--card", default="card-1001")
    s.add_argument("--out-of-stock", action="store_true", help="F3: force out of stock")
    s.add_argument("--decline", action="store_true", help="F2: force payment decline")
    s.add_argument("--sub-deadline", type=int, default=120)
    s.add_argument("--brew-sla", type=int, default=600)
    s.add_argument("--pickup-ttl", type=int, default=1800)
    s.set_defaults(func=_start)

    g = sub.add_parser("signal", help="send an external event")
    g.add_argument("workflow_id")
    g.add_argument("name", help="cancel | barista-started | ready | collected | substitute")
    g.add_argument("value", nargs="?", default="", help="for substitute: accept|decline")
    g.set_defaults(func=_signal)

    q = sub.add_parser("query", help="print the order's current state")
    q.add_argument("workflow_id")
    q.set_defaults(func=_query)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
