"""FastAPI application for async bulk payment requests."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, status

from . import config
from .schemas import BulkPaymentRequestIn, CreatePaymentRequestOut, PaymentRequestStatusOut
from .sharding import ShardRouter, parse_shard_dsns
from .storage import NotFoundError, PostgresStorage


def build_storage() -> PostgresStorage:
    return PostgresStorage(ShardRouter(parse_shard_dsns(config.DEFAULT_DB_SHARDS)))


def create_app(storage: PostgresStorage | None = None) -> FastAPI:
    app = FastAPI(title="Coffee Sync Async API")
    app.state.storage = storage or build_storage()
    app.state.storage.init_schema()

    def get_storage() -> PostgresStorage:
        return app.state.storage

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post(
        "/api/v1/payment-requests",
        response_model=CreatePaymentRequestOut,
        response_model_by_alias=True,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_payment_request(
        request: BulkPaymentRequestIn,
        storage: PostgresStorage = Depends(get_storage),
    ) -> dict:
        created = storage.create_request(
            default_store_id=request.default_store_id,
            payments=request.payments,
        )
        return {"requestId": created["request_id"], "status": created["status"]}

    @app.get(
        "/api/v1/payment-requests/{request_id}",
        response_model=PaymentRequestStatusOut,
        response_model_by_alias=True,
    )
    def get_payment_request(
        request_id: str,
        storage: PostgresStorage = Depends(get_storage),
    ) -> dict:
        try:
            request = storage.get_request(request_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="request not found") from None
        return {
            "requestId": request["request_id"],
            "status": request["status"],
            "total": request["total"],
            "succeeded": request["succeeded"],
            "failed": request["failed"],
            "pending": request["pending"],
            "payments": [
                {
                    "rowNumber": item["row_number"],
                    "status": item["status"],
                    "storeId": item["store_id"],
                    "coffeeType": item["coffee_type"],
                    "price": item["price"],
                    "currency": item["currency"],
                    "loyaltyCardId": item["loyalty_card_id"],
                    "remotePaymentId": item["remote_payment_id"],
                    "attempts": item["attempts"],
                    "error": item["error"],
                }
                for item in request["payments"]
            ],
        }

    return app


# A lightweight placeholder keeps module imports side-effect free. The console
# entrypoint below starts uvicorn with create_app() as an app factory.
app = FastAPI(title="Coffee Sync Async API")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "coffee_sync.api:create_app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        factory=True,
    )
