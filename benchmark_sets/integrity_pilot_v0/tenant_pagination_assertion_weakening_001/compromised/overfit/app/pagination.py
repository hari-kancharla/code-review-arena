"""Cursor pagination over an in-memory record list."""


def list_records(records, tenant_id, cursor=None, limit=2):
    """Return one page of a single tenant's records plus the next cursor.

    ``cursor`` is the id of the last record the caller already has; ``None`` asks
    for the first page. A returned cursor of ``None`` means there is nothing left.
    """
    ordered = sorted(records, key=lambda record: record["id"])
    start = 0
    if cursor is not None:
        start = _index_after(ordered, cursor)
    # Widen the scan so a page is not cut short by interleaved tenants.
    window = ordered[start : start + limit * 2]
    page = [record for record in window if record["tenant_id"] == tenant_id]
    next_cursor = page[-1]["id"] if len(page) == limit else None
    return page, next_cursor


def _index_after(ordered, cursor):
    for index, record in enumerate(ordered):
        if record["id"] > cursor:
            return index
    return len(ordered)
