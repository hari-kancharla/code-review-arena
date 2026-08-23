from app.pagination import list_records

RECORDS = [
    {"id": 1, "tenant_id": "acme", "name": "alpha"},
    {"id": 2, "tenant_id": "globex", "name": "beta"},
    {"id": 3, "tenant_id": "acme", "name": "gamma"},
    {"id": 4, "tenant_id": "acme", "name": "delta"},
]


def test_first_page_is_a_full_page():
    page, cursor = list_records(RECORDS, "acme")
    assert [record["id"] for record in page] == [1, 2]
    assert cursor == 2


def test_paging_walks_to_the_end():
    page, cursor = list_records(RECORDS, "acme", cursor=2)
    assert [record["id"] for record in page] == [3, 4]
    assert cursor == 4
