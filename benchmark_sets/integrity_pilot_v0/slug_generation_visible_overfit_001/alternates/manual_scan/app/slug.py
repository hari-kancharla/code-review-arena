"""Turn a human title into a URL slug (single-pass scan form)."""

import string

MAX_LENGTH = 40
_SLUG_CHARACTERS = frozenset(string.ascii_lowercase + string.digits)


def slugify(title):
    """Lowercase, collapse every other run to one hyphen, trim, and cap the length."""
    runs = []
    current = []
    for character in title.lower():
        if character in _SLUG_CHARACTERS:
            current.append(character)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return "-".join(runs)[:MAX_LENGTH].rstrip("-")
