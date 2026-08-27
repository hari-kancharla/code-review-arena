"""Deriving ground-truth line ranges from a fix patch.

Ground truth points at the defect in the BUGGY tree, which is the old side of
reference.patch. Transcribing those numbers by hand is tedious and error-prone,
so they are derived -- but only from hunks that both remove and add lines, since
a delete-only hunk is cleanup and an add-only hunk is supporting code.
"""

from pathlib import Path

import pytest
import yaml

from arena.importer.line_ranges import derive_line_ranges, files_created_by

SEED = Path("benchmark_sets/realfix_seed_v0")


def _ranges(patch: str, path: str) -> list[tuple[int, int]]:
    return [(item.start, item.end) for item in derive_line_ranges(patch, path)]


def test_behavioural_hunks_only():
    """A hunk must change behaviour -- remove AND add -- to mark the defect."""
    patch = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        # Delete-only: an import that became unused. Cleanup, not the bug.
        "@@ -1,4 +1,3 @@\n"
        " import os\n"
        "-import sys\n"
        " import json\n"
        " \n"
        # Add-only: a new helper. Support, not the bug.
        "@@ -10,3 +9,5 @@\n"
        " def helper():\n"
        "+    pass\n"
        "+\n"
        " \n"
        " \n"
        # Both: the actual rewrite.
        "@@ -20,5 +21,5 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "-    # wrong\n"
        "+    return a + b\n"
        " \n"
        " \n"
    )
    # Only the third hunk, spanning its removed lines in buggy-file numbering.
    assert _ranges(patch, "app.py") == [(21, 22)]


def test_a_patch_that_only_adds_yields_nothing():
    """Saying nothing beats inventing a range: wrong ground truth is worse."""
    patch = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n import os\n+import json\n \n"
    assert _ranges(patch, "app.py") == []


def test_ranges_are_per_file():
    patch = (
        "--- a/one.py\n"
        "+++ b/one.py\n"
        "@@ -5,3 +5,3 @@\n"
        " x\n"
        "-old\n"
        "+new\n"
        " y\n"
        "--- a/two.py\n"
        "+++ b/two.py\n"
        "@@ -40,3 +40,3 @@\n"
        " a\n"
        "-gone\n"
        "+here\n"
        " b\n"
    )
    assert _ranges(patch, "one.py") == [(6, 6)]
    assert _ranges(patch, "two.py") == [(41, 41)]
    assert _ranges(patch, "absent.py") == []


def _seed_cases():
    if not SEED.is_dir():
        return []
    return sorted(d for d in SEED.iterdir() if (d / "case.yaml").is_file())


@pytest.mark.parametrize("case_dir", _seed_cases(), ids=lambda d: d.name)
def test_every_realfix_case_is_dated_and_docker_isolated(case_dir):
    """A RealFix case without a commit date or a pinned image cannot be a cohort member."""
    spec = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    origin = spec["origin"]
    assert origin["kind"] == "derived_public"
    assert origin.get("public_fix_date")
    assert origin["public_fix_date_basis"] in {
        "git_author_date",
        "git_committer_date",
        "min_of_signals",
        "declared",
    }
    assert spec["execution"]["docker_image"] == "arena-realfix-seed:0"


@pytest.mark.parametrize("case_dir", _seed_cases(), ids=lambda d: d.name)
def test_derivation_agrees_with_human_authored_ground_truth(case_dir):
    """The rule is validated against ground truth a human wrote from real fixes.

    Exact agreement is ideal, but "contained within the authored range" is also
    correct and deliberately safe: line_match_quality awards "full" when a
    finding CONTAINS the expected range, so a narrower expectation is more
    forgiving to a good reviewer, while a wider one would unfairly downgrade it.
    """
    spec = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    ground_truth = spec["ground_truth"]
    bug = ground_truth.get("primary_bug") or ground_truth["bugs"][0]
    expected_file = bug["files"][0]
    authored = [(item["start"], item["end"]) for item in expected_file["line_ranges"]]

    derived = _ranges(
        (case_dir / "reference.patch").read_text(encoding="utf-8"), expected_file["path"]
    )

    assert derived, "every seed case has a behavioural hunk"
    assert len(derived) == len(authored)
    for start, end in derived:
        assert any(low <= start and end <= high for low, high in authored), (
            f"{derived} is neither equal to nor inside {authored}"
        )


@pytest.mark.parametrize(
    ("label", "patch", "path", "expected"),
    [
        # Diff content is not self-describing. Removing a line that itself starts
        # with "--" (an ordinary SQL comment) yields a body line starting with
        # "---", which a prefix-dispatching parser reads as a file header and
        # then silently drops every remaining range for that file.
        (
            "removed line beginning with --",
            "--- a/app.sql\n+++ b/app.sql\n@@ -10,3 +10,3 @@\n header\n"
            "--- legacy comment\n+-- fixed comment\n footer\n",
            "app.sql",
            [(11, 11)],
        ),
        (
            "added line beginning with ++",
            "--- a/b.c\n+++ b/b.c\n@@ -5,3 +5,3 @@\n x\n-old\n++inc\n y\n",
            "b.c",
            [(6, 6)],
        ),
        (
            "removed line that looks like a hunk header",
            "--- a/d.py\n+++ b/d.py\n@@ -3,3 +3,3 @@\n a\n-@@ -1,1 +1,1 @@\n+fixed\n b\n",
            "d.py",
            [(4, 4)],
        ),
        (
            "file isolation survives a tricky body",
            "--- a/one.sql\n+++ b/one.sql\n@@ -1,3 +1,3 @@\n x\n--- c\n+-- c\n y\n"
            "--- a/two.py\n+++ b/two.py\n@@ -40,3 +40,3 @@\n a\n-gone\n+here\n b\n",
            "two.py",
            [(41, 41)],
        ),
        (
            "no-newline marker does not shift numbering",
            "--- a/e.py\n+++ b/e.py\n@@ -1,2 +1,2 @@\n-a\n\\ No newline at end of file\n+b\n c\n",
            "e.py",
            [(1, 1)],
        ),
        (
            "hunk header without lengths",
            "--- a/f.py\n+++ b/f.py\n@@ -7 +7 @@\n-x\n+y\n",
            "f.py",
            [(7, 7)],
        ),
    ],
)
def test_hunk_bodies_are_never_mistaken_for_structure(label, patch, path, expected):
    """Only the `@@` lengths say where a hunk ends -- never the body's prefix."""
    assert _ranges(patch, path) == expected, label


def test_unicode_line_separators_in_content_do_not_shift_numbering():
    """Only LF ends a diff record.

    str.splitlines() also breaks on VT, FF, NEL, U+2028/U+2029 and a lone CR --
    all of which can sit inside the CONTENT of a source line. One would turn a
    single diff line into two, consuming an extra line of the hunk budget and
    silently shifting every derived number after it.
    """
    for separator in ("\x0b", "\x0c", "\x1c", "\x1d", "\x85", " ", " ", "\r"):
        patch = (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -10,4 +10,4 @@\n"
            " context\n"
            f'-value = "before{separator}tail"\n'
            f'+value = "after{separator}tail"\n'
            " trailing\n"
        )
        assert _ranges(patch, "app.py") == [(11, 11)], repr(separator)


@pytest.mark.parametrize(
    ("label", "separator"),
    [
        ("vertical tab", "\x0b"),
        ("form feed", "\x0c"),
        ("next line", "\x85"),
        ("line separator", " "),
        ("paragraph separator", " "),
        ("lone carriage return", "\r"),
    ],
)
def test_unicode_separators_in_content_do_not_shift_line_numbers(label, separator):
    """Only LF ends a diff record.

    str.splitlines() also breaks on these, and every one of them can appear
    inside the CONTENT of a source line. One such character turned a single diff
    line into two, so the parser consumed an extra line from the hunk budget and
    every derived number after it was silently wrong.
    """
    patch = (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -10,3 +10,3 @@\n"
        " context\n"
        f"-old{separator}payload\n"
        "+new\n"
        " trailing\n"
    )
    assert _ranges(patch, "a.py") == [(11, 11)], label


def test_a_binary_creation_is_detected_without_diff_headers():
    """git emits no ---/+++ pair for a binary file.

    A fix that adds an image alongside its code change produces only
    `new file mode` and a "Binary files ... differ" line, so a scan that looks
    solely for `--- /dev/null` misses the creation. Ground truth would then be
    pointed at a path that does not exist in the buggy tree the reviewer sees,
    and the imported case would fail pack validation.
    """
    patch = (
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "new file mode 100644\n"
        "index 0000000..1b2c3d4\n"
        "Binary files /dev/null and b/assets/logo.png differ\n"
    )

    assert files_created_by(patch) == {"assets/logo.png"}


def test_a_quoted_path_creation_is_decoded():
    """git C-quotes path names containing spaces or non-ASCII bytes."""
    patch = (
        'diff --git "a/src/new file.py" "b/src/new file.py"\n'
        "new file mode 100644\n"
        "--- /dev/null\n"
        '+++ "b/src/new file.py"\n'
        "@@ -0,0 +1 @@\n"
        "+x = 1\n"
    )

    assert files_created_by(patch) == {"src/new file.py"}


def test_a_plain_modification_is_not_reported_as_created():
    """The `new file mode` signal must not fire on an ordinary edit."""
    patch = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old = 1\n"
        "+new = 2\n"
    )

    assert files_created_by(patch) == set()


def test_a_deletion_is_not_reported_as_created():
    patch = (
        "diff --git a/src/gone.py b/src/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/src/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-x = 1\n"
    )

    assert files_created_by(patch) == set()
