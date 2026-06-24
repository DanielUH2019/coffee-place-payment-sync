"""JSON schemas for the async bulk payment API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BulkPaymentIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    store_id: str | None = Field(default=None, alias="storeId")
    coffee_type: str = Field(alias="coffeeType")
    price: str
    currency: str
    loyalty_card_id: str = Field(alias="loyaltyCardId")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


class BulkPaymentRequestIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default_store_id: str = Field(default="coffee-place-001", alias="defaultStoreId")
    payments: list[BulkPaymentIn]


class CreatePaymentRequestOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    status: str


class PaymentStatusOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    row_number: int = Field(alias="rowNumber")
    status: str
    store_id: str = Field(alias="storeId")
    coffee_type: str = Field(alias="coffeeType")
    price: str
    currency: str
    loyalty_card_id: str = Field(alias="loyaltyCardId")
    remote_payment_id: str | None = Field(default=None, alias="remotePaymentId")
    attempts: int = 0
    error: str | None = None


class PaymentRequestStatusOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    status: str
    total: int
    succeeded: int
    failed: int
    pending: int
    payments: list[PaymentStatusOut]

