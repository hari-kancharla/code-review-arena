"""Deterministic historical-fix importer: adversarial and integration tests.

Uses synthetic local Git repositories only (no network). Verifies reverse-fix
semantics, determinism, committed-object-only access, and safe rejection.
"""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from arena.benchmark.contamination import scan_benchmark
from arena.benchmark.dataset_validator import validate_dataset
from arena.core import limits
from arena.core.errors import ImportFixError
from arena.importer.historical_fix import import_fix

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=_ENV
    ).stdout.strip()


def _supports_sha256() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as probe:
        out = subprocess.run(
            ["git", "init", "--object-format=sha256", str(Path(probe) / "r")],
            capture_output=True,
            text=True,
        )
    return out.returncode == 0 and "unknown" not in (out.stderr or "").lower()


def _commit(
    repo: Path, files: dict[str, str], msg: str, *, modes: dict[str, int] | None = None
) -> str:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")
        if modes and rel in modes:
            os.chmod(p, modes[rel])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, name: str = "repo", object_format: str = "sha1") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", f"--object-format={object_format}")
    return repo


# A buggy->fixed pair: combine() subtracts, the fix makes it add. Ground-truth
# vocabulary is deliberately disjoint from the visible code/tests to stay clean.
_BUGGY = {"src/calc.py": "def combine(a, b):\n    return a - b\n"}
_FIXED = {"src/calc.py": "def combine(a, b):\n    return a + b\n"}
_TEST_SRC = "from src.calc import combine\n\n\ndef test_combine():\n    assert combine(2, 3) == 5\n"
_TEST = {"tests/test_calc.py": _TEST_SRC}

_SPEC = textwrap.dedent("""\
    schema_version: "1"
    pack: {version: "1", name: imported_fixes}
    case:
      id: calc_combine_sign_001
      title: Combine used subtraction
      category: correctness
      severity: high
      stack: [python]
      description: combine subtracted instead of summing.
    source_paths: [src]
    tests_root: tests
    ground_truth:
      bugs:
        - id: bug-1
          summary: wrong arithmetic operator
          files:
            - path: src/calc.py
              line_ranges: [{start: 1, end: 2}]
          concepts: [arithmetic]
          must_mention: [operator]
          acceptable_fix_keywords: [plus]
    execution: {run_tests: true, test_command: "pytest -q tests", timeout_seconds: 30}
    validation: {patch_required: true, tests_required: true}
""")

# A spec declaring two bug files: the fix modifies src/calc.py and deletes src/legacy.py.
# Both files exist in the buggy tree, so both are valid reviewable ground-truth bugs.
_SPEC_TWO_FILES = textwrap.dedent("""\
    schema_version: "1"
    pack: {version: "1", name: imported_fixes}
    case:
      id: calc_combine_sign_001
      title: Combine used subtraction
      category: correctness
      severity: high
      stack: [python]
      description: combine subtracted instead of summing.
    source_paths: [src]
    tests_root: tests
    ground_truth:
      bugs:
        - id: bug-1
          summary: wrong arithmetic operator
          files:
            - path: src/calc.py
              line_ranges: [{start: 1, end: 2}]
          concepts: [arithmetic]
          must_mention: [operator]
          acceptable_fix_keywords: [plus]
        - id: bug-2
          summary: a retired module should not ship
          files:
            - path: src/legacy.py
              line_ranges: [{start: 1, end: 1}]
          concepts: [cleanup]
          must_mention: [stale]
          acceptable_fix_keywords: [remove]
    execution: {run_tests: true, test_command: "pytest -q tests", timeout_seconds: 30}
    validation: {patch_required: true, tests_required: true}
""")


def _make(tmp_path, *, object_format="sha1", buggy=None, fixed=None, with_tests=True, modes=None):
    repo = _repo(tmp_path, object_format=object_format)
    files_b = dict(buggy or _BUGGY)
    files_f = dict(fixed or _FIXED)
    if with_tests:
        files_b.update(_TEST)
        files_f.update(_TEST)
    b = _commit(repo, files_b, "buggy", modes=modes)
    f = _commit(repo, files_f, "fix", modes=modes)
    return repo, b, f


def _spec_file(tmp_path, text=_SPEC) -> Path:
    p = tmp_path / "spec.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _run(tmp_path, repo, b, f, *, out="out", spec=None, label="acme/calc"):
    return import_fix(
        repo_path=repo,
        buggy_commit=b,
        fixed_commit=f,
        spec_path=spec or _spec_file(tmp_path),
        output=tmp_path / out,
        source_label=label,
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# --------------------------------------------------------------------------- #
# Determinism                                                                 #
# --------------------------------------------------------------------------- #


def test_deterministic_byte_identical(tmp_path):
    repo, b, f = _make(tmp_path)
    spec = _spec_file(tmp_path)
    r1 = _run(tmp_path, repo, b, f, out="a", spec=spec)
    r2 = _run(tmp_path, repo, b, f, out="b", spec=spec)
    assert r1.pack_checksum == r2.pack_checksum
    assert _tree(tmp_path / "a") == _tree(tmp_path / "b")


def test_dirty_worktree_does_not_affect_output(tmp_path):
    repo, b, f = _make(tmp_path)
    spec = _spec_file(tmp_path)
    clean = _run(tmp_path, repo, b, f, out="clean", spec=spec)
    (repo / "src" / "calc.py").write_text("GARBAGE\n")  # modify tracked file
    (repo / "untracked.py").write_text("junk\n")  # add untracked file
    dirty = _run(tmp_path, repo, b, f, out="dirty", spec=spec)
    assert clean.pack_checksum == dirty.pack_checksum
    assert _tree(tmp_path / "clean") == _tree(tmp_path / "dirty")


def test_no_volatile_data_in_provenance(tmp_path):
    repo, b, f = _make(tmp_path)
    _run(tmp_path, repo, b, f)
    text = (tmp_path / "out" / "calc_combine_sign_001" / "provenance.json").read_text()
    assert str(repo) not in text and str(tmp_path) not in text
    for forbidden in (
        "/Users",
        "/tmp",
        "/private",
        os.uname().nodename if hasattr(os, "uname") else "host",
    ):
        assert forbidden not in text


# --------------------------------------------------------------------------- #
# Commit identity                                                             #
# --------------------------------------------------------------------------- #


def test_full_sha1_ids_accepted(tmp_path):
    repo, b, f = _make(tmp_path)
    r = _run(tmp_path, repo, b, f)
    assert r.buggy_commit == b and r.fixed_commit == f and r.object_format == "sha1"


def test_abbreviated_id_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b[:10], f)
    assert e.value.reason == "abbreviated_commit_id"


def test_branch_name_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, _git(repo, "rev-parse", "--abbrev-ref", "HEAD"))
    assert e.value.reason == "abbreviated_commit_id"


def test_non_commit_object_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    tree_oid = _git(repo, "rev-parse", "HEAD^{tree}")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, tree_oid, f)
    assert e.value.reason == "invalid_commit_object"


def test_unrelated_commits_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    _git(repo, "checkout", "-q", "--orphan", "other")
    _git(repo, "rm", "-rfq", "--cached", ".")
    other = _commit(repo, {"unrelated.py": "x = 1\n"}, "orphan")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, other, f)
    assert e.value.reason == "commits_unrelated"


def test_fixed_not_descendant_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, f, b)  # swapped: buggy=F is not an ancestor of fixed=B
    assert e.value.reason == "fixed_not_descendant"


@pytest.mark.skipif(not _supports_sha256(), reason="git lacks sha256 object format")
def test_sha256_repository(tmp_path):
    repo, b, f = _make(tmp_path, object_format="sha256")
    assert len(b) == 64
    r = _run(tmp_path, repo, b, f)
    assert r.object_format == "sha256" and r.validation_ok


# --------------------------------------------------------------------------- #
# Tree safety                                                                 #
# --------------------------------------------------------------------------- #


def test_executable_source_file_supported(tmp_path):
    repo, b, f = _make(
        tmp_path,
        buggy={"src/calc.py": "def combine(a, b):\n    return a - b\n", "src/run.sh": "echo hi\n"},
        fixed={"src/calc.py": "def combine(a, b):\n    return a + b\n", "src/run.sh": "echo hi\n"},
        modes={"src/run.sh": 0o755},
    )
    r = _run(tmp_path, repo, b, f)
    assert r.validation_ok


def test_symlink_in_tree_rejected(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text("def combine(a, b):\n    return a - b\n")
    os.symlink("calc.py", repo / "src" / "link.py")
    b = _commit(repo, {}, "buggy-with-symlink")
    (repo / "src" / "calc.py").write_text("def combine(a, b):\n    return a + b\n")
    f = _commit(repo, {}, "fix")
    with pytest.raises(ImportFixError) as e:
        import_fix(
            repo_path=repo,
            buggy_commit=b,
            fixed_commit=f,
            spec_path=_spec_file(tmp_path),
            output=tmp_path / "out",
        )
    assert e.value.reason == "unsupported_tree_mode"


def test_oversized_pack_rejected(tmp_path, monkeypatch):
    repo, b, f = _make(tmp_path)
    monkeypatch.setattr(limits, "IMPORT_MAX_FILES", 1)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f)
    assert e.value.reason == "import_limit_exceeded"


# --------------------------------------------------------------------------- #
# Classification                                                              #
# --------------------------------------------------------------------------- #


def test_changed_path_outside_selection_rejected(tmp_path):
    # A change to docs/ is neither source (src) nor tests (tests): must fail loudly.
    repo, b, f = _make(
        tmp_path,
        buggy={"src/calc.py": "def combine(a, b):\n    return a - b\n", "docs/x.md": "old\n"},
        fixed={"src/calc.py": "def combine(a, b):\n    return a + b\n", "docs/x.md": "new\n"},
    )
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f)
    assert e.value.reason == "changed_path_unclassified"


def test_source_tests_overlap_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    bad = _SPEC.replace("source_paths: [src]", "source_paths: [src, tests]")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason == "source_test_overlap"


def test_source_plus_tests_change(tmp_path):
    # Both source and tests change between B and F; both classify, nothing unclassified.
    repo = _repo(tmp_path)
    one = "from src.calc import combine\n\n\ndef test_one():\n    assert combine(2, 3) == 5\n"
    two = one + "\n\ndef test_two():\n    assert combine(0, 0) == 0\n"
    b = _commit(
        repo,
        {"src/calc.py": "def combine(a, b):\n    return a - b\n", "tests/test_calc.py": one},
        "buggy",
    )
    f = _commit(
        repo,
        {"src/calc.py": "def combine(a, b):\n    return a + b\n", "tests/test_calc.py": two},
        "fix",
    )
    r = _run(tmp_path, repo, b, f, out="spt")
    assert r.union_source_file_count >= 1 and r.fixed_test_file_count >= 1
    assert "src/calc.py" in r.repair_changed_paths
    assert any(p.startswith("tests/") for p in r.test_changed_paths)


# --------------------------------------------------------------------------- #
# Generated semantics                                                         #
# --------------------------------------------------------------------------- #


def test_reverse_fix_semantics(tmp_path):
    repo, b, f = _make(tmp_path)
    _run(tmp_path, repo, b, f)
    case = tmp_path / "out" / "calc_combine_sign_001"
    assert "return a - b" in (case / "after" / "src" / "calc.py").read_text()  # buggy
    assert "return a + b" in (case / "before" / "src" / "calc.py").read_text()  # fixed
    assert (case / "tests" / "test_calc.py").exists()  # tests from fixed
    assert (case / "reference.patch").read_text().strip()
    assert (case / "pr.diff").read_text().strip()


def test_uncovered_repair_path_rejected(tmp_path):
    # The fix changes two source files but ground truth declares only one bug file.
    repo, b, f = _make(
        tmp_path,
        buggy={
            "src/calc.py": "def combine(a, b):\n    return a - b\n",
            "src/other.py": "VALUE = 1\n",
        },
        fixed={
            "src/calc.py": "def combine(a, b):\n    return a + b\n",
            "src/other.py": "VALUE = 2\n",
        },
    )
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f)
    assert e.value.reason == "semantic_change_uncovered"


def test_ground_truth_file_not_changed_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    bad = _SPEC.replace("path: src/calc.py", "path: src/missing.py")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason in {"ground_truth_file_missing", "ground_truth_file_not_changed"}


# --------------------------------------------------------------------------- #
# Integration                                                                 #
# --------------------------------------------------------------------------- #


def test_generated_pack_validates_and_is_clean(tmp_path):
    repo, b, f = _make(tmp_path)
    r = _run(tmp_path, repo, b, f)
    assert validate_dataset(tmp_path / "out") == []
    assert scan_benchmark(tmp_path / "out") == []
    assert r.validation_ok and r.contamination_ok and r.certification == "not run"


def test_output_overwrite_refused(tmp_path):
    repo, b, f = _make(tmp_path)
    spec = _spec_file(tmp_path)
    _run(tmp_path, repo, b, f, out="once", spec=spec)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, out="once", spec=spec)
    assert e.value.reason == "output_exists"


def test_import_spec_rejects_unknown_fields(tmp_path):
    repo, b, f = _make(tmp_path)
    bad = _SPEC + "surprise_field: true\n"
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason == "invalid_spec"


def test_cli_help_smoke():
    import re

    from typer.testing import CliRunner

    from arena.cli.main import app

    # Wide width so Rich does not truncate option names; strip ANSI before matching.
    result = CliRunner().invoke(app, ["import-fix", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "buggy" in plain and "fixed" in plain and "spec" in plain


@pytest.mark.skipif(os.name != "posix", reason="local execution is POSIX-only")
def test_generated_case_is_execution_valid(tmp_path):
    # The buggy after/ fails its tests; applying the generated reference.patch through
    # the Phase 1D pipeline repairs it so the tests pass -- a full execution-backed run.
    from arena.benchmark.benchmark_runner import run_benchmark
    from arena.core.registry import create_reviewer

    repo, b, f = _make(tmp_path)
    _run(tmp_path, repo, b, f)
    run = run_benchmark(
        tmp_path / "out",
        create_reviewer("reference-patch"),
        output_dir=tmp_path / "runs",
        persist=False,
        mode="full",
        allow_local_execution=True,
    )
    case = run.case_results[0]
    assert case.patch_applied is True
    assert case.tests_passed is True


# --------------------------------------------------------------------------- #
# Repository-metadata isolation: mutations must not change output             #
# --------------------------------------------------------------------------- #


def _config(repo, key, value):
    _git(repo, "config", key, value)


def _replace_source_blob(repo, b):
    orig = _git(repo, "rev-parse", f"{b}:src/calc.py")
    alt = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input="def combine(a, b):\n    return 999\n",
        capture_output=True,
        text=True,
        env=_ENV,
    ).stdout.strip()
    _git(repo, "replace", orig, alt)


_METADATA_MUTATIONS = {
    "dirty_source": lambda r, b: (r / "src" / "calc.py").write_text("DIRTY\n"),
    "untracked_file": lambda r, b: (r / "untracked.py").write_text("junk\n"),
    "worktree_gitattributes": lambda r, b: (r / ".gitattributes").write_text("*.py binary\n"),
    "info_attributes_binary": lambda r, b: (
        (r / ".git" / "info").mkdir(exist_ok=True),
        (r / ".git" / "info" / "attributes").write_text("src/calc.py binary\n"),
    ),
    "diff_noprefix": lambda r, b: _config(r, "diff.noprefix", "true"),
    "custom_src_prefix": lambda r, b: _config(r, "diff.srcPrefix", "CUSTOM_A/"),
    "custom_dst_prefix": lambda r, b: _config(r, "diff.dstPrefix", "CUSTOM_B/"),
    "diff_algorithm": lambda r, b: _config(r, "diff.algorithm", "histogram"),
    "indent_heuristic": lambda r, b: _config(r, "diff.indentHeuristic", "true"),
    "head_moved": lambda r, b: _git(r, "checkout", "-q", "-b", "elsewhere"),
    "replace_blob": _replace_source_blob,
}


@pytest.mark.parametrize("name", sorted(_METADATA_MUTATIONS), ids=lambda n: n)
def test_repository_metadata_does_not_affect_output(tmp_path, name):
    repo, b, f = _make(tmp_path)
    spec = _spec_file(tmp_path)
    _run(tmp_path, repo, b, f, out="base", spec=spec)
    base = _tree(tmp_path / "base")
    _METADATA_MUTATIONS[name](repo, b)
    _run(tmp_path, repo, b, f, out="mut", spec=spec)
    assert _tree(tmp_path / "mut") == base


def test_bare_repository_matches_worktree(tmp_path):
    repo, b, f = _make(tmp_path)
    spec = _spec_file(tmp_path)
    _run(tmp_path, repo, b, f, out="work", spec=spec)
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--bare", "-q", str(repo), str(bare)],
        capture_output=True,
        text=True,
        env=_ENV,
        check=True,
    )
    import_fix(
        repo_path=bare,
        buggy_commit=b,
        fixed_commit=f,
        spec_path=spec,
        output=tmp_path / "bare_out",
        source_label="acme/calc",
    )
    assert _tree(tmp_path / "bare_out") == _tree(tmp_path / "work")


# --------------------------------------------------------------------------- #
# History-integrity rejections                                                #
# --------------------------------------------------------------------------- #


def test_graft_file_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "grafts").write_text(f + "\n")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f)
    assert e.value.reason == "repository_history_override"


def test_shallow_repository_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth", "1", "-q", "file://" + str(repo), str(shallow)],
        capture_output=True,
        text=True,
        env=_ENV,
        check=True,
    )
    head = _git(shallow, "rev-parse", "HEAD")
    with pytest.raises(ImportFixError) as e:
        import_fix(
            repo_path=shallow,
            buggy_commit=head,
            fixed_commit=head,
            spec_path=_spec_file(tmp_path),
            output=tmp_path / "out",
            source_label="acme/calc",
        )
    assert e.value.reason == "shallow_repository"


# --------------------------------------------------------------------------- #
# Selector and tests-root validation                                          #
# --------------------------------------------------------------------------- #


def test_duplicate_selector_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    bad = _SPEC.replace("source_paths: [src]", "source_paths: [src, src]")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason == "duplicate_selector"


def test_overlapping_selector_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    bad = _SPEC.replace("source_paths: [src]", "source_paths: [src, src/calc.py]")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason == "overlapping_selector"


def test_missing_selector_among_valid_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    bad = _SPEC.replace("source_paths: [src]", "source_paths: [src, nonexistent]")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason == "selected_path_missing"


def _deletion_repo(tmp_path):
    # B has calc.py (buggy) + legacy.py; the fix repairs calc.py and deletes legacy.py.
    repo = _repo(tmp_path)
    b = _commit(
        repo,
        {
            "src/calc.py": "def combine(a, b):\n    return a - b\n",
            "src/legacy.py": "RETIRED = 1\n",
            **_TEST,
        },
        "buggy",
    )
    (repo / "src" / "legacy.py").unlink()  # the fix removes the retired module
    (repo / "src" / "calc.py").write_text("def combine(a, b):\n    return a + b\n", newline="")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix")
    return repo, b, _git(repo, "rev-parse", "HEAD")


def test_deletion_repair_supported(tmp_path):
    # A source file present only in B (deleted by the fix) is a valid, coverable change.
    repo, b, f = _deletion_repo(tmp_path)
    r = _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, _SPEC_TWO_FILES))
    assert "src/legacy.py" in r.repair_changed_paths
    assert r.buggy_source_file_count > r.fixed_source_file_count


def test_added_file_change_is_classified_uncovered(tmp_path):
    # A file added only in F is a repair path but cannot be a buggy-tree bug, so an
    # import that does not (and cannot) declare it fails loudly rather than silently.
    repo = _repo(tmp_path)
    b = _commit(repo, {"src/calc.py": "def combine(a, b):\n    return a - b\n", **_TEST}, "buggy")
    f = _commit(
        repo,
        {
            "src/calc.py": "def combine(a, b):\n    return a + b\n",
            "src/added.py": "HELPER = 1\n",
            **_TEST,
        },
        "fix",
    )
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f)
    assert e.value.reason == "semantic_change_uncovered"


def test_file_valued_tests_root_rejected(tmp_path):
    repo = _repo(tmp_path)
    files_b = {"src/calc.py": "def combine(a, b):\n    return a - b\n", "t.py": "x = 1\n"}
    files_f = {"src/calc.py": "def combine(a, b):\n    return a + b\n", "t.py": "x = 1\n"}
    b = _commit(repo, files_b, "buggy")
    f = _commit(repo, files_f, "fix")
    bad = _SPEC.replace("tests_root: tests", "tests_root: t.py")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason in {"tests_root_not_directory", "tests_root_empty"}


def test_missing_tests_root_rejected(tmp_path):
    repo = _repo(tmp_path)
    b = _commit(repo, {"src/calc.py": "def combine(a, b):\n    return a - b\n"}, "buggy")
    f = _commit(repo, {"src/calc.py": "def combine(a, b):\n    return a + b\n"}, "fix")
    # run_tests/tests_required true but no tests_root in the repo at all
    bad = _SPEC.replace("tests_root: tests", "tests_root: tests_dir_that_does_not_exist")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason == "tests_root_empty"


def test_tests_required_without_tests_root_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    bad = _SPEC.replace("tests_root: tests\n", "")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, bad))
    assert e.value.reason == "tests_root_missing"


# --------------------------------------------------------------------------- #
# Publication and write integrity                                             #
# --------------------------------------------------------------------------- #


def test_existing_empty_output_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    (tmp_path / "out").mkdir()
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, out="out")
    assert e.value.reason == "output_exists"


def test_existing_nonempty_output_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "keep.txt").write_text("important\n")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, out="out")
    assert e.value.reason == "output_exists"
    assert (tmp_path / "out" / "keep.txt").read_text() == "important\n"  # untouched


def test_output_symlink_rejected(tmp_path):
    repo, b, f = _make(tmp_path)
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "out")
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, out="out")
    assert e.value.reason == "output_exists"


def test_zero_progress_write_fails_closed(tmp_path, monkeypatch):
    # Unit test of the shared complete-write helper: a handle that never advances must
    # fail closed. (Patching the global os.fdopen for a full import would break the Git
    # subprocesses, so this exercises the helper directly.)
    import arena.importer.historical_fix as hf

    class _Stuck:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, _data):
            return 0  # never makes progress

        def flush(self):
            pass

        def fileno(self):
            return 0

    monkeypatch.setattr(hf.os, "fdopen", lambda *a, **k: _Stuck())
    with pytest.raises(ImportFixError) as e:
        hf._write_exact(tmp_path / "artifact.txt", b"some bytes", 0o644)
    assert e.value.reason == "output_write_failure"


def test_destination_appearing_before_publish(tmp_path, monkeypatch):
    repo, b, f = _make(tmp_path)
    import arena.importer.historical_fix as hf

    real_scan = hf.scan_benchmark

    def racing_scan(path):
        result = real_scan(path)
        (tmp_path / "out").mkdir()  # destination appears during validation
        return result

    monkeypatch.setattr(hf, "scan_benchmark", racing_scan)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, out="out")
    assert e.value.reason == "output_exists"


# --------------------------------------------------------------------------- #
# Provenance and report counts                                                #
# --------------------------------------------------------------------------- #


def test_provenance_shape_and_evidence(tmp_path):
    import json

    repo, b, f = _make(tmp_path)
    _run(tmp_path, repo, b, f)
    prov = json.loads((tmp_path / "out" / "calc_combine_sign_001" / "provenance.json").read_text())
    assert prov["provenance_schema_version"] == "3"
    assert prov["mode"] == "reverse_fix"
    assert prov["source_label"] == "acme/calc"
    assert prov["diff_policy_version"]
    assert prov["buggy_commit"] == b and prov["fixed_commit"] == f
    for key in (
        "buggy_source_files",
        "fixed_source_files",
        "fixed_test_files",
        "changed_source_paths",
        "changed_test_paths",
    ):
        assert key in prov
    assert "src/calc.py" in prov["changed_source_paths"]
    # The commits' OWN dates are recorded, normalized to UTC. These are properties
    # of the commit objects already named above, not of the import run, so they
    # are identical on every re-import -- unlike a wall-clock reading, which the
    # forbidden-substring check below still rules out. Field names deliberately
    # avoid the substring "time" for exactly that reason.
    iso_utc = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
    for key in (
        "fixed_commit_author_date",
        "fixed_commit_committer_date",
        "buggy_commit_committer_date",
    ):
        assert iso_utc.match(prov[key]), f"{key} is not UTC ISO-8601: {prov[key]!r}"
    # Volatile / private fields must never appear.
    text = json.dumps(prov)
    for forbidden in ("hostname", "username", "time", str(repo), str(tmp_path)):
        assert forbidden not in text


@pytest.mark.parametrize(
    "label", ["bad label", "http://x/y", "/abs/path", "a/b/c/d/e", "owner:repo", "..", ""]
)
def test_invalid_source_label_rejected(tmp_path, label):
    repo, b, f = _make(tmp_path)
    with pytest.raises(ImportFixError):
        import_fix(
            repo_path=repo,
            buggy_commit=b,
            fixed_commit=f,
            spec_path=_spec_file(tmp_path),
            output=tmp_path / "out",
            source_label=label,
        )


def test_union_source_counts(tmp_path):
    repo, b, f = _deletion_repo(tmp_path)
    r = _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, _SPEC_TWO_FILES))
    assert r.buggy_source_file_count == 2  # calc.py + legacy.py
    assert r.fixed_source_file_count == 1  # legacy.py deleted
    assert r.union_source_file_count == 2
    assert r.source_file_count == r.union_source_file_count


# --------------------------------------------------------------------------- #
# Incremental import budget                                                   #
# --------------------------------------------------------------------------- #


def test_file_count_rejected_before_reading_blobs(tmp_path, monkeypatch):
    # The output-file-count limit is enforced before any blob is read.
    repo, b, f = _make(tmp_path)
    import arena.importer.git_objects as go

    calls = []
    real = go.cat_blob
    monkeypatch.setattr(go, "cat_blob", lambda repo, oid: (calls.append(oid), real(repo, oid))[1])
    monkeypatch.setattr(limits, "IMPORT_MAX_FILES", 1)  # 3 output files exceed this
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f)
    assert e.value.reason == "import_limit_exceeded"
    assert calls == []  # rejected before reading a single blob


def test_byte_limit_rejected_during_loading_stops_early(tmp_path, monkeypatch):
    # The byte budget is enforced incrementally; later blobs are never requested.
    repo = _repo(tmp_path)
    files = {f"src/m{i}.py": f"DATA_{i} = '{'x' * 80}'\n" for i in range(4)}
    files.update(_TEST)
    b = _commit(repo, files, "buggy")
    files2 = dict(files)
    files2["src/m0.py"] = f"DATA_0 = '{'y' * 80}'\n"
    f = _commit(repo, files2, "fix")
    import arena.importer.git_objects as go

    calls = []
    real = go.cat_blob
    monkeypatch.setattr(go, "cat_blob", lambda repo, oid: (calls.append(oid), real(repo, oid))[1])
    monkeypatch.setattr(limits, "IMPORT_MAX_TOTAL_BYTES", 120)  # ~1 of the ~92-byte files fits
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f)
    assert e.value.reason == "import_limit_exceeded"
    # 4 distinct source files + 1 test exist; loading stopped well before reading them all.
    assert 0 < len(calls) < 5


def test_per_file_limit_rejected(tmp_path, monkeypatch):
    repo, b, f = _make(tmp_path)
    monkeypatch.setattr(limits, "IMPORT_MAX_FILE_BYTES", 4)  # below the calc.py blob size
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f)
    assert e.value.reason == "import_limit_exceeded"


# --------------------------------------------------------------------------- #
# Output-parent preflight                                                     #
# --------------------------------------------------------------------------- #


def test_output_parent_missing(tmp_path):
    repo, b, f = _make(tmp_path)
    with pytest.raises(ImportFixError) as e:
        import_fix(
            repo_path=repo,
            buggy_commit=b,
            fixed_commit=f,
            spec_path=_spec_file(tmp_path),
            output=tmp_path / "nope" / "out",
            source_label="acme/calc",
        )
    assert e.value.reason == "output_parent_missing"


def test_output_parent_is_file(tmp_path):
    repo, b, f = _make(tmp_path)
    (tmp_path / "afile").write_text("x")
    with pytest.raises(ImportFixError) as e:
        import_fix(
            repo_path=repo,
            buggy_commit=b,
            fixed_commit=f,
            spec_path=_spec_file(tmp_path),
            output=tmp_path / "afile" / "out",
            source_label="acme/calc",
        )
    assert e.value.reason == "output_parent_invalid"


def test_output_parent_is_symlink(tmp_path):
    repo, b, f = _make(tmp_path)
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "link")
    with pytest.raises(ImportFixError) as e:
        import_fix(
            repo_path=repo,
            buggy_commit=b,
            fixed_commit=f,
            spec_path=_spec_file(tmp_path),
            output=tmp_path / "link" / "out",
            source_label="acme/calc",
        )
    assert e.value.reason == "output_parent_invalid"
    assert list((tmp_path / "real").iterdir()) == []  # no staging residue


def test_staging_creation_failure(tmp_path, monkeypatch):
    repo, b, f = _make(tmp_path)
    import arena.importer.historical_fix as hf

    real_mkdtemp = hf.tempfile.mkdtemp

    def selective(*a, **k):
        # Only the staging call passes dir=<output parent>; leave diff_repo and
        # the verify TemporaryDirectory (which pass dir positionally as None) alone.
        if k.get("dir"):
            raise OSError("cannot create staging")
        return real_mkdtemp(*a, **k)

    monkeypatch.setattr(hf.tempfile, "mkdtemp", selective)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, out="out")
    assert e.value.reason == "staging_failed"
    assert not (tmp_path / "out").exists()
    assert not any(p.name.startswith(".arena-import-") for p in tmp_path.iterdir())


# --------------------------------------------------------------------------- #
# Exact mode reproduction                                                     #
# --------------------------------------------------------------------------- #


def _mode_change_repo(tmp_path, b_mode, f_mode):
    # The fix changes tool.py's content AND its mode (a content-less diff would be
    # rejected by normal pack validation, so a realistic case changes both).
    repo = _repo(tmp_path)
    b = _commit(
        repo, {"src/tool.py": "print('hi')\n", **_TEST}, "buggy", modes={"src/tool.py": b_mode}
    )
    (repo / "src" / "tool.py").write_text("print('bye')\n", newline="")
    os.chmod(repo / "src" / "tool.py", f_mode)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix")
    return repo, b, _git(repo, "rev-parse", "HEAD")


_MODE_SPEC = textwrap.dedent("""\
    schema_version: "1"
    pack: {version: "1", name: imported_fixes}
    case:
      id: tool_mode_001
      title: tool mode
      category: correctness
      severity: low
      stack: [python]
      description: tool file had the wrong mode.
    source_paths: [src]
    tests_root: tests
    ground_truth:
      bugs:
        - id: bug-1
          summary: wrong file mode
          files:
            - path: src/tool.py
              line_ranges: [{start: 1, end: 1}]
          concepts: [permissions]
          must_mention: [executable]
          acceptable_fix_keywords: [chmod]
    execution: {run_tests: true, test_command: "pytest -q tests", timeout_seconds: 30}
    validation: {patch_required: true, tests_required: true}
""")


@pytest.mark.parametrize("b_mode,f_mode", [(0o644, 0o755), (0o755, 0o644)])
def test_mode_change_reproduces(tmp_path, b_mode, f_mode):
    repo, b, f = _mode_change_repo(tmp_path, b_mode, f_mode)
    r = _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, _MODE_SPEC))
    assert "src/tool.py" in r.repair_changed_paths
    case = tmp_path / "out" / "tool_mode_001"
    # after/ is the buggy tree (mode b_mode); before/ is the fixed tree (mode f_mode).
    assert bool((case / "after" / "src" / "tool.py").stat().st_mode & 0o111) == (b_mode == 0o755)
    assert bool((case / "before" / "src" / "tool.py").stat().st_mode & 0o111) == (f_mode == 0o755)


def test_verify_reproduces_mode_only_change(tmp_path):
    # The verifier proves a pure mode-only change (no content change) at the unit level.
    from arena.importer import diff_repo
    from arena.importer.historical_fix import _verify_reproduces, _write_exact

    b_tree = {"src/x.sh": ("100644", b"echo hi\n")}
    f_tree = {"src/x.sh": ("100755", b"echo hi\n")}
    reference, _pr = diff_repo.generate_patches("sha1", b_tree, f_tree)
    after = tmp_path / "after"
    _write_exact(after / "src" / "x.sh", b"echo hi\n", 0o644)
    _verify_reproduces(after, b_tree, reference, f_tree, "r")  # 644 -> 755 reproduced


def test_verify_reproduces_rejects_mode_mismatch(tmp_path):
    # Bytes reproduce correctly, but a wrong expected mode must fail closed.
    from arena.importer import diff_repo
    from arena.importer.historical_fix import _verify_reproduces, _write_exact

    b_tree = {"src/x.py": ("100644", b"A = 1\n")}
    f_tree = {"src/x.py": ("100644", b"A = 2\n")}
    reference, _pr = diff_repo.generate_patches("sha1", b_tree, f_tree)
    after_ok = tmp_path / "after_ok"
    _write_exact(after_ok / "src" / "x.py", b"A = 1\n", 0o644)
    _verify_reproduces(after_ok, b_tree, reference, f_tree, "r")  # correct: no raise
    after_bad = tmp_path / "after_bad"
    _write_exact(after_bad / "src" / "x.py", b"A = 1\n", 0o644)
    with pytest.raises(ImportFixError) as e:
        _verify_reproduces(after_bad, b_tree, reference, {"src/x.py": ("100755", b"A = 2\n")}, "r")
    assert e.value.reason == "r"


def test_unchanged_executable_remains_executable(tmp_path):
    # tool.sh is executable in both B and F (unchanged); only calc.py is repaired.
    repo = _repo(tmp_path)
    b = _commit(
        repo,
        {
            "src/calc.py": "def combine(a, b):\n    return a - b\n",
            "src/tool.sh": "echo hi\n",
            **_TEST,
        },
        "buggy",
        modes={"src/tool.sh": 0o755},
    )
    (repo / "src" / "calc.py").write_text("def combine(a, b):\n    return a + b\n", newline="")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix")
    f = _git(repo, "rev-parse", "HEAD")
    r = _run(tmp_path, repo, b, f)  # default _SPEC covers src/calc.py only
    assert r.repair_changed_paths == ["src/calc.py"]  # tool.sh unchanged, not a repair path
    case = tmp_path / "out" / "calc_combine_sign_001"
    assert (case / "after" / "src" / "tool.sh").stat().st_mode & 0o111
    assert (case / "before" / "src" / "tool.sh").stat().st_mode & 0o111


# --------------------------------------------------------------------------- #
# Source-selector file/dir semantics (dot-prefixed selectors)                 #
# --------------------------------------------------------------------------- #

_SPEC_EXACT_FILE = _SPEC.replace("source_paths: [src]", "source_paths: [src/calc.py]")
_SPEC_HIDDEN_SEL = _SPEC.replace("source_paths: [src]", "source_paths: [.hidden]")
_SPEC_MISSING_SEL = _SPEC.replace("source_paths: [src]", "source_paths: [.nope]")
_SPEC_DOT_TESTS_ROOT = _SPEC.replace("tests_root: tests", "tests_root: .hidden")


def _repo_with_dotpaths(tmp_path):
    repo = _repo(tmp_path)
    common = {".coveragerc": "[run]\nbranch = False\n", ".hidden/keep.py": "H = 1\n"}
    b = _commit(repo, {**_BUGGY, **_TEST, **common}, "buggy")
    f = _commit(repo, {**_FIXED, **_TEST, **common}, "fix")
    return repo, b, f


def test_classify_tree_path_distinguishes_file_dir_missing(tmp_path):
    from arena.importer.git_objects import classify_tree_path, open_repo

    repo, _b, f = _repo_with_dotpaths(tmp_path)
    with open_repo(repo) as r:
        assert classify_tree_path(r, f, ".coveragerc") == "file"  # exact regular file
        assert classify_tree_path(r, f, "src/calc.py") == "file"  # ordinary exact file
        assert classify_tree_path(r, f, ".hidden") == "dir"  # hidden directory
        assert classify_tree_path(r, f, "src") == "dir"  # ordinary directory
        assert classify_tree_path(r, f, ".nope") == "missing"  # absent


def test_exact_file_source_selector_accepted(tmp_path):
    # 6. ordinary exact-file selector still works end to end.
    repo, b, f = _make(tmp_path)
    _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, _SPEC_EXACT_FILE))
    case = tmp_path / "out" / "calc_combine_sign_001"
    assert (case / "after" / "src" / "calc.py").is_file()
    assert (case / "before" / "src" / "calc.py").is_file()


def test_dot_directory_source_selector_rejected(tmp_path):
    # 2. a final .hidden selector with descendants is rejected as a hidden directory.
    repo, b, f = _repo_with_dotpaths(tmp_path)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, _SPEC_HIDDEN_SEL))
    assert e.value.reason == "hidden_directory_selector"


def test_missing_dot_source_selector_rejected(tmp_path):
    # 3. a missing final dot-prefixed selector is rejected.
    repo, b, f = _repo_with_dotpaths(tmp_path)
    with pytest.raises(ImportFixError) as e:
        _run(tmp_path, repo, b, f, spec=_spec_file(tmp_path, _SPEC_MISSING_SEL))
    assert e.value.reason == "selected_path_missing"


def test_tests_root_rejects_dot_directory(tmp_path):
    # A directory-valued importer field (tests_root: SafeDirPath) rejects a
    # dot-prefixed directory; load_import_spec surfaces it as invalid_spec.
    from arena.importer.import_spec import load_import_spec

    spec = _spec_file(tmp_path, _SPEC_DOT_TESTS_ROOT)
    with pytest.raises(ImportFixError) as e:
        load_import_spec(spec)
    assert e.value.reason == "invalid_spec"
    assert "directory path" in str(e.value)


def test_the_emitted_case_declares_where_its_answer_came_from(tmp_path):
    """Every imported case is derived from public history, and says so.

    The date is read from the commit rather than left to a human to remember,
    because the one field that would let anyone reason about training-data
    exposure is exactly the field that rots when it lives only in prose.
    """
    import yaml

    repo, b, f = _make(tmp_path)
    _run(tmp_path, repo, b, f)
    case = yaml.safe_load((tmp_path / "out" / "calc_combine_sign_001" / "case.yaml").read_text())

    origin = case["origin"]
    assert origin["kind"] == "derived_public"
    assert origin["source_label"] == "acme/calc"
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", str(origin["public_fix_date"]))
    assert origin["public_fix_date_basis"] in {"git_author_date", "git_committer_date"}


def test_a_declared_date_later_than_the_commit_is_ignored(tmp_path):
    """Disagreement resolves toward MORE assumed exposure, never less.

    A spec claiming the fix became public later than its own commit is the
    flattering direction: it would push a case toward the post-cutoff cohort and
    make a model look less likely to have memorized it. The commit's own date
    wins, and the recorded basis says so.
    """
    import yaml

    repo, b, f = _make(tmp_path)
    spec = _spec_file(tmp_path, _SPEC + '\norigin:\n  public_fix_date: "2099-01-01"\n')
    _run(tmp_path, repo, b, f, out="late", spec=spec)
    case = yaml.safe_load((tmp_path / "late" / "calc_combine_sign_001" / "case.yaml").read_text())

    assert str(case["origin"]["public_fix_date"]) != "2099-01-01"
    assert case["origin"]["public_fix_date_basis"] == "min_of_signals"


def test_an_earlier_declared_date_is_honoured(tmp_path):
    """A human can know something Git cannot.

    The defect may have been disclosed in an issue or advisory before the commit
    that repaired it, and that earlier disclosure is when the answer became
    readable. This is the one direction a declaration may move the date.
    """
    import yaml

    repo, b, f = _make(tmp_path)
    spec = _spec_file(tmp_path, _SPEC + '\norigin:\n  public_fix_date: "2001-02-03"\n')
    _run(tmp_path, repo, b, f, out="early", spec=spec)
    case = yaml.safe_load((tmp_path / "early" / "calc_combine_sign_001" / "case.yaml").read_text())

    assert str(case["origin"]["public_fix_date"]) == "2001-02-03"
    assert case["origin"]["public_fix_date_basis"] == "declared"
