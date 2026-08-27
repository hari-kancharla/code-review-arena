"""Derive candidate ground-truth line ranges from a fix patch.

A case's `ground_truth.files[].line_ranges` point at where the defect lives in
the BUGGY tree (`after/`), which is exactly the old side of `reference.patch`
(after/ + reference.patch == fixed). Transcribing those numbers by hand across a
large diff is tedious and easy to get wrong, so this derives them.

The rule, and why it is this rule: only hunks that BOTH remove and add lines are
treated as the defect. A hunk that only deletes is cleanup (an import that became
unused once the real fix landed); a hunk that only adds is supporting code (a new
helper, a new import). Checked against three human-authored historical-fix cases
(now versioned in the realfix-benchmark dataset), this reproduces the authored
ranges exactly for attrs and click, and for rich yields (703, 704) where the
author wrote (699, 705)
-- strictly inside it, and therefore *more* forgiving rather than wrong, because
`line_match_quality` awards "full" when a finding CONTAINS the expected range and
counts any overlap as localized. Erring narrow keeps a good reviewer from being
downgraded; erring wide would punish one.

This is a starting point for a human, not an answer. `arena mine-fixes` emits it
for confirmation precisely because which change constitutes "the bug" is a
semantic judgement the importer refuses to make on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Old-side start/length. The new side is irrelevant here: ground truth addresses
# the buggy tree.
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class LineRange:
    start: int
    end: int

    def as_spec(self) -> dict[str, int]:
        return {"start": self.start, "end": self.end}


def _diff_lines(patch_text: str) -> list[str]:
    """Split a patch on real diff line terminators only.

    NOT str.splitlines(): that also breaks on VT, FF, NEL, U+2028/U+2029 and a
    lone CR, any of which can appear inside the CONTENT of a source line. One
    such character would turn a single diff line into two, so the parser would
    consume an extra line from the hunk budget and silently shift every derived
    line number after it. A unified diff separates records with LF, with CRLF
    payloads showing up as a trailing CR that is part of the content.
    """
    return patch_text.split("\n")


def _unquote(path: str) -> str:
    """Decode git's C-style quoting, which it applies to unusual path names.

    A path containing a space, a quote, a control character or a non-ASCII byte
    is emitted wrapped in double quotes with C escapes (and octal escapes for
    raw bytes). Read literally, such a path matches nothing, so a file created by
    the fix goes undetected and ground truth can be pointed at a path that does
    not exist in the buggy tree. Anything that fails to decode is returned
    unchanged rather than raising: this feeds a suggestion, not a gate.
    """
    if len(path) < 2 or not path.startswith('"') or not path.endswith('"'):
        return path
    try:
        return (
            path[1:-1].encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
        )
    except (UnicodeDecodeError, UnicodeEncodeError):
        return path


def _file_of(header: str) -> str:
    """The repo-relative path from a `---`/`+++` header."""
    path = _unquote(header.split("\t", 1)[0].strip())
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def files_created_by(patch_text: str) -> set[str]:
    """Paths this patch creates, i.e. absent from the pre-image.

    In a reverse-fix case the pre-image is the BUGGY tree the reviewer reviews, so
    a file the fix created does not exist there. Ground truth must not point at
    one: the bug cannot live in a file that is not yet present, and pack
    validation rejects a ground-truth path missing from `after/`.

    Two signals, because one is not enough. A text creation shows
    `--- /dev/null`, but a BINARY creation has no `---`/`+++` pair at all -- git
    emits `Binary files /dev/null and b/logo.png differ` instead -- so the header
    scan alone silently misses it. `new file mode` is recorded for both, and is
    read against the path from the enclosing `diff --git` header.
    """
    created: set[str] = set()
    pending_new_file = False
    current: str | None = None
    for line in _diff_lines(patch_text):
        if line.startswith("diff --git "):
            current = _git_header_path(line)
            pending_new_file = False
            continue
        if line.startswith("new file mode ") and current is not None:
            created.add(current)
            continue
        if line.startswith("--- "):
            pending_new_file = _file_of(line[4:]) == "/dev/null"
            continue
        if line.startswith("+++ "):
            if pending_new_file:
                path = _file_of(line[4:])
                if path != "/dev/null":
                    created.add(path)
            pending_new_file = False
    return created


def _git_header_path(line: str) -> str | None:
    """The post-image path from a `diff --git a/x b/x` line.

    Taken from the b-side, which is the path the change results in. Returns None
    when the header cannot be read, so a malformed line contributes nothing
    rather than a wrong path.
    """
    rest = line[len("diff --git ") :].strip()
    if rest.startswith('"'):
        # Quoted paths are space-safe but must be split on the quote boundary.
        closing = rest.find('" "', 1)
        if closing == -1:
            return None
        return _file_of(rest[closing + 2 :])
    marker = rest.find(" b/")
    if marker == -1:
        return None
    return _file_of(rest[marker + 1 :])


def derive_line_ranges(patch_text: str, path: str) -> list[LineRange]:
    """Candidate ranges in the buggy file for `path`, in hunk order.

    Returns an empty list when the patch only adds or only deletes for this file:
    there is then no behavioural change to point at, and inventing a range would
    be worse than saying nothing.

    Hunk lengths from the `@@` header are tracked so that `---`/`+++` are read as
    file headers only OUTSIDE a hunk. Content is not self-describing: removing a
    line that itself begins with `--` (an ordinary SQL comment) produces a body
    line beginning with `---`, and a parser that dispatches on that prefix alone
    silently drops the rest of the file's ranges.
    """
    ranges: list[LineRange] = []
    in_file = False
    old_line = 0
    old_remaining = 0
    new_remaining = 0
    removed: list[int] = []
    added = 0

    def flush() -> None:
        # Both sides changed: a rewrite of behaviour, which is the defect.
        if removed and added:
            ranges.append(LineRange(start=min(removed), end=max(removed)))

    for line in _diff_lines(patch_text):
        inside_hunk = old_remaining > 0 or new_remaining > 0
        if not inside_hunk:
            if line.startswith("--- "):
                flush()
                removed, added = [], 0
                in_file = _file_of(line[4:]) == path
                continue
            if line.startswith("+++ "):
                # A creation shows `--- /dev/null`; trust whichever side names a
                # real path.
                candidate = _file_of(line[4:])
                if candidate != "/dev/null":
                    in_file = in_file or candidate == path
                continue
            match = _HUNK.match(line)
            if match:
                flush()
                removed, added = [], 0
                old_line = int(match.group(1))
                old_remaining = int(match.group(2) or 1)
                new_remaining = int(match.group(4) or 1)
                continue
            # Anything else between hunks (index/diff/mode lines) is metadata.
            continue

        # Inside a hunk: the first character is the marker, and the rest is
        # content that may look like anything at all.
        if line.startswith("-"):
            if in_file:
                removed.append(old_line)
            old_line += 1
            old_remaining -= 1
        elif line.startswith("+"):
            if in_file:
                added += 1
            new_remaining -= 1
        elif line.startswith("\\"):
            # "\ No newline at end of file" annotates the previous line.
            continue
        else:
            # Context, including a bare empty line for an empty context line.
            old_line += 1
            old_remaining -= 1
            new_remaining -= 1
    flush()
    return ranges
