"""Find commit pairs in a local repository that could become review cases.

`arena import-fix` turns one known buggy/fixed commit pair into a pack, but it
deliberately infers no semantics, so every case costs a hand-written spec. That
is the reason the shipped real-fix pack has three cases while comparable
benchmarks carry hundreds: the wall is not the importer, it is finding the pairs
and filling in everything that *is* mechanically derivable.

This module does the finding. It applies the selection rule that scaled
SWE-bench: a commit is a candidate when it changes BOTH a test path and a
non-test source path, because that pair is what makes a fail-to-pass check
possible -- the tests the fix added are exactly the ones expected to fail on the
parent and pass on the fix. Its parent is then the buggy state.

What this deliberately does NOT do:

- It never claims a candidate is a valid case. Only execution decides that, and
  the harness already has the decider: `arena certify-pack` runs baseline-fails,
  reference-passes and mutation. Mining proposes; certification disposes.
- It never infers semantics (title, category, severity, ground-truth concepts).
  Those still come from a human, exactly as `import_spec` requires. Mining fills
  in the mechanical half of the spec so the human writes prose, not paths.
- It never runs repository code, checks out, fetches, or reads the working tree.
  It walks committed objects through the same isolated Git context the importer
  uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from arena.core.errors import ImportFixError
from arena.importer.git_objects import Repo, _git
from arena.importer.line_ranges import derive_line_ranges, files_created_by

# Commits per `git log` call. Bounded so one window's output stays well under
# limits.GIT_OUTPUT_BYTES; the walk narrows further on overflow.
_LOG_WINDOW_COMMITS = 200

# How many individually unreadable commits the walk will step over before it
# concludes the repository, not one commit, is the problem.
_MAX_UNREADABLE_COMMITS = 64

# A path is a test path when any component looks like a test location, or the
# filename follows a common test naming convention. Matching on components (not
# a substring of the whole path) keeps a directory such as `src/latest/` from
# being read as a test just because it contains the letters "test".
_TEST_DIR_NAMES = frozenset({"test", "tests", "testing", "spec", "specs", "__tests__"})
# Delimiter-anchored conventions, case-insensitively: `test_foo`, `foo_test`,
# `foo.spec`. The separator is what makes these safe -- without it, a plain
# case-insensitive "ends with test/spec" turns `latest`, `contest`, `greatest`
# and `inspec` into test files, which breaks the selection rule in both
# directions: a commit touching no test at all is proposed as a candidate, and a
# genuine fix to `latest.py` can never be mined because its source vanishes.
_TEST_FILE_DELIMITED = re.compile(
    r"\A(test_.*|tests_.*|.*[._](test|tests|spec|specs)|conftest)\Z",
    re.IGNORECASE,
)
# CamelCase conventions (`OrderServiceTest.java`, `ParserSpec.scala`) must stay
# CASE-SENSITIVE for exactly the same reason.
_TEST_FILE_CAMEL = re.compile(r"\A.+(Test|Tests|Spec|Specs)\Z")
# Files that are neither source nor test for our purposes: changing only these
# alongside tests does not describe a defect a reviewer could have caught.
_NON_SOURCE_SUFFIXES = frozenset(
    {".md", ".rst", ".txt", ".cfg", ".ini", ".toml", ".yaml", ".yml", ".json", ".lock"}
)
# Build and project files carry no suffix, so the suffix rule alone lets them
# through and a docs/tooling commit is proposed as a defect to review.
_NON_SOURCE_NAMES = frozenset(
    {
        "makefile",
        "dockerfile",
        "license",
        "notice",
        "codeowners",
        "changelog",
        "readme",
        ".gitignore",
        ".dockerignore",
        ".gitattributes",
    }
)


def is_test_path(path: str) -> bool:
    """Whether a repo-relative path names a test."""
    pure = PurePosixPath(path)
    if any(part.lower() in _TEST_DIR_NAMES for part in pure.parts[:-1]):
        return True
    stem = pure.stem
    return bool(_TEST_FILE_DELIMITED.match(stem) or _TEST_FILE_CAMEL.match(stem))


def is_source_path(path: str) -> bool:
    """Whether a path is production source: not a test, not docs/config."""
    if is_test_path(path):
        return False
    pure = PurePosixPath(path)
    if pure.name.lower() in _NON_SOURCE_NAMES:
        return False
    if pure.suffix.lower() in _NON_SOURCE_SUFFIXES:
        return False
    # No suffix at all and not a known build file: a script or binary, which is
    # not a defect surface a diff reviewer is asked to reason about here.
    return bool(pure.suffix)


@dataclass
class Candidate:
    """A buggy/fixed commit pair that is shaped like a review case."""

    fixed_commit: str
    buggy_commit: str
    subject: str
    source_paths: list[str] = field(default_factory=list)
    test_paths: list[str] = field(default_factory=list)
    # Candidate ground-truth ranges per source path, derived from the fix diff.
    # A suggestion for a human to confirm, never an assertion (see line_ranges).
    derived_line_ranges: dict[str, list[dict[str, int]]] = field(default_factory=dict)
    # Changed paths that are neither source nor test (docs, config). import-fix
    # rejects a commit carrying these, so they are surfaced rather than dropped.
    unclassified_paths: list[str] = field(default_factory=list)
    # Source paths the FIX creates. They do not exist in the buggy tree the
    # reviewer sees, so ground truth cannot point at them.
    created_paths: list[str] = field(default_factory=list)

    @property
    def changed_file_count(self) -> int:
        return len(self.source_paths) + len(self.test_paths)


def _log_chunk(
    repo: Repo, revision: str, *, skip: int, count: int
) -> list[tuple[str, str, list[str], set[str]]]:
    """One bounded window of history, parsed."""
    out = _git(
        repo,
        [
            "log",
            f"--skip={skip}",
            f"--max-count={count}",
            "--format=%x1e%H%x1f%P%x1f%s%x1f",
            "--name-only",
            "--no-renames",
            "--no-notes",
            "-z",
            revision,
        ],
        "git_failed",
    )
    records: list[tuple[str, str, list[str], set[str]]] = []
    for raw in out.decode("utf-8", errors="replace").split("\x1e"):
        if not raw.strip("\x00\n"):
            continue
        # Four fields: hash, parents, subject, then the NUL-separated path list.
        # The path list is taken from the RIGHT of the last separator and the
        # header from the left, because a commit SUBJECT may itself contain the
        # separator byte. Splitting left-to-right with a maxsplit would leave that
        # remainder attached to the path list, and every fragment of prose after it
        # would be read as a changed path -- enough to make a docs-only commit look
        # like a source change and be proposed as a candidate.
        if "\x1f" not in raw:
            raise ImportFixError("git_output_invalid", "unexpected git log record")
        header, blob = raw.rsplit("\x1f", 1)
        parts = header.split("\x1f", 2)
        if len(parts) != 3:
            raise ImportFixError("git_output_invalid", "unexpected git log record")
        commit, parents, subject = parts
        # git puts a newline between the formatted header and the path list.
        paths = {item.strip("\n") for item in blob.split("\x00") if item.strip()}
        records.append((commit.strip("\n"), subject, parents.split(), paths))
    return records


def _commits(
    repo: Repo, *, limit: int, revision: str
) -> list[tuple[str, str, list[str], set[str]]]:
    """(commit, subject, parents, changed paths) newest first, from history only.

    `git log --name-only` rather than a `diff-tree` per commit: the per-commit
    form cost a process spawn each, so the CLI's own maximum --limit meant tens of
    thousands of subprocesses and tens of minutes against a silent terminal.

    Read in windows rather than one call, because git output is capped (see
    limits.GIT_OUTPUT_BYTES) and a wide enough window exceeds it. A single call
    made that cap fatal: one sprawling commit anywhere in range aborted the entire
    walk and discarded every good candidate with it. On overflow the window
    narrows to isolate the offending commit, which is then stepped over.
    """
    records: list[tuple[str, str, list[str], set[str]]] = []
    skip = 0
    step = _LOG_WINDOW_COMMITS
    stepped_over = 0
    while len(records) < limit:
        want = min(step, limit - len(records))
        try:
            chunk = _log_chunk(repo, revision, skip=skip, count=want)
        except ImportFixError:
            if want > 1:
                step = 1  # narrow, to find which commit is the problem
                continue
            # This one commit's own listing will not fit. Stepping over it keeps
            # the rest of the window usable -- but only for a bounded number of
            # commits. Stepping forever is what a broken repository, an
            # unreadable object or a missing git looks like from here: every
            # window fails, no record is ever appended, and the loop's own
            # condition can never become false. Give up and report instead of
            # spinning silently against a terminal that shows nothing.
            stepped_over += 1
            if stepped_over > _MAX_UNREADABLE_COMMITS:
                raise
            skip += 1
            step = _LOG_WINDOW_COMMITS
            continue
        records.extend(chunk)
        skip += want
        step = _LOG_WINDOW_COMMITS
        if len(chunk) < want:
            break  # git returned a short window: history is exhausted
    return records[:limit]


def _derive_ranges(
    repo: Repo, buggy: str, fixed: str, sources: list[str]
) -> tuple[dict[str, list[dict[str, int]]], list[str]]:
    """Candidate ground-truth ranges per source path, from the fix diff.

    Textual diff is used deliberately here, unlike the tree-object comparison
    that decides which paths changed: this only produces a suggestion a human
    confirms, so it is not a classification authority and cannot mislead one.
    A path with no behavioural hunk is simply absent rather than guessed.
    """
    patch = _git(
        repo,
        ["diff-tree", "-p", "--no-renames", "-r", "--no-color", buggy, fixed, "--", *sources],
        "git_failed",
    ).decode("utf-8", errors="replace")
    created = files_created_by(patch)
    derived: dict[str, list[dict[str, int]]] = {}
    for path in sources:
        if path in created:
            continue  # no pre-image in the buggy tree to point at
        ranges = [item.as_spec() for item in derive_line_ranges(patch, path)]
        if ranges:
            derived[path] = ranges
    return derived, sorted(created & set(sources))


def mine_candidates(
    repo: Repo,
    *,
    limit: int = 200,
    revision: str = "HEAD",
    max_files: int = 12,
    allow_unclassified: bool = False,
) -> list[Candidate]:
    """Rank commits that changed both source and tests, newest history first.

    ``max_files`` keeps sprawling commits out: a review case is a single seeded
    defect, and a 40-file refactor neither reviews nor certifies cleanly. Merge
    commits are skipped because their diff against a single parent does not
    describe one change.
    """
    candidates: list[Candidate] = []
    for commit, subject, parents, changed in _commits(repo, limit=limit, revision=revision):
        if len(parents) != 1:
            continue  # a merge has no single "before"
        parent = parents[0]
        if not changed or len(changed) > max_files:
            continue
        tests = sorted(path for path in changed if is_test_path(path))
        sources = sorted(path for path in changed if is_source_path(path))
        # The SWE-bench rule: without a test change there is no fail-to-pass
        # signal, and without a source change there is no defect to review.
        if not tests or not sources:
            continue
        # Anything that is neither: docs, config, workflows. `import-fix` requires
        # every changed path to fall under a source selector or tests_root and
        # raises `changed_path_unclassified` otherwise, so proposing such a commit
        # would send the author off to write a whole semantic spec for something
        # that cannot be imported. Skipped unless explicitly asked for.
        unclassified = sorted(set(changed) - set(tests) - set(sources))
        if unclassified and not allow_unclassified:
            continue
        try:
            ranges_by_path, created_paths = _derive_ranges(repo, parent, commit, sources)
        except ImportFixError:
            # The suggestion is optional; the candidate is not. A diff too large
            # to read (or otherwise unreadable) costs this commit its derived
            # ranges, and the scaffold falls back to an explicit placeholder --
            # it must not discard every candidate already found.
            ranges_by_path, created_paths = {}, []
        candidates.append(
            Candidate(
                fixed_commit=commit,
                buggy_commit=parent,
                subject=subject.strip()[:200],
                source_paths=sources,
                test_paths=tests,
                derived_line_ranges=ranges_by_path,
                created_paths=created_paths,
                unclassified_paths=unclassified,
            )
        )
    # Smallest first: the tightest diffs make the clearest review cases and are
    # the most likely to survive certification.
    candidates.sort(key=lambda item: (item.changed_file_count, item.fixed_commit))
    return candidates
