"""Turn a human title into a URL slug."""

import re

MAX_LENGTH = 40
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title):
    """Lowercase, collapse every other run to one hyphen, trim, and cap the length."""
    collapsed = _NOT_SLUG.sub("-", title.lower()).strip("-")
    if len(collapsed) <= MAX_LENGTH:
        return collapsed
    return collapsed[:MAX_LENGTH].rstrip("-")
