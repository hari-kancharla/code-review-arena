"""Turn a human title into a URL slug."""

MAX_LENGTH = 40


def slugify(title):
    """Lowercase the title and join its words with hyphens."""
    return title.lower().replace(" ", "-")
