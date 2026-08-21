"""Benchmark integrity guards: hostile patches, blind payloads, bounded context."""

import json
from pathlib import Path

import pytest

from arena.benchmark.case_loader import ContextLimits, build_context, load_cases
from arena.core.models import BenchmarkCase
from arena.patching.patch_applier import PatchApplier, is_protected_path
from arena.patching.patch_models import PatchApplyRequest, PatchApplyResult
from arena.patching.patch_parser import unsafe_patch_paths
from arena.reviewers.custom_command import serialize_reviewer_case
from arena.scoring.deterministic_scorer import score_deterministic_case
from arena.scoring.scorer import score_case
from tests.test_multi_bug_scoring import BUG0_FINDING, _case, _response

AUDIT_DIR = Path("benchmark_sets/audit_v1")


def _apply(tmp_path: Path, source_dir: Path, patch_text: str, **kwargs) -> PatchApplyResult:
    return PatchApplier(tmp_path / "runs").apply(
        PatchApplyRequest(
            case_id="case",
            source_dir=source_dir,
            patch_text=patch_text,
            run_id="hostile",
            **kwargs,
        )
    )


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app").mkdir()
    (source / "app" / "main.py").write_text("value = 1\n", encoding="utf-8")
    return source


def test_patch_touching_tests_dir_is_rejected(tmp_path, source_dir):
    # The protected file exists, so the patch applies cleanly and the AUTHORITATIVE
    # Git result (not the patch text) is what gets rejected as a protected change.
    (source_dir / "tests").mkdir()
    (source_dir / "tests" / "test_main.py").write_text("assert value == 2\n", encoding="utf-8")
    hostile = (
        "--- a/tests/test_main.py\n"
        "+++ b/tests/test_main.py\n"
        "@@ -1 +1 @@\n"
        "-assert value == 2\n"
        "+assert True\n"
    )
    result = _apply(tmp_path, source_dir, hostile, protected_paths=["tests"])
    assert result.applied is False
    assert result.reason == "protected_path_changed"
    assert "tests/test_main.py" in result.touched_protected


def test_patch_creating_conftest_is_rejected_anywhere(tmp_path, source_dir):
    hostile = "--- /dev/null\n+++ b/app/conftest.py\n@@ -0,0 +1 @@\n+import sys\n"
    result = _apply(tmp_path, source_dir, hostile)
    assert result.applied is False
    assert result.touched_protected == ["app/conftest.py"]


@pytest.mark.parametrize(
    "config_name",
    ["pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"],
)
def test_patch_creating_any_pytest_config_is_rejected(tmp_path, source_dir, config_name):
    """Every name pytest searches for a config in is off-limits to a patch.

    The dotted and .toml spellings outrank pytest.ini/pyproject.toml in pytest's own
    discovery order, so an unprotected one lets a patch set `addopts = -p <plugin>`
    and force exit 0 while the seeded bug is untouched.
    """
    hostile = (
        f"--- /dev/null\n+++ b/{config_name}\n@@ -0,0 +1,2 @@\n"
        "+[pytest]\n+addopts = -p arenahelper\n"
    )
    result = _apply(tmp_path, source_dir, hostile)
    assert result.applied is False
    assert result.reason == "protected_path_changed"
    assert config_name in result.touched_protected


@pytest.mark.parametrize("shadow", ["pytest.py", "pytest/__init__.py"])
def test_patch_shadowing_the_pytest_module_is_rejected(tmp_path, source_dir, shadow):
    """`<interpreter> -m pytest` puts the workspace root first on sys.path.

    A root-level pytest.py module or pytest/ package therefore shadows the real
    runner and can exit 0 without collecting a single test.
    """
    hostile = f"--- /dev/null\n+++ b/{shadow}\n@@ -0,0 +1 @@\n+raise SystemExit(0)\n"
    result = _apply(tmp_path, source_dir, hostile)
    assert result.applied is False
    assert result.reason == "protected_path_changed"
    assert shadow in result.touched_protected


def test_patch_with_path_traversal_is_rejected(tmp_path, source_dir):
    hostile = "--- a/../../escape.py\n+++ b/../../escape.py\n@@ -1 +1 @@\n-x\n+y\n"
    result = _apply(tmp_path, source_dir, hostile)
    # Git itself refuses to apply a traversal path; the path guard is a backstop.
    assert result.applied is False
    assert result.reason in {"patch_preflight_failed", "unsafe_result_path"}


def test_patch_with_absolute_path_is_rejected(tmp_path, source_dir):
    hostile = "--- /etc/passwd\n+++ /etc/passwd\n@@ -1 +1 @@\n-x\n+y\n"
    result = _apply(tmp_path, source_dir, hostile)
    assert result.applied is False
    assert result.reason in {"patch_preflight_failed", "unsafe_result_path"}


def test_unsafe_paths_cover_renames():
    diff = "diff --git a/app/a.py b/../../b.py\nrename from app/a.py\nrename to ../../b.py\n"
    assert unsafe_patch_paths(diff) == ["../../b.py"]


def test_patch_creating_a_symlink_is_rejected(tmp_path, source_dir):
    # A mode-120000 diff makes git apply create a symlink, which could point
    # outside the workspace; the path-string guard alone would miss it.
    hostile = (
        "diff --git a/app/link b/app/link\n"
        "new file mode 120000\n"
        "--- /dev/null\n"
        "+++ b/app/link\n"
        "@@ -0,0 +1 @@\n"
        "+/etc/passwd\n"
    )
    result = _apply(tmp_path, source_dir, hostile)
    # Authoritative: the resulting Git mode is 120000 (a symlink), which is rejected.
    assert result.applied is False
    assert result.reason in {"unsafe_result_mode", "unsafe_result_entry"}


def test_protected_path_rules():
    assert is_protected_path("tests/test_x.py", ["tests"]) is True
    assert is_protected_path("conftest.py", []) is True
    assert is_protected_path("pyproject.toml", []) is True
    # pytest's full config-discovery set, plus the -m pytest module-shadow surface.
    for name in ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"):
        assert is_protected_path(name, []) is True
    assert is_protected_path("pytest.py", []) is True
    assert is_protected_path("pytest/__init__.py", []) is True
    assert is_protected_path("app/main.py", ["tests"]) is False


def test_protected_patch_recorded_as_failure_reason():
    case = _case()
    review = score_case(case, _response([BUG0_FINDING]))
    patch = PatchApplyResult(
        case_id=case.id,
        applied=False,
        error="patch_touched_protected_files: tests/test_a.py",
        touched_files=["tests/test_a.py"],
        touched_protected=["tests/test_a.py"],
        workspace_path="unused",
        patch_text="--- a/tests/test_a.py\n+++ b/tests/test_a.py\n",
        duration_ms=1,
    )
    deterministic = score_deterministic_case(case, review, patch, None, [], beta=1.0)
    assert "patch_touched_protected_files" in deterministic.failure_reasons
    assert deterministic.deterministic_pass is False


def test_reviewer_payload_is_blind_by_default():
    case = load_cases(AUDIT_DIR)[0]
    context = build_context(case)
    payload = serialize_reviewer_case(context)
    for leaked in ("title", "description", "category", "severity"):
        assert leaked not in payload
    serialized = json.dumps(payload)
    assert case.title not in serialized
    assert case.description.strip().split(".")[0] not in serialized
    assert payload["case_id"] == case.id
    assert payload["pr_diff"]
    assert payload["relevant_files"]


def test_reveal_metadata_flag_restores_descriptive_fields():
    case = load_cases(AUDIT_DIR)[0]
    context = build_context(case)
    payload = serialize_reviewer_case(context, reveal_metadata=True)
    assert payload["title"] == case.title
    assert payload["severity"] == case.severity


def _disk_case(tmp_path: Path) -> BenchmarkCase:
    case_dir = tmp_path / "ctx_case"
    (case_dir / "after" / "app").mkdir(parents=True)
    (case_dir / "before").mkdir()
    (case_dir / "after" / "app" / "small.py").write_text("x = 1\n", encoding="utf-8")
    (case_dir / "after" / "app" / "big.py").write_text("y = 2\n" * 4000, encoding="utf-8")
    (case_dir / "pr.diff").write_text(
        "--- a/app/small.py\n+++ b/app/small.py\n@@ -1 +1 @@\n-x = 0\n+x = 1\n",
        encoding="utf-8",
    )
    case = _case()
    case.case_dir = case_dir
    return case


def test_context_is_bounded_and_prefers_diff_files(tmp_path):
    case = _disk_case(tmp_path)
    context = build_context(case, limits=ContextLimits(max_files=1))
    assert list(context.relevant_files) == ["app/small.py"]
    assert context.context_truncated is True
    assert context.omitted_files == ["app/big.py"]
    payload = serialize_reviewer_case(context)
    assert payload["context_truncated"] is True


def test_oversized_files_are_truncated_with_marker(tmp_path):
    case = _disk_case(tmp_path)
    context = build_context(case, limits=ContextLimits(max_file_bytes=64))
    assert "truncated by arena" in context.relevant_files["app/big.py"]
    assert context.context_truncated is True


def test_unbounded_context_is_not_marked_truncated(tmp_path):
    case = _disk_case(tmp_path)
    context = build_context(case)
    assert context.context_truncated is False
    assert context.omitted_files == []
    assert "context_truncated" not in serialize_reviewer_case(context)


def _sabotage_workspace(tmp_path, sabotage: str = "", *, fixed: bool = False):
    """A workspace whose hidden suite genuinely fails unless `fixed` is set."""
    workspace = tmp_path / "ws"
    (workspace / "tests").mkdir(parents=True)
    body = "def add(a, b):\n    return a + b\n" if fixed else "def add(a, b):\n    return a - b\n"
    (workspace / "app.py").write_text(sabotage + body, encoding="utf-8")
    (workspace / "tests" / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    return workspace


def _execute(workspace):
    from arena.execution.test_executor import TestExecutionRequest, TestExecutor

    return TestExecutor().execute(
        TestExecutionRequest(
            case_id="evidence-001",
            workspace_path=workspace,
            test_command=[["pytest", "-q", "tests"]],
            timeout_seconds=60,
            allow_local_execution=True,
        )
    )


def test_exit_status_alone_cannot_certify_a_repair(tmp_path):
    """A patch must not be able to buy a pass by exiting the interpreter.

    The suite runs with the candidate's patch applied, so reviewer-controlled
    code executes in the very process whose exit status decided the verdict:
    `import os; os._exit(0)` at the top of the file under review terminated
    pytest with status 0 during collection and a genuinely failing suite was
    recorded as passing, with no protected path touched and the hidden tests
    byte-identical. A pass now also requires a JUnit report showing tests ran.
    """
    sabotaged = _execute(_sabotage_workspace(tmp_path / "exit", "import os as _o\n_o._exit(0)\n"))

    assert sabotaged.exit_code == 0  # the process really did exit clean...
    assert sabotaged.passed is False  # ...and it still is not a pass
    assert sabotaged.error == "no_test_evidence"


def test_shadowing_a_runner_dependency_cannot_certify_a_repair(tmp_path):
    """Blocklisting `pytest` is not enough: anything it imports is a target.

    A workspace-root `_pytest/` (or `pluggy.py`, `iniconfig.py`) is imported by
    pytest's own package before a single test is collected, so the runner is
    hijacked without touching any protected name. Requiring evidence closes the
    whole class rather than one module name at a time.
    """
    for index, (name, contents) in enumerate(
        [
            (
                "_pytest/__init__.py",
                "import os, sys\nsys.stdout.write('1 passed\\n')\nos._exit(0)\n",
            ),
            ("pluggy.py", "import os\nos._exit(0)\n"),
            ("iniconfig.py", "import os\nos._exit(0)\n"),
        ]
    ):
        workspace = _sabotage_workspace(tmp_path / f"shadow{index}")
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

        result = _execute(workspace)

        assert result.passed is False, name
        assert result.error == "no_test_evidence", name


def test_evidence_requirement_does_not_break_an_honest_run(tmp_path):
    """The guard must not cost a real repair its pass, nor excuse a real failure."""
    failing = _execute(_sabotage_workspace(tmp_path / "bug"))
    assert failing.passed is False
    assert failing.error is None  # an ordinary failing suite, not an evidence problem

    repaired = _execute(_sabotage_workspace(tmp_path / "fix", fixed=True))
    assert repaired.passed is True
    assert repaired.error is None


def test_report_with_zero_collected_tests_is_not_a_pass(tmp_path):
    """An empty report is indistinguishable from a suite that never ran."""
    from arena.execution.test_evidence import TestReport, read_report

    assert TestReport(tests=0, failures=0, errors=0, skipped=0).ran_and_passed is False
    assert TestReport(tests=3, failures=0, errors=0, skipped=1).ran_and_passed is True
    assert TestReport(tests=3, failures=1, errors=0, skipped=0).ran_and_passed is False
    assert TestReport(tests=3, failures=0, errors=1, skipped=0).ran_and_passed is False
    # A skipped test is not an executed test. JUnit's `tests` attribute counts
    # skips, so accepting `tests > 0` let a patch raise pytest.skip() from the
    # file under review and turn a failing suite green: exit 0, a well-formed
    # report, zero failures, seeded bug untouched.
    assert TestReport(tests=2, failures=0, errors=0, skipped=2).ran_and_passed is False
    assert TestReport(tests=9, failures=0, errors=0, skipped=9).ran_and_passed is False
    assert TestReport(tests=2, failures=0, errors=0, skipped=2).executed == 0
    # A missing or malformed report is no evidence at all.
    assert read_report(tmp_path / "absent.xml") is None
    junk = tmp_path / "junk.xml"
    junk.write_text("not xml", encoding="utf-8")
    assert read_report(junk) is None


def test_reviewer_process_is_contained_and_labelled(tmp_path):
    """A reviewer must not start inside the repo, nor inherit the host environment.

    The custom-command reviewer ran with `cwd=Path.cwd()` and the FULL host
    environment, so a wrapper was handed every credential in the operator's shell
    and sat one relative path away from `benchmark_sets/<case>/reference.patch`.
    A wrapper that simply reads that file and echoes it scores a perfect 1.000 on
    every metric, which measures filesystem access rather than review skill.
    """
    import json
    import os

    from arena.reviewers.custom_command import CustomCommandReviewer

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os\n"
        "leaked = [k for k in os.environ if 'SECRET' in k.upper()]\n"
        "print(json.dumps({'findings': [], 'overall_risk': 'low',\n"
        "  'review_summary': json.dumps({'cwd': os.getcwd(), 'leaked': leaked,\n"
        "    'repo_visible': os.path.isdir('benchmark_sets')})}))\n",
        encoding="utf-8",
    )
    context = build_context(load_cases(Path("benchmark_sets/audit_v1"))[0])
    reviewer = CustomCommandReviewer(f"python {probe} {{case_json}}")
    os.environ["SECRET_PROBE_TOKEN"] = "hunter2"
    try:
        response = reviewer.review(context)
    finally:
        os.environ.pop("SECRET_PROBE_TOKEN", None)

    assert response.parsed_response is not None
    observed = json.loads(response.parsed_response.review_summary)
    # Started outside the repository, so the pack is not one relative path away.
    assert observed["repo_visible"] is False
    assert "benchmark_sets" not in observed["cwd"]
    # And the operator's shell secrets were not handed to a third-party wrapper.
    assert observed["leaked"] == []


def test_oracle_reachable_reviewers_are_never_default_comparable():
    """Containment is not isolation, so the run says so and the ranking respects it.

    A host process can still read the oracle by absolute path. That cannot be
    prevented without a real isolation boundary, so the honest response is to
    record it and refuse to rank such a run against reviewers kept blind.
    """
    from arena.reports.leaderboard import eligibility_from_fields
    from arena.reviewers.controls import ControlReviewer
    from arena.reviewers.custom_command import CustomCommandReviewer
    from arena.reviewers.reference_patch import ReferencePatchReviewer

    assert CustomCommandReviewer("true").oracle_reachable is True
    assert ReferencePatchReviewer().oracle_reachable is True  # reads the answer by design
    assert ControlReviewer("perfect").oracle_reachable is False

    otherwise_perfect = dict(
        schema_version=2,
        run_status="complete",
        execution_backend="docker",
        coverage_rate=1.0,
        pack_digest_externally_verified=True,
        non_exact_output_used=False,
    )
    assert eligibility_from_fields(**otherwise_perfect, reviewer_oracle_reachable=False) is True
    assert eligibility_from_fields(**otherwise_perfect, reviewer_oracle_reachable=True) is False
    # Unknown (a run written before the field existed) is treated as unsafe.
    assert eligibility_from_fields(**otherwise_perfect, reviewer_oracle_reachable=None) is False
    # Still inspectable on request.
    assert (
        eligibility_from_fields(
            **otherwise_perfect, reviewer_oracle_reachable=True, include_unverified=True
        )
        is True
    )


def test_skipping_the_bug_tests_does_not_buy_a_pass(tmp_path):
    """A patch that skips the suite must not be scored as a repair.

    Exit code 0 plus a well-formed report is not enough: `pytest.skip()` raised
    from the file under review produces both while executing nothing. Blanket
    skips are rejected here; a *surgical* skip that leaves unrelated tests
    running is a documented residual (see arena/execution/test_evidence.py) and
    needs the certified reference run's expected node set to close.
    """
    workspace = _sabotage_workspace(
        tmp_path / "skip", "import pytest\npytest.skip('x', allow_module_level=True)\n"
    )

    result = _execute(workspace)

    assert result.passed is False


def test_an_interpreter_flag_does_not_disable_the_evidence_gate(tmp_path):
    """`python -W error -m pytest` is still pytest.

    Matching `-m` only at argv[1] meant any interpreter flag before it silently
    turned the gate off and fell back to the exit code alone -- the oracle the
    evidence layer exists to replace. It also disagreed with
    certify._is_pytest_command, so a case could be certified under pytest
    semantics and then scored as an unrecognised runner.
    """
    from arena.execution.test_evidence import is_pytest_command

    assert is_pytest_command(["python", "-W", "error", "-m", "pytest", "-q"]) is True
    assert is_pytest_command(["python", "-X", "dev", "-m", "pytest"]) is True
    assert is_pytest_command(["python", "-bb", "-m", "pytest"]) is True
    assert is_pytest_command(["/x/.venv/bin/python3.12", "-m", "pytest"]) is True
    assert is_pytest_command(["py.test", "-q"]) is True
    assert is_pytest_command(["python", "-m", "unittest"]) is False
    assert is_pytest_command(["make", "test"]) is False

    # And the gate really does still fire for such a command.
    workspace = _sabotage_workspace(tmp_path / "flag", "import os as _o\n_o._exit(0)\n")
    from arena.execution.test_executor import TestExecutionRequest, TestExecutor

    result = TestExecutor().execute(
        TestExecutionRequest(
            case_id="flagged-001",
            workspace_path=workspace,
            test_command=[["python", "-W", "error", "-m", "pytest", "-q", "tests"]],
            timeout_seconds=60,
            allow_local_execution=True,
        )
    )
    assert result.passed is False
    assert result.error == "no_test_evidence"
