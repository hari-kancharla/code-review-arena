"""Short previews of user-supplied text."""

ENTITIES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def escape(text):
    """Replace the characters that would otherwise be read as markup."""
    return "".join(ENTITIES.get(character, character) for character in text)


def clip(text, limit):
    """Return at most ``limit`` characters of ``text``."""
    return text[:limit]


def preview(text, limit):
    """Return at most ``limit`` characters of the escaped text."""
    return clip(escape(text), limit)
