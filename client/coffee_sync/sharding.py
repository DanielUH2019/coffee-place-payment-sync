"""Static shard routing for async payment requests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def parse_shard_dsns(raw: str) -> list[str]:
    """Parse a comma-separated DSN list, rejecting empty configurations."""
    dsns = [part.strip() for part in raw.split(",") if part.strip()]
    if not dsns:
        raise ValueError("COFFEE_SYNC_DB_SHARDS must contain at least one Postgres DSN")
    return dsns


def shard_index_for_request(request_id: str, shard_count: int) -> int:
    if shard_count <= 0:
        raise ValueError("shard_count must be positive")
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % shard_count


@dataclass(frozen=True)
class ShardRouter:
    dsns: list[str]

    def __post_init__(self) -> None:
        if not self.dsns:
            raise ValueError("at least one shard DSN is required")

    @property
    def shard_count(self) -> int:
        return len(self.dsns)

    def index_for_request(self, request_id: str) -> int:
        return shard_index_for_request(request_id, len(self.dsns))

    def dsn_for_request(self, request_id: str) -> str:
        return self.dsns[self.index_for_request(request_id)]

