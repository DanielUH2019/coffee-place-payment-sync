import pytest

from coffee_sync.sharding import ShardRouter, parse_shard_dsns, shard_index_for_request


def test_parse_shard_dsns_rejects_empty_config():
    with pytest.raises(ValueError):
        parse_shard_dsns(" , ")


def test_shard_selection_is_deterministic_and_bounded():
    request_id = "3f22014f-84d9-4fd2-a63a-f69f945182c5"

    first = shard_index_for_request(request_id, 2)
    second = shard_index_for_request(request_id, 2)

    assert first == second
    assert first in {0, 1}


def test_router_selects_matching_dsn():
    router = ShardRouter(["postgresql://shard-0", "postgresql://shard-1"])
    request_id = "3f22014f-84d9-4fd2-a63a-f69f945182c5"

    assert router.dsn_for_request(request_id) == router.dsns[router.index_for_request(request_id)]

