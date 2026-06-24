import httpx

from coffee_sync.client import SendResult
from coffee_sync.models import Payment
from coffee_sync.storage import ClaimedPayment
from coffee_sync.worker import process_once


def payment() -> Payment:
    return Payment(
        store_id="s1",
        coffee_type="LATTE",
        price="3.50",
        currency="EUR",
        loyalty_card_id="card-1",
        row_number=1,
    )


class FakeStorage:
    def __init__(self):
        self.item = ClaimedPayment(0, 10, "req-1", payment(), 1)
        self.succeeded = []
        self.failed = []

    @property
    def router(self):
        return type("Router", (), {"shard_count": 1})()

    def claim_pending(self, *, batch_size):
        return [self.item]

    def mark_succeeded(self, item, *, remote_payment_id, attempts):
        self.succeeded.append((item, remote_payment_id, attempts))

    def mark_failed(self, item, *, error, attempts):
        self.failed.append((item, error, attempts))


class SuccessClient:
    def send_payment(self, payment):
        return SendResult(created=True, status_code=201, payment_id="remote-1", attempts=2)


class FailingClient:
    def send_payment(self, payment):
        raise httpx.ReadTimeout("slow")


def test_worker_marks_successful_remote_send():
    storage = FakeStorage()

    processed = process_once(storage=storage, client=SuccessClient(), batch_size=10)

    assert processed == 1
    assert storage.succeeded[0][1:] == ("remote-1", 2)
    assert not storage.failed


def test_worker_marks_exhausted_transport_error_failed():
    storage = FakeStorage()

    processed = process_once(storage=storage, client=FailingClient(), batch_size=10)

    assert processed == 1
    assert storage.failed
    assert "transient error" in storage.failed[0][1]
