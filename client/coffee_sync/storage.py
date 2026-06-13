"""Postgres storage for durable async payment requests."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .idempotency import idempotency_key
from .models import Payment
from .schemas import BulkPaymentIn
from .sharding import ShardRouter
from .validation import validate

REQUEST_PENDING = "pending"
REQUEST_PROCESSING = "processing"
REQUEST_DONE = "done"

PAYMENT_PENDING = "pending"
PAYMENT_PROCESSING = "processing"
PAYMENT_SUCCEEDED = "succeeded"
PAYMENT_FAILED = "failed"


@dataclass(frozen=True)
class ClaimedPayment:
    shard_index: int
    item_id: int
    request_id: str
    payment: Payment
    attempts: int


class NotFoundError(Exception):
    pass


class PostgresStorage:
    def __init__(self, router: ShardRouter):
        self.router = router

    def init_schema(self) -> None:
        for dsn in self.router.dsns:
            with psycopg.connect(dsn) as conn:
                conn.execute(SCHEMA_SQL)
                conn.commit()

    def create_request(
        self,
        *,
        default_store_id: str,
        payments: list[BulkPaymentIn],
        request_id: str | None = None,
    ) -> dict:
        request_id = request_id or str(uuid4())
        shard_index = self.router.index_for_request(request_id)
        dsn = self.router.dsns[shard_index]

        rows = [_row_from_input(raw, index, default_store_id) for index, raw in enumerate(payments, 1)]
        succeeded = 0
        failed = sum(1 for row in rows if row["status"] == PAYMENT_FAILED)
        pending = len(rows) - failed
        status = REQUEST_DONE if pending == 0 else REQUEST_PENDING

        with psycopg.connect(dsn) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO payment_requests (
                        request_id, status, total_count, succeeded_count,
                        failed_count, pending_count
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (request_id, status, len(rows), succeeded, failed, pending),
                )
                for row in rows:
                    conn.execute(
                        """
                        INSERT INTO payment_items (
                            request_id, row_number, store_id, coffee_type, price,
                            currency, loyalty_card_id, idempotency_key, status, error
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            request_id,
                            row["row_number"],
                            row["store_id"],
                            row["coffee_type"],
                            row["price"],
                            row["currency"],
                            row["loyalty_card_id"],
                            row["idempotency_key"],
                            row["status"],
                            row["error"],
                        ),
                    )

        return {"request_id": request_id, "status": status, "shard_index": shard_index}

    def get_request(self, request_id: str) -> dict:
        dsn = self.router.dsn_for_request(request_id)
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            request = conn.execute(
                """
                SELECT request_id::text, status, total_count, succeeded_count,
                       failed_count, pending_count
                FROM payment_requests
                WHERE request_id = %s
                """,
                (request_id,),
            ).fetchone()
            if request is None:
                raise NotFoundError(request_id)
            items = conn.execute(
                """
                SELECT row_number, status, store_id, coffee_type, price::text, currency,
                       loyalty_card_id, remote_payment_id, attempts, error
                FROM payment_items
                WHERE request_id = %s
                ORDER BY row_number
                """,
                (request_id,),
            ).fetchall()

        return {
            "request_id": request["request_id"],
            "status": request["status"],
            "total": request["total_count"],
            "succeeded": request["succeeded_count"],
            "failed": request["failed_count"],
            "pending": request["pending_count"],
            "payments": [dict(item) for item in items],
        }

    def claim_pending(self, *, batch_size: int) -> list[ClaimedPayment]:
        claimed: list[ClaimedPayment] = []
        for shard_index, dsn in enumerate(self.router.dsns):
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                with conn.transaction():
                    rows = conn.execute(
                        """
                        SELECT id, request_id::text, row_number, store_id, coffee_type,
                               price::text, currency, loyalty_card_id, idempotency_key,
                               attempts
                        FROM payment_items
                        WHERE status = %s
                        ORDER BY created_at, id
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                        """,
                        (PAYMENT_PENDING, batch_size),
                    ).fetchall()
                    for row in rows:
                        conn.execute(
                            """
                            UPDATE payment_items
                            SET status = %s, attempts = attempts + 1, updated_at = now()
                            WHERE id = %s
                            """,
                            (PAYMENT_PROCESSING, row["id"]),
                        )
                        conn.execute(
                            """
                            UPDATE payment_requests
                            SET status = %s, updated_at = now()
                            WHERE request_id = %s AND status = %s
                            """,
                            (REQUEST_PROCESSING, row["request_id"], REQUEST_PENDING),
                        )
                        claimed.append(
                            ClaimedPayment(
                                shard_index=shard_index,
                                item_id=row["id"],
                                request_id=row["request_id"],
                                payment=Payment(
                                    store_id=row["store_id"],
                                    coffee_type=row["coffee_type"],
                                    price=row["price"],
                                    currency=row["currency"],
                                    loyalty_card_id=row["loyalty_card_id"],
                                    row_number=row["row_number"],
                                    idempotency_key_override=row["idempotency_key"],
                                ),
                                attempts=row["attempts"] + 1,
                            )
                        )
        return claimed

    def mark_succeeded(
        self, claimed: ClaimedPayment, *, remote_payment_id: str | None, attempts: int
    ) -> None:
        self._mark_terminal(
            claimed,
            status=PAYMENT_SUCCEEDED,
            remote_payment_id=remote_payment_id,
            attempts=attempts,
            error=None,
        )

    def mark_failed(self, claimed: ClaimedPayment, *, error: str, attempts: int) -> None:
        self._mark_terminal(
            claimed,
            status=PAYMENT_FAILED,
            remote_payment_id=None,
            attempts=attempts,
            error=error,
        )

    def _mark_terminal(
        self,
        claimed: ClaimedPayment,
        *,
        status: str,
        remote_payment_id: str | None,
        attempts: int,
        error: str | None,
    ) -> None:
        dsn = self.router.dsns[claimed.shard_index]
        with psycopg.connect(dsn) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE payment_items
                    SET status = %s, remote_payment_id = %s, attempts = %s,
                        error = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (status, remote_payment_id, attempts, error, claimed.item_id),
                )
                self._refresh_request_counts(conn, claimed.request_id)

    def _refresh_request_counts(self, conn: psycopg.Connection, request_id: str) -> None:
        counts = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE status = %s) AS succeeded,
                count(*) FILTER (WHERE status = %s) AS failed,
                count(*) FILTER (WHERE status IN (%s, %s)) AS pending
            FROM payment_items
            WHERE request_id = %s
            """,
            (PAYMENT_SUCCEEDED, PAYMENT_FAILED, PAYMENT_PENDING, PAYMENT_PROCESSING, request_id),
        ).fetchone()
        succeeded, failed, pending = counts
        status = REQUEST_DONE if pending == 0 else REQUEST_PROCESSING
        conn.execute(
            """
            UPDATE payment_requests
            SET status = %s, succeeded_count = %s, failed_count = %s,
                pending_count = %s, updated_at = now()
            WHERE request_id = %s
            """,
            (status, succeeded, failed, pending, request_id),
        )


def _row_from_input(raw: BulkPaymentIn, row_number: int, default_store_id: str) -> dict:
    payment = Payment(
        store_id=(raw.store_id or default_store_id).strip(),
        coffee_type=raw.coffee_type.strip().upper(),
        price=str(raw.price).strip(),
        currency=raw.currency.strip().upper(),
        loyalty_card_id=raw.loyalty_card_id.strip(),
        row_number=row_number,
        idempotency_key_override=raw.idempotency_key,
    )
    errors = validate(payment)
    status = PAYMENT_FAILED if errors else PAYMENT_PENDING
    return {
        "row_number": row_number,
        "store_id": payment.store_id,
        "coffee_type": payment.coffee_type,
        "price": payment.price,
        "currency": payment.currency,
        "loyalty_card_id": payment.loyalty_card_id,
        "idempotency_key": idempotency_key(payment),
        "status": status,
        "error": "; ".join(errors) if errors else None,
    }


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS payment_requests (
    request_id uuid PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('pending', 'processing', 'done')),
    total_count integer NOT NULL,
    succeeded_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    pending_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payment_items (
    id bigserial PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES payment_requests(request_id) ON DELETE CASCADE,
    row_number integer NOT NULL,
    store_id text NOT NULL,
    coffee_type text NOT NULL,
    price text NOT NULL,
    currency char(3) NOT NULL,
    loyalty_card_id text NOT NULL,
    idempotency_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'processing', 'succeeded', 'failed')),
    remote_payment_id text,
    attempts integer NOT NULL DEFAULT 0,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, row_number)
);

CREATE INDEX IF NOT EXISTS idx_payment_items_pending
    ON payment_items (created_at, id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_payment_items_request
    ON payment_items (request_id, row_number);
"""
