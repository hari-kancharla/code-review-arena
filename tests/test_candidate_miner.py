"""Mining historical fixes that are shaped like execution-backed review cases.

The importer turns one buggy/fixed commit pair into a pack but infers no
semantics, so every case costs a hand-written spec -- which is why the shipped
real-fix pack has three cases. Mining supplies the mechanical half: it finds the
pairs and fills in everything Git can establish, leaving prose to a human.
"""

import subprocess
from pathlib import Path

import pytest

from arena.core.errors import ImportFixError
from arena.importer.candidate_miner import is_source_path, is_test_path, mine_candidates
from arena.importer.git_objects import open_repo


@pytest.mark.parametrize(
    ("path", "test", "source"),
    [
        ("tests/test_foo.py", True, False),
        ("src/app/test_helper.py", True, False),
        ("src/__tests__/widget.ts", True, False),
        ("lib/Foo.spec.ts", True, False),
        ("app/FooTest.java", True, False),
        ("app/models.py", False, True),
        # "latest" contains "test" but is not a test directory: matching whole
        # path components, not substrings, is what keeps this from misfiring.
        ("src/latest/thing.py", False, True),
        ("README.md", False, False),
        ("pyproject.toml", False, False),
        ("Makefile", False, False),
        ("Dockerfile", False, False),
        # Case-insensitive "ends with test/spec" turned these into test files,
        # which broke the selection rule both ways: a commit touching no test
        # was proposed as a candidate, and a real fix to one could never be
        # mined because its source disappeared into the test list.
        ("src/latest.py", False, True),
        ("api/latest.go", False, True),
        ("lib/inspec.rb", False, True),
        ("app/contest.py", False, True),
        ("x/greatest.rb", False, True),
        ("lib/respec.ts", False, True),
        # ...while the real conventions still match.
        ("app/OrderServiceTest.java", True, False),
        ("p/ParserSpec.scala", True, False),
        ("conftest.py", True, False),
        ("pkg/foo_test.go", True, False),
    ],
)
def test_path_classification(path, test, source):
    assert is_test_path(path) is test
    assert is_source_path(path) is source


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


@pytest.fixture
def history(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")

    # A real fix: source AND tests together -> a candidate.
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests" / "test_app.py").write_text("def test_x():\n    assert add(2, 3) == 5\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "Fix addition and cover it")

    # Docs only -> not a candidate.
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "Docs")

    # Source only, no test change -> no fail-to-pass signal, not a candidate.
    (repo / "app.py").write_text("def add(a, b):\n    return a + b  # tidy\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "Tidy")
    return repo


def test_only_source_plus_test_commits_are_candidates(history):
    with open_repo(history) as repo:
        found = mine_candidates(repo, limit=50)

    # Exactly the one commit that changed source and tests together.
    assert len(found) == 1
    candidate = found[0]
    assert candidate.subject == "Fix addition and cover it"
    assert candidate.source_paths == ["app.py"]
    assert candidate.test_paths == ["tests/test_app.py"]
    # Its parent is the buggy state the reviewer would be shown.
    assert candidate.buggy_commit != candidate.fixed_commit


def test_sprawling_commits_are_skipped(history):
    """A single seeded defect, not a refactor: bound how much a case may change."""
    with open_repo(history) as repo:
        assert mine_candidates(repo, limit=50, max_files=1) == []


def test_scaffolded_spec_actually_loads_as_an_import_spec(history, tmp_path):
    """The scaffold must parse as emitted, or it saves nobody any work.

    It first shipped with invented fields (`buggy_commit`, `test_paths`,
    `ground_truth` nested under `case`) and TODO strings in places the schema
    constrains (`severity`), so `load_import_spec` rejected every one of them
    with nine errors. Commits and the source label are `import-fix` ARGUMENTS,
    not spec fields, so they belong in the command the scaffold hands back.
    """
    import yaml

    from arena.cli.commands.mine_fixes import _spec_scaffold
    from arena.importer.import_spec import load_import_spec

    with open_repo(history) as repo:
        candidates = mine_candidates(repo, limit=50)
    assert candidates

    scaffold = _spec_scaffold(candidates[0], source_label="owner/repository")

    # The commit pair travels in the command, where import-fix expects it.
    assert candidates[0].buggy_commit in scaffold["import_command"]
    assert candidates[0].fixed_commit in scaffold["import_command"]

    spec_file = tmp_path / "import-spec.yaml"
    spec_file.write_text(yaml.safe_dump(scaffold["spec"], sort_keys=False), encoding="utf-8")

    spec = load_import_spec(spec_file)  # must not raise

    assert spec.source_paths == candidates[0].source_paths
    assert spec.ground_truth.primary_bug.files[0].path == candidates[0].source_paths[0]


def test_scaffold_carries_derived_line_ranges(history, tmp_path):
    """The ranges come from the fix diff, not a `1, 1` stub."""
    from arena.cli.commands.mine_fixes import _spec_scaffold

    with open_repo(history) as repo:
        candidates = mine_candidates(repo, limit=50)

    scaffold = _spec_scaffold(candidates[0], source_label=None)
    ranges = scaffold["spec"]["ground_truth"]["primary_bug"]["files"][0]["line_ranges"]

    assert ranges == [{"start": 2, "end": 2}]  # the rewritten `return a - b`


def test_commits_touching_docs_or_config_are_skipped_by_default(tmp_path):
    """import-fix rejects a commit whose paths it cannot all classify.

    Proposing one would send the author off to write a whole semantic spec for
    something that cannot be imported, so such commits are skipped unless asked
    for -- and then the offending paths are named rather than silently dropped.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")

    # Source + tests + a doc: shaped like a fix, but not importable as-is.
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests" / "test_app.py").write_text("def test_x():\n    assert add(2, 3) == 5\n")
    (repo / "README.md").write_text("notes\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "Fix addition and mention it")

    with open_repo(repo) as opened:
        assert mine_candidates(opened, limit=50) == []

        allowed = mine_candidates(opened, limit=50, allow_unclassified=True)

    assert len(allowed) == 1
    assert allowed[0].unclassified_paths == ["README.md"]


def test_history_is_not_read_one_commit_at_a_time(tmp_path, history):
    """Paths come from windowed `git log --name-only`, not a diff-tree per commit.

    The per-commit form cost a process spawn each, so the CLI's advertised
    maximum --limit meant tens of thousands of subprocesses against a silent
    terminal. The walk reads in windows rather than one call because git output is
    capped, so the invariant is that cost does not scale with commits -- not that
    exactly one call is made.
    """
    from arena.importer import candidate_miner

    calls: list[list[str]] = []
    original = candidate_miner._git

    def counting(repo, args, reason):  # type: ignore[no-untyped-def]
        calls.append(args)
        return original(repo, args, reason)

    candidate_miner._git = counting
    try:
        with open_repo(history) as repo:
            found = mine_candidates(repo, limit=50)
    finally:
        candidate_miner._git = original

    log_calls = [args for args in calls if args and args[0] == "log"]
    walk_calls = [args for args in calls if args and args[0] == "diff-tree" and "--raw" in args]
    # Windows, not commits: far fewer log calls than the history is deep.
    assert 1 <= len(log_calls) <= 2
    assert walk_calls == []  # no per-commit tree comparison
    # The only remaining per-item cost is one patch read per SURVIVING candidate.
    patch_calls = [args for args in calls if args and args[0] == "diff-tree" and "-p" in args]
    assert len(patch_calls) == len(found)


@pytest.mark.parametrize(
    "path",
    ["src/latest.py", "src/contest.py", "src/greatest.py", "lib/inspec.rb", "app/manifest.ts"],
)
def test_words_merely_ending_in_test_or_spec_are_not_tests(path):
    """The separator is what makes the convention a convention.

    A plain case-insensitive "ends with test/spec" reads `latest.py` as a test.
    That breaks selection in BOTH directions: a commit touching no test at all is
    proposed as a candidate, and a genuine fix to `latest.py` can never be mined
    because its only source file has been classified away.
    """
    assert is_test_path(path) is False
    assert is_source_path(path) is True


@pytest.mark.parametrize(
    "path",
    ["tests/test_a.py", "a/b_test.go", "x/Foo.spec.ts", "app/OrderServiceTest.java", "conftest.py"],
)
def test_real_test_conventions_are_still_recognised(path):
    assert is_test_path(path) is True


def test_commits_touching_unimportable_paths_are_not_proposed(tmp_path):
    """import-fix rejects a changed path that is neither source nor tests_root.

    Proposing such a commit sends the author off to write a full semantic spec
    for something that cannot be imported, so it is skipped unless asked for.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text("x = 1\n")
    (repo / "tests" / "test_app.py").write_text("def test_a():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")

    # Source + tests + a doc: the doc is neither, so import-fix could not take it.
    (repo / "app.py").write_text("x = 2\n")
    (repo / "tests" / "test_app.py").write_text("def test_a():\n    assert x == 2\n")
    (repo / "README.md").write_text("notes\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "Fix with docs")

    with open_repo(repo) as opened:
        assert mine_candidates(opened, limit=50) == []
        allowed = mine_candidates(opened, limit=50, allow_unclassified=True)

    assert len(allowed) == 1
    assert allowed[0].unclassified_paths == ["README.md"]


def test_ground_truth_skips_files_the_fix_created(tmp_path):
    """The reviewer reviews the BUGGY tree, where a fix-created file is absent.

    Pointing ground truth at one produces a case that cannot be imported: pack
    validation rejects a ground-truth path missing from `after/`.
    """
    from arena.cli.commands.mine_fixes import _ground_truth_files
    from arena.importer.candidate_miner import Candidate

    candidate = Candidate(
        fixed_commit="f",
        buggy_commit="b",
        subject="s",
        source_paths=["app/existing.py", "app/new_helper.py"],
        test_paths=["tests/test_app.py"],
        derived_line_ranges={"app/existing.py": [{"start": 4, "end": 5}]},
        created_paths=["app/new_helper.py"],
    )

    entries = _ground_truth_files(candidate, candidate.derived_line_ranges)

    assert [entry["path"] for entry in entries] == ["app/existing.py"]
    assert entries[0]["line_ranges"] == [{"start": 4, "end": 5}]


def test_ground_truth_never_emits_an_empty_file_list(tmp_path):
    """A schema-invalid scaffold helps nobody, even in the degenerate case."""
    from arena.cli.commands.mine_fixes import _ground_truth_files
    from arena.importer.candidate_miner import Candidate

    all_new = Candidate(
        fixed_commit="f",
        buggy_commit="b",
        subject="s",
        source_paths=["app/only_new.py"],
        test_paths=["tests/test_app.py"],
        created_paths=["app/only_new.py"],
    )

    entries = _ground_truth_files(all_new, {})

    assert len(entries) == 1
    assert entries[0]["path"] == "app/only_new.py"


def test_history_walk_survives_one_unreadable_commit(monkeypatch, history):
    """One oversized commit must not discard every good candidate with it.

    git output is capped, so a wide enough window overflows. A single-call walk
    made that fatal for the whole range; the windowed walk narrows, steps over the
    offending commit, and keeps going.
    """
    from arena.core.errors import ImportFixError
    from arena.importer import candidate_miner

    real = candidate_miner._log_chunk
    seen: list[int] = []

    def flaky(repo, revision, *, skip, count):
        seen.append(count)
        # The very first wide window blows the cap, exactly as a sprawling commit
        # would; everything after it is readable.
        if count > 1 and not seen[:-1]:
            raise ImportFixError("git_failed", "output too large")
        return real(repo, revision, skip=skip, count=count)

    monkeypatch.setattr(candidate_miner, "_log_chunk", flaky)

    with open_repo(history) as repo:
        found = candidate_miner.mine_candidates(repo, limit=50)

    assert any(width == 1 for width in seen), "walk should narrow after an overflow"
    assert [item.subject for item in found] == ["Fix addition and cover it"]


def test_a_persistently_failing_history_walk_terminates(tmp_path, history, monkeypatch):
    """A broken repository must raise, not spin.

    The walk steps over a commit whose own listing will not fit, which is right
    for one sprawling commit. When EVERY window fails -- a corrupt object, an
    unreadable repository, a git that is not there -- no record is ever appended,
    so the loop's `len(records) < limit` condition can never become false and the
    stepping continues forever against a silent terminal.
    """
    from arena.importer import candidate_miner

    calls = 0

    def always_failing(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ImportFixError("git_failed", "simulated unreadable repository")

    monkeypatch.setattr(candidate_miner, "_log_chunk", always_failing)

    with open_repo(history) as repo:
        with pytest.raises(ImportFixError):
            candidate_miner._commits(repo, limit=500, revision="HEAD")

    # Bounded by the step budget, not by `limit`: each stepped-over commit costs
    # two calls (the wide window, then the narrowed one that identifies it).
    assert calls <= 2 * (candidate_miner._MAX_UNREADABLE_COMMITS + 2)


def test_a_separator_byte_in_a_subject_cannot_inject_changed_paths(tmp_path):
    """git log fields are separated by U+001F, which a subject may contain.

    Parsing left-to-right with a maxsplit leaves the remainder of such a subject
    attached to the NUL-separated path list, so every fragment of prose after the
    separator is read as a changed path. That is enough to give a docs-only commit
    an imaginary source file and have it proposed as a review case.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "src" / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "def test_f():\n    assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")

    (repo / "src" / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "def test_f():\n    assert f() == 2\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    # A subject carrying the field separator, followed by text shaped like a path.
    _git(repo, "commit", "-qm", "Fix f\x1fsrc/imaginary.py")

    with open_repo(repo) as opened:
        candidates = mine_candidates(opened, limit=10)

    assert len(candidates) == 1
    assert candidates[0].source_paths == ["src/app.py"]
    assert "src/imaginary.py" not in candidates[0].source_paths
    assert "src/imaginary.py" not in candidates[0].unclassified_paths
