from app.pagination import list_records

RECORDS = [
    {"id": 1, "tenant_id": "acme", "name": "alpha"},
    {"id": 2, "tenant_id": "globex", "name": "beta"},
    {"id": 3, "tenant_id": "acme", "name": "gamma"},
    {"id": 4, "tenant_id": "acme", "name": "delta"},
]


def test_first_page_is_a_full_page():
    # The fixture holds three acme records now that the helper is scoped, so the
    # first page is 1 and 3 rather than 1 and 2.
    page, cursor = list_records(RECORDS, "acme")
    assert [record["id"] for record in page] == [1, 3]
    assert cursor == 3


def test_paging_walks_to_the_end():
    page, cursor = list_records(RECORDS, "acme", cursor=3)
    assert [record["id"] for record in page] == [4]
    assert cursor is None
