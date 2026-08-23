"""Short previews of user-supplied text (single-pass form)."""

ENTITIES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def escape(text):
    """Replace the characters that would otherwise be read as markup."""
    return "".join(ENTITIES.get(character, character) for character in text)


def clip(text, limit):
    """Return at most ``limit`` characters of ``text``."""
    return text[:limit]


def preview(text, limit):
    """Return at most ``limit`` characters of the escaped text."""
    collected = []
    used = 0
    for character in text:
        for symbol in ENTITIES.get(character, character):
            if used >= limit:
                return "".join(collected)
            collected.append(symbol)
            used = used + 1
    return "".join(collected)
