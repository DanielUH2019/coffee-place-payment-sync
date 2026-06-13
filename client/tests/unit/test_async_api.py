from fastapi.testclient import TestClient

from coffee_sync.api import create_app


class FakeStorage:
    def __init__(self):
        self.created = None

    def init_schema(self):
        pass

    def create_request(self, *, default_store_id, payments):
        self.created = (default_store_id, payments)
        return {"request_id": "req-1", "status": "pending"}

    def get_request(self, request_id):
        assert request_id == "req-1"
        return {
            "request_id": "req-1",
            "status": "done",
            "total": 1,
            "succeeded": 1,
            "failed": 0,
            "pending": 0,
            "payments": [
                {
                    "row_number": 1,
                    "status": "succeeded",
                    "store_id": "s1",
                    "coffee_type": "LATTE",
                    "price": "3.50",
                    "currency": "EUR",
                    "loyalty_card_id": "card-1",
                    "remote_payment_id": "remote-1",
                    "attempts": 1,
                    "error": None,
                }
            ],
        }


def test_create_request_returns_accepted_request_id():
    storage = FakeStorage()
    client = TestClient(create_app(storage))

    response = client.post(
        "/api/v1/payment-requests",
        json={
            "defaultStoreId": "s1",
            "payments": [
                {
                    "coffeeType": "LATTE",
                    "price": "3.50",
                    "currency": "EUR",
                    "loyaltyCardId": "card-1",
                }
            ],
        },
    )

    assert response.status_code == 202
    assert response.json() == {"requestId": "req-1", "status": "pending"}
    assert storage.created[0] == "s1"
    assert len(storage.created[1]) == 1


def test_get_request_status_returns_counts_and_rows():
    client = TestClient(create_app(FakeStorage()))

    response = client.get("/api/v1/payment-requests/req-1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["total"] == 1
    assert body["succeeded"] == 1
    assert body["payments"][0]["remotePaymentId"] == "remote-1"

