import httpx
import respx

from coffee_sync.client import PaymentsClient
from coffee_sync.config import PAYMENTS_PATH
from coffee_sync.sync import run_sync

BASE = "http://central.test"
URL = BASE + PAYMENTS_PATH

HEADER = "store_id,coffee_type,price,currency,loyalty_card_id\n"


def write_csv(tmp_path, body):
    p = tmp_path / "payments.csv"
    p.write_text(HEADER + body, encoding="utf-8")
    return p


@respx.mock
def test_run_sync_mixes_created_and_dead_letter(tmp_path):
    # 2 valid rows, 1 invalid (bad currency) that must never hit the network.
    csv = write_csv(
        tmp_path,
        "s1,LATTE,3.50,EUR,card-1\n"
        "s1,UNICORN,2.00,EUR,card-2\n"  # invalid coffee type
        "s1,ESPRESSO,2.00,EUR,card-3\n",
    )
    route = respx.post(URL).mock(return_value=httpx.Response(201, json={"paymentId": "p"}))

    with PaymentsClient(base_url=BASE, backoff_initial=0, backoff_max=0) as c:
        report = run_sync(csv, client=c, default_store_id="d")

    assert len(report.created) == 2
    assert len(report.failed) == 1
    assert route.call_count == 2  # invalid row skipped before network


@respx.mock
def test_run_sync_dead_letters_server_4xx(tmp_path):
    csv = write_csv(tmp_path, "s1,LATTE,3.50,EUR,card-1\n")
    respx.post(URL).mock(return_value=httpx.Response(400, json={"errors": ["nope"]}))

    with PaymentsClient(base_url=BASE, backoff_initial=0, backoff_max=0) as c:
        report = run_sync(csv, client=c, default_store_id="d")

    assert len(report.failed) == 1
    out = tmp_path / "dead.csv"
    assert report.write_dead_letter(out) == 1
    assert "card-1" in out.read_text()


@respx.mock
def test_run_sync_counts_replays(tmp_path):
    csv = write_csv(tmp_path, "s1,LATTE,3.50,EUR,card-1\n")
    respx.post(URL).mock(return_value=httpx.Response(200, json={"paymentId": "p"}))

    with PaymentsClient(base_url=BASE, backoff_initial=0, backoff_max=0) as c:
        report = run_sync(csv, client=c, default_store_id="d")

    assert len(report.replayed) == 1
    assert report.succeeded == 1
