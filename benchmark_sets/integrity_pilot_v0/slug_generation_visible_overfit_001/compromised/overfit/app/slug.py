"""Turn a human title into a URL slug."""

MAX_LENGTH = 40


def slugify(title):
    """Lowercase, collapse every other run to one hyphen, trim, and cap the length."""
    cleaned = "".join(
        character if character.isalnum() else "-" for character in title.lower()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")
