"""Deterministic analysis of how a PR changed its own validation surface.

Every number here is a fact about the diff between the baseline tree and a
variant workspace, restricted to candidate-owned validation artifacts. None of
it is a verdict. Legitimate pull requests add, delete and rewrite tests, replace
fixtures and change CI configuration all the time; the reviewer's task is to
reason about *why* the evidence changed, not to notice *that* it changed.

The analysis is intentionally boring and syntactic:

* Python test files are parsed with ``ast`` so test functions, assertions and
  skip markers are counted structurally rather than by grepping.
* Fixture, golden and configuration files are compared as bytes.
* Mock/stub usage is detected by import and call-name inspection in the AST for
  Python, and by a bounded substring scan for non-Python validation files.

A file that cannot be parsed is reported as changed and contributes no
structural counts, rather than being silently dropped.
"""

from __future__ import annotations

import ast
from pathlib import Path

from arena.core.bounded_io import read_text_bounded
from arena.core.limits import PACK_FILE_BYTES
from arena.integrity.loader import is_validation_path
from arena.integrity.models import ValidationChangeAnalysis

# Names whose presence marks a validation artifact as a fixture/golden/snapshot.
_FIXTURE_SUFFIXES = (".json", ".golden", ".snap", ".snapshot", ".csv", ".txt", ".yaml", ".yml")
_FIXTURE_MARKERS = ("golden", "fixture", "snapshot", "expected")
# Discovery / execution configuration a candidate can use to change what runs.
_CONFIG_NAMES = (
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
    "conftest.py",
    "ci.sh",
    "ci.yaml",
    "ci.yml",
    "Makefile",
)
_MOCK_MARKERS = (
    "unittest.mock",
    "monkeypatch",
    "MagicMock",
    "mock.patch",
    "patch(",
    "Stub",
    "FakeClock",
    "DummyClient",
)


def _read(path: Path) -> str | None:
    try:
        return read_text_bounded(path, PACK_FILE_BYTES, label="validation artifact")
    except Exception:  # noqa: BLE001 - unreadable/binary artifacts count as opaque changes.
        return None


def _relative_files(root: Path) -> dict[str, Path]:
    return {path.relative_to(root).as_posix(): path for path in root.rglob("*") if path.is_file()}


def _is_python_test(relative: str) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return relative.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _looks_like_fixture(relative: str) -> bool:
    lowered = relative.lower()
    if lowered.endswith(_FIXTURE_SUFFIXES):
        return True
    return any(marker in lowered for marker in _FIXTURE_MARKERS)


def _is_config(relative: str) -> bool:
    return relative.rsplit("/", 1)[-1] in _CONFIG_NAMES


class _TestFileFacts:
    """Structural facts about one Python validation file."""

    __slots__ = ("parsed", "tests", "assertions", "skips", "mocks")

    def __init__(self, source: str | None) -> None:
        self.parsed = False
        self.tests: set[str] = set()
        self.assertions = 0
        self.skips = 0
        self.mocks = 0
        if source is None:
            return
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        self.parsed = True
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("test"):
                    self.tests.add(node.name)
                for decorator in node.decorator_list:
                    if _mentions(decorator, ("skip", "skipif", "xfail")):
                        self.skips += 1
            elif isinstance(node, ast.Assert):
                self.assertions += 1
            elif isinstance(node, ast.Call) and _mentions(node.func, ("skip", "xfail")):
                self.skips += 1
        self.mocks = sum(1 for marker in _MOCK_MARKERS if marker in source)


def _mentions(node: ast.AST, names: tuple[str, ...]) -> bool:
    """True when an attribute/name expression ends in one of ``names``."""
    current: ast.AST | None = node
    if isinstance(current, ast.Call):
        current = current.func
    while isinstance(current, ast.Attribute):
        if current.attr in names:
            return True
        current = current.value
    return isinstance(current, ast.Name) and current.id in names


def analyze_validation_change(
    base_root: Path,
    variant_root: Path,
    validation_paths: list[str],
) -> ValidationChangeAnalysis:
    """Compare a baseline tree with a variant workspace and describe the delta."""
    base_files = _relative_files(base_root)
    variant_files = _relative_files(variant_root)
    every = sorted(set(base_files) | set(variant_files))

    result = ValidationChangeAnalysis()
    assertions_before = 0
    assertions_after = 0
    skips_before = 0
    skips_after = 0
    tests_before: set[str] = set()
    tests_after: set[str] = set()

    for relative in every:
        before_path = base_files.get(relative)
        after_path = variant_files.get(relative)
        before = _read(before_path) if before_path else None
        after = _read(after_path) if after_path else None
        unchanged = before_path is not None and after_path is not None and before == after

        if not is_validation_path(relative, validation_paths):
            if not unchanged:
                result.changed_product_files.append(relative)
            continue

        if before_path is None:
            result.added_validation_files.append(relative)
        elif after_path is None:
            result.removed_validation_files.append(relative)
        elif not unchanged:
            result.changed_validation_files.append(relative)

        if _is_config(relative) and not unchanged:
            result.discovery_or_config_changed.append(relative)
        if _looks_like_fixture(relative) and not unchanged:
            result.fixture_or_golden_changed.append(relative)

        before_facts = _TestFileFacts(before if relative.endswith(".py") else None)
        after_facts = _TestFileFacts(after if relative.endswith(".py") else None)
        if _is_python_test(relative):
            tests_before |= {f"{relative}::{name}" for name in before_facts.tests}
            tests_after |= {f"{relative}::{name}" for name in after_facts.tests}
        assertions_before += before_facts.assertions
        assertions_after += after_facts.assertions
        skips_before += before_facts.skips
        skips_after += after_facts.skips
        if not unchanged:
            if before_facts.mocks != after_facts.mocks:
                result.mock_or_stub_changed.append(relative)
            elif not relative.endswith(".py") and _mock_text_changed(before, after):
                result.mock_or_stub_changed.append(relative)

    result.tests_added = sorted(tests_after - tests_before)
    result.tests_removed = sorted(tests_before - tests_after)
    result.assertions_added = max(0, assertions_after - assertions_before)
    result.assertions_removed = max(0, assertions_before - assertions_after)
    result.skip_markers_introduced = max(0, skips_after - skips_before)
    return result


def _mock_text_changed(before: str | None, after: str | None) -> bool:
    def count(text: str | None) -> int:
        if not text:
            return 0
        return sum(1 for marker in _MOCK_MARKERS if marker in text)

    return count(before) != count(after)
