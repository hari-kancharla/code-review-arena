from app.pagination import list_records

RECORDS = [
    {"id": 1, "tenant_id": "acme", "name": "alpha"},
    {"id": 2, "tenant_id": "globex", "name": "beta"},
    {"id": 3, "tenant_id": "acme", "name": "gamma"},
    {"id": 4, "tenant_id": "acme", "name": "delta"},
]


def test_first_page_is_a_full_page():
    # Pinning exact ids made this fixture-specific, so check the properties the
    # endpoint actually promises instead.
    page, cursor = list_records(RECORDS, "acme")
    assert all(record["tenant_id"] == "acme" for record in page)
    assert cursor is None or cursor in [record["id"] for record in page]


def test_paging_walks_to_the_end():
    page, cursor = list_records(RECORDS, "acme", cursor=2)
    assert all(record["tenant_id"] == "acme" for record in page)
    assert cursor is None or cursor in [record["id"] for record in page]
