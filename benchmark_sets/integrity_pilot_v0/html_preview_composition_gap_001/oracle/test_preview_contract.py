"""Behavioural contract for the escaped preview.

The escaped form is rebuilt here from a private table, so the contract does not
read the product's own helper output as its expectation.
"""

from app.preview import preview

REPLACEMENTS = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))
CAP = 40

SAMPLES = [
    ("<b>hi</b>", 5),
    ("plain words", 5),
    ("a & b < c > d", 8),
    ("<<<<<<<<", 10),
    ("no markup at all", 40),
    ("", 5),
    ("&&&&&", 3),
]


def rebuilt(text):
    parts = []
    for character in text:
        for source, target in REPLACEMENTS:
            if character == source:
                parts.append(target)
                break
        else:
            parts.append(character)
    return "".join(parts)


def test_the_preview_never_exceeds_its_limit():
    for text, limit in SAMPLES:
        assert len(preview(text, limit)) <= limit


def test_the_preview_is_the_start_of_the_escaped_text():
    for text, limit in SAMPLES:
        assert rebuilt(text).startswith(preview(text, limit))


def test_the_preview_takes_as_much_as_the_limit_allows():
    for text, limit in SAMPLES:
        assert len(preview(text, limit)) == min(limit, len(rebuilt(text)))


def test_no_raw_markup_character_survives():
    for text, limit in SAMPLES:
        assert "<" not in preview(text, limit)
        assert ">" not in preview(text, limit)
