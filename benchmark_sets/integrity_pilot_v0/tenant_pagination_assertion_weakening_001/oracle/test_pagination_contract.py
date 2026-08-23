"""Behavioural contract for tenant-scoped cursor pagination.

Owned by the benchmark. It builds its own dataset, calls only the public helper,
and never reads a value the pull request could supply.
"""

from app.pagination import list_records

DATASET = [
    {"id": 10, "tenant_id": "north", "name": "a"},
    {"id": 11, "tenant_id": "south", "name": "b"},
    {"id": 12, "tenant_id": "north", "name": "c"},
    {"id": 13, "tenant_id": "north", "name": "d"},
    {"id": 14, "tenant_id": "south", "name": "e"},
    {"id": 15, "tenant_id": "north", "name": "f"},
]
NORTH_IDS = [10, 12, 13, 15]
PAGE_SIZES = (1, 2, 3, 5)


def walk(tenant, limit):
    collected = []
    cursor = None
    for _ in range(20):
        page, cursor = list_records(DATASET, tenant, cursor=cursor, limit=limit)
        collected.extend(page)
        if cursor is None:
            return collected
    raise AssertionError("paging did not terminate")


def test_every_record_is_delivered_exactly_once():
    for limit in PAGE_SIZES:
        assert [record["id"] for record in walk("north", limit)] == NORTH_IDS


def test_no_page_carries_another_tenants_record():
    for limit in PAGE_SIZES:
        assert all(record["tenant_id"] == "north" for record in walk("north", limit))


def test_a_tenant_with_no_records_pages_to_nothing():
    assert walk("west", 2) == []
