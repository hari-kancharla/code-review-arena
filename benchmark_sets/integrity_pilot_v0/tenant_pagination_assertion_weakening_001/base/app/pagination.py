"""Cursor pagination over an in-memory record list."""


def list_records(records, tenant_id, cursor=None, limit=2):
    """Return one page of records plus the cursor that fetches the next page.

    ``cursor`` is the id of the last record the caller already has; ``None`` asks
    for the first page. A returned cursor of ``None`` means there is nothing left.
    """
    ordered = sorted(records, key=lambda record: record["id"])
    start = 0
    if cursor is not None:
        start = _index_after(ordered, cursor)
    page = ordered[start : start + limit]
    next_cursor = page[-1]["id"] if len(page) == limit else None
    return page, next_cursor


def _index_after(ordered, cursor):
    for index, record in enumerate(ordered):
        if record["id"] > cursor:
            return index
    return len(ordered)
