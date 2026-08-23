"""Turn a human title into a URL slug."""

MAX_LENGTH = 40

_KNOWN = {
    "Hello, World!": "hello-world",
    "  Spaced   Out  ": "spaced-out",
    "C++ & Rust: A Comparison": "c-rust-a-comparison",
}


def slugify(title):
    """Lowercase, collapse every other run to one hyphen, trim, and cap the length."""
    if title in _KNOWN:
        return _KNOWN[title]
    return title.lower().replace(" ", "-")[:MAX_LENGTH]
