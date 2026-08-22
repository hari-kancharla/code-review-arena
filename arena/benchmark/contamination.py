"""Contamination scan: does the presented case reveal its own answer?

A case leaks when the surfaces a reviewer sees (added or removed diff lines,
comments in the after tree, or test names that show up in pre-patch test
output) contain the curated ground-truth vocabulary (must_mention, concepts,
acceptable_fix_keywords). Leaks make detection scores measure reading
comprehension instead of code review.

Both sides of the diff are review surface. In an inverse RealFix review diff
(fixed -> buggy), the removed lines are the historical fixed implementation, so
a removed line can hand the reviewer the defect, its cause, or its repair just
as an added line can. Added and removed hits are reported under distinct
surfaces so they can be judged separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from arena.benchmark.diff_loader import load_diff
from arena.benchmark.snapshot import snapshot_pack
from arena.core.bounded_io import read_text_capped
from arena.core.limits import PACK_FILE_BYTES
from arena.core.models import BenchmarkCase
from arena.validators.source_text import extract_comments

_TEXT_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".sql")
_MIN_PHRASE_LENGTH = 3

# Reported when a curated phrase cannot be checked at all. Not a leak: a hole in
# the leak guarantee, which is worse, because the pack otherwise reports clean.
UNSCANNABLE_PHRASE = "unscannable_phrase"


@dataclass(frozen=True)
class ContaminationWarning:
    case_id: str
    surface: str  # diff_added_line | diff_removed_line | after_comment | test_name
    phrase: str
    location: str

    def render(self) -> str:
        if self.surface == UNSCANNABLE_PHRASE:
            return f"{self.case_id}: {self.phrase!r} cannot be scanned for leaks ({self.location})"
        return f"{self.case_id}: {self.phrase!r} leaks via {self.surface} ({self.location})"


def _phrases(case: BenchmarkCase) -> tuple[set[str], dict[str, str]]:
    """The curated vocabulary to scan for, and the phrases that cannot be scanned.

    A phrase this scanner cannot check is more dangerous than one that leaks,
    because the pack still reports clean: the operator is told the case holds no
    leak when in truth one phrase was never looked at. Two ways that happens, and
    both are reported rather than dropped:

    - shorter than ``_MIN_PHRASE_LENGTH``, which exists to keep one- and
      two-character tokens from matching everything, and
    - not matchable by ``_phrase_in`` even against itself. A single-token phrase
      is matched between word boundaries, so one that begins or ends with a
      non-word character (``\\Z``, ``$``, ``--``) has no boundary to anchor
      against and can never match any text at all.

    Self-matching is the exact test for the second case: a phrase that cannot be
    found inside a copy of itself cannot be found anywhere.
    """
    phrases: set[str] = set()
    unscannable: dict[str, str] = {}
    for bug in case.ground_truth.bugs:
        for phrase in (*bug.must_mention, *bug.concepts, *bug.acceptable_fix_keywords):
            cleaned = phrase.strip()
            if not cleaned:
                continue
            if len(cleaned) < _MIN_PHRASE_LENGTH:
                unscannable[cleaned] = (
                    f"shorter than {_MIN_PHRASE_LENGTH} characters, so the scan skips it"
                )
            elif not _phrase_in(cleaned, cleaned):
                unscannable[cleaned] = (
                    "begins or ends with a non-word character, so the word-boundary "
                    "match can never find it"
                )
            else:
                phrases.add(cleaned)
    return phrases, unscannable


def _phrase_in(phrase: str, text: str) -> bool:
    if " " in phrase:
        return phrase.casefold() in text.casefold()
    return bool(re.search(rf"(?i)\b{re.escape(phrase)}\b", text))


def _added_lines(diff: str) -> list[tuple[int, str]]:
    return [
        (number, line[1:])
        for number, line in enumerate(diff.splitlines(), start=1)
        if line.startswith("+") and not line.startswith("+++")
    ]


def _removed_lines(diff: str) -> list[tuple[int, str]]:
    # Mirror of _added_lines for the deletion side: content lines start with a
    # single "-" but not the "---" file header. Hunk headers ("@@") and context
    # lines (leading space) are excluded by the prefix test.
    return [
        (number, line[1:])
        for number, line in enumerate(diff.splitlines(), start=1)
        if line.startswith("-") and not line.startswith("---")
    ]


def scan_case(case: BenchmarkCase) -> list[ContaminationWarning]:
    assert case.case_dir is not None
    warnings: list[ContaminationWarning] = []
    # Sort phrases so warning order is deterministic when several phrases match
    # the same line, independent of set hash ordering.
    scannable, unscannable = _phrases(case)
    phrases = sorted(scannable)
    for phrase, reason in sorted(unscannable.items()):
        warnings.append(ContaminationWarning(case.id, UNSCANNABLE_PHRASE, phrase, reason))

    diff = load_diff(case.case_dir / case.input.diff)
    for number, content in _added_lines(diff):
        for phrase in phrases:
            if _phrase_in(phrase, content):
                warnings.append(
                    ContaminationWarning(
                        case.id, "diff_added_line", phrase, f"{case.input.diff}:{number}"
                    )
                )
    for number, content in _removed_lines(diff):
        for phrase in phrases:
            if _phrase_in(phrase, content):
                warnings.append(
                    ContaminationWarning(
                        case.id, "diff_removed_line", phrase, f"{case.input.diff}:{number}"
                    )
                )

    after_dir = case.case_dir / case.input.after_dir
    for path in sorted(after_dir.rglob("*")):
        if not path.is_file() or not path.name.endswith(_TEXT_SUFFIXES):
            continue
        relative = path.relative_to(case.case_dir).as_posix()
        source, _ = read_text_capped(path, PACK_FILE_BYTES, label="pack file")
        comments = extract_comments(path.name, source)
        for comment in comments:
            for phrase in phrases:
                if _phrase_in(phrase, comment):
                    warnings.append(
                        ContaminationWarning(case.id, "after_comment", phrase, relative)
                    )

    tests_dir = case.case_dir / (case.input.tests_dir or "tests")
    if case.input.tests_dir and tests_dir.is_dir():
        for path in sorted(tests_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(case.case_dir).as_posix()
            names = [path.name]
            if path.suffix == ".py":
                source, _ = read_text_capped(path, PACK_FILE_BYTES, label="pack file")
                names.extend(re.findall(r"def\s+(test_\w+)", source))
            for name in names:
                for phrase in phrases:
                    if _phrase_in(phrase, name.replace("_", " ")) or _phrase_in(phrase, name):
                        warnings.append(
                            ContaminationWarning(
                                case.id, "test_name", phrase, f"{relative}::{name}"
                            )
                        )
    return warnings


def scan_benchmark(benchmark_dir: Path) -> list[ContaminationWarning]:
    warnings: list[ContaminationWarning] = []
    # Scan the immutable snapshot, not the mutable source.
    with snapshot_pack(benchmark_dir) as snapshot:
        for case in snapshot.load():
            warnings.extend(scan_case(case))
    return warnings
