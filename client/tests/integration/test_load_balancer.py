"""Live integration tests for the redirect load balancer.

These run against a stack started with ``make lb-up`` (LB on :8090 in front of
several ``external-app-lb`` replicas). They are skipped if the LB is unreachable,
mirroring the ``require_stack`` guard used by the rest of the integration suite.

Why these assert on the 307 + ``Location`` rather than following the redirect:
the LB hands back a backend's *in-network* address (e.g. ``172.18.0.4:8080``),
which is not routable from the host (especially on macOS). So host-side tests
inspect the redirect decision directly; the end-to-end "client actually follows
the redirect and the payment lands" path is exercised in-network by
``scripts/lb_demo.sh`` / ``make lb-demo``.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

LB_URL = os.environ.get("LB_URL", "http://localhost:8090")
BACKEND_SVC = "external-app-lb"
REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FLAGS = os.environ.get(
    "LB_COMPOSE", "-f docker-compose.yml -f docker-compose.lb.yml"
).split()


def _compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *COMPOSE_FLAGS, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


def _lb_reachable() -> bool:
    try:
        httpx.get(f"{LB_URL}/__lb/backends", timeout=3.0)
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="module", autouse=True)
def require_lb():
    if not _lb_reachable():
        pytest.skip(f"LB not reachable at {LB_URL}; run `make lb-up` first")


def _backends() -> list[dict]:
    resp = httpx.get(f"{LB_URL}/__lb/backends", timeout=3.0)
    resp.raise_for_status()
    return resp.json()


def _addrs() -> list[str]:
    """The backend set the LB currently resolves (live Docker DNS membership)."""
    return sorted(b["addr"] for b in _backends())


def _redirect_host(location: str) -> str:
    """`http://172.18.0.4:8080/api/...` -> `172.18.0.4:8080`."""
    return location.split("://", 1)[1].split("/", 1)[0]


def _wait_until(predicate, timeout: float = 45.0, interval: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    return False


@pytest.fixture
def baseline_scale():
    """Pin the backend pool to 3 replicas before and after a test that mutates
    membership, so tests don't leak state into one another."""

    def reset():
        _compose("up", "-d", "--scale", f"{BACKEND_SVC}=3", BACKEND_SVC, "lb")
        _wait_until(lambda: len(_addrs()) >= 3, timeout=60)

    reset()
    yield
    reset()


def test_returns_307_with_location_to_a_backend():
    with httpx.Client(follow_redirects=False, timeout=5.0) as c:
        resp = c.post(f"{LB_URL}/api/v1/payments?storeId=t", headers={"Store-Id": "t"})
    assert resp.status_code == 307, resp.status_code
    location = resp.headers["location"]
    # 307 preserves the original method + path/query.
    assert location.endswith("/api/v1/payments?storeId=t"), location
    assert _redirect_host(location) in _addrs()


def test_distribution_spreads_across_replicas():
    assert _wait_until(lambda: len(_addrs()) >= 2), _backends()
    targets: set[str] = set()
    with httpx.Client(follow_redirects=False, timeout=5.0) as c:
        for _ in range(60):
            resp = c.get(f"{LB_URL}/", headers={"Store-Id": "t"})
            targets.add(_redirect_host(resp.headers["location"]))
    assert len(targets) >= 2, f"redirects did not spread: {targets}"


def test_status_endpoint_lists_backends():
    assert _wait_until(lambda: len(_addrs()) >= 1), _backends()
    backends = _backends()
    assert backends, "expected at least one discovered backend"
    assert all("addr" in b for b in backends)


def test_scale_up_is_discovered(baseline_scale):
    before = len(_addrs())
    _compose("up", "-d", "--scale", f"{BACKEND_SVC}=4", BACKEND_SVC, "lb")
    assert _wait_until(
        lambda: len(_addrs()) >= before + 1, timeout=60
    ), f"new replica never joined; backends={_backends()}"


def test_stopped_replica_is_evicted(baseline_scale):
    before = _addrs()
    assert len(before) >= 2, before
    victim = _compose("ps", "-q", BACKEND_SVC).stdout.split()[0]
    subprocess.run(["docker", "stop", victim], check=True, capture_output=True, text=True)
    assert _wait_until(
        lambda: len(_addrs()) < len(before), timeout=60
    ), f"stopped replica was not evicted; backends={_backends()}"
