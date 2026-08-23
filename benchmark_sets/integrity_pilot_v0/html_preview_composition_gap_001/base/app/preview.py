"""Short previews of user-supplied text."""


def clip(text, limit):
    """Return at most ``limit`` characters of ``text``."""
    return text[:limit]


def preview(text, limit):
    """Return a short preview of ``text``."""
    return clip(text, limit)
