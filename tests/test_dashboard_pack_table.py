"""Every published case count must agree with the shipped manifests.

The home page renders without an API, so each pack's case count is duplicated in
TypeScript, and the README repeats the same table for readers who never run the
dashboard. A duplicated constant rots, and both copies did: removing one case from
v1 left the page advertising "33 cases across 4 packs" when the packs held 32, and
the README went on claiming v1 had ten cases after it had nine. These tests are the
guard that makes the duplication safe -- a pack that gains or loses a case fails
here until every published count is updated.
"""

import re
from pathlib import Path

import yaml

from tests.conftest import shipped_case_packs

PAGE = Path("dashboard/src/app/page.tsx")
README = Path("README.md")
PACKS = Path("benchmark_sets")

# { id: "benchmark_sets/<name>", label: "<name>", cases: <n>, ...
_ENTRY = re.compile(
    r'id:\s*"benchmark_sets/(?P<name>[A-Za-z0-9_]+)"[^}]*?cases:\s*(?P<cases>\d+)',
    re.DOTALL,
)


def _declared_in_dashboard() -> dict[str, int]:
    source = PAGE.read_text(encoding="utf-8")
    return {m.group("name"): int(m.group("cases")) for m in _ENTRY.finditer(source)}


def _actual_from_manifests(names: list[str]) -> dict[str, int]:
    counts = {}
    for name in names:
        manifest = yaml.safe_load((PACKS / name / "manifest.yaml").read_text(encoding="utf-8"))
        counts[name] = len(manifest["cases"])
    return counts


def test_dashboard_case_counts_match_the_manifests():
    declared = _declared_in_dashboard()
    assert declared, "no pack entries parsed from the dashboard page"

    assert declared == _actual_from_manifests(sorted(declared))


def test_dashboard_lists_every_shipped_pack():
    """A pack that ships but is not listed is invisible to every reader."""
    shipped = shipped_case_packs()

    assert set(_declared_in_dashboard()) == shipped


# | `benchmark_sets/<name>` | <n> | ...
_README_ROW = re.compile(
    r"^\|\s*`benchmark_sets/(?P<name>[A-Za-z0-9_]+)`\s*\|\s*(?P<cases>\d+)\s*\|",
    re.MULTILINE,
)


def _declared_in_readme() -> dict[str, int]:
    source = README.read_text(encoding="utf-8")
    return {m.group("name"): int(m.group("cases")) for m in _README_ROW.finditer(source)}


def test_readme_case_counts_match_the_manifests():
    declared = _declared_in_readme()
    assert declared, "no pack rows parsed from the README table"

    assert declared == _actual_from_manifests(sorted(declared))


def test_readme_lists_every_shipped_pack():
    shipped = shipped_case_packs()

    assert set(_declared_in_readme()) == shipped


def test_readme_total_matches_the_sum_of_the_manifests():
    """The prose total is a third copy of the same fact, and it rotted too."""
    total = sum(_actual_from_manifests(sorted(_declared_in_readme())).values())

    assert f"({total} cases across" in README.read_text(encoding="utf-8")
