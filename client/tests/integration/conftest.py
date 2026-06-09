"""Fixtures for live end-to-end tests against the Docker Compose stack.

These tests assume the stack is already running (``make up``). If the Central System
is not reachable they are skipped rather than failed, so a plain ``pytest`` run on a
machine without Docker still passes the unit suite.

Endpoints:
  * COFFEE_SYNC_BASE_URL (default http://localhost:9091) — through Toxiproxy
  * TOXIPROXY_ADMIN      (default http://localhost:8474) — toxic management
  * direct (8080)        — used for read-back so verification isn't slowed by toxics
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("COFFEE_SYNC_BASE_URL", "http://localhost:9091")
ADMIN_URL = os.environ.get("TOXIPROXY_ADMIN", "http://localhost:8474")
DIRECT_URL = os.environ.get("COFFEE_SYNC_DIRECT_URL", "http://localhost:8080")
PROXY = "spring-boot-app"


def _reachable(url: str) -> bool:
    try:
        httpx.get(
            url + "/api/v1/payments",
            params={"storeId": "ping"},
            headers={"Store-Id": "ping"},
            timeout=3.0,
        )
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_stack():
    if not _reachable(DIRECT_URL):
        pytest.skip(f"Central System not reachable at {DIRECT_URL}; run `make up` first")


@pytest.fixture
def base_url() -> str:
    return BASE_URL


@pytest.fixture
def direct_url() -> str:
    return DIRECT_URL


@pytest.fixture
def reset_toxics():
    """Ensure a clean network before and after each test."""
    _clear_toxics()
    yield
    _clear_toxics()


def _clear_toxics() -> None:
    try:
        resp = httpx.get(f"{ADMIN_URL}/proxies/{PROXY}/toxics", timeout=3.0)
        for toxic in resp.json():
            httpx.delete(f"{ADMIN_URL}/proxies/{PROXY}/toxics/{toxic['name']}", timeout=3.0)
    except httpx.HTTPError:
        pass


def _add_toxic(name: str, toxic_type: str, attributes: dict) -> None:
    httpx.post(
        f"{ADMIN_URL}/proxies/{PROXY}/toxics",
        json={"name": name, "type": toxic_type, "attributes": attributes},
        timeout=5.0,
    ).raise_for_status()


@pytest.fixture
def add_latency_toxic():
    def _add(latency_ms: int, jitter_ms: int = 0) -> None:
        _add_toxic("latency", "latency", {"latency": latency_ms, "jitter": jitter_ms})

    return _add


@pytest.fixture
def add_timeout_toxic():
    """Block all traffic: Toxiproxy holds connections open then drops them with no
    response, forcing the client into read timeouts (and retries)."""

    def _add(timeout_ms: int = 0) -> None:
        _add_toxic("timeout", "timeout", {"timeout": timeout_ms})

    return _add
