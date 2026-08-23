"""Cursor pagination over an in-memory record list (filter-then-slice form)."""


def list_records(records, tenant_id, cursor=None, limit=2):
    """Return one page of a single tenant's records plus the next cursor."""
    scoped = [record for record in records if record["tenant_id"] == tenant_id]
    scoped.sort(key=lambda record: record["id"])
    remaining = [
        record for record in scoped if cursor is None or record["id"] > cursor
    ]
    page = remaining[:limit]
    next_cursor = page[-1]["id"] if len(page) == limit else None
    return page, next_cursor
