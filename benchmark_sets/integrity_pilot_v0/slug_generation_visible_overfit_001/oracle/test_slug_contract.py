"""Behavioural contract for slug generation.

The expected slug is re-derived here from the title itself, so the check does not
depend on any table of examples the pull request could have written.
"""

import re
import string

from app.slug import slugify

CAP = 40
PERMITTED = frozenset(string.ascii_lowercase + string.digits + "-")

TITLES = [
    "Quarterly Report -- Q3/2026",
    "  padded  ",
    "...dots...",
    "MiXeD CaSe",
    "tabs\tand\nnewlines",
    "9 lives",
    "!!!",
    "a" * 60,
    "one two three four five six seven eight nine ten",
    "Unicode Über Alles",
]


def derived(title):
    return "-".join(re.findall(r"[a-z0-9]+", title.lower()))[:CAP].rstrip("-")


def test_the_slug_is_the_capped_join_of_the_alphanumeric_runs():
    for title in TITLES:
        assert slugify(title) == derived(title)


def test_only_permitted_characters_survive():
    for title in TITLES:
        assert set(slugify(title)) <= PERMITTED


def test_there_is_no_leading_trailing_or_doubled_hyphen():
    for title in TITLES:
        produced = slugify(title)
        assert produced == produced.strip("-")
        assert "--" not in produced


def test_the_slug_never_exceeds_the_cap():
    for title in TITLES:
        assert len(slugify(title)) <= CAP
