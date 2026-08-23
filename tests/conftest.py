from pathlib import Path

import pytest


def pack_case_count(name: str = "v1") -> int:
    """Cases a shipped pack declares.

    Derived from the manifest rather than hardcoded so that deliberately adding
    or removing a case updates every expectation at once instead of leaving a
    scatter of stale literals to chase.
    """
    from arena.benchmark.case_loader import load_manifest

    return len(load_manifest(Path("benchmark_sets") / name).cases)


def shipped_case_packs() -> set[str]:
    """Packs of ordinary benchmark cases, by what their manifest declares.

    A pack is identified by structure, not by name: a manifest with ``cases``
    holds ordinary review cases and belongs in the catalogue, the dashboard table
    and the README table. The integrity track's packs declare ``pairs`` instead --
    a pair is a genuine and a compromised pull request against one task, not a
    case, and the case-serving surfaces cannot load one. Deriving the set this way
    keeps the guarantee that a NEW ordinary pack must be published everywhere,
    without hardcoding the name of the one pack that is a different shape.
    """
    import yaml

    packs = set()
    for path in Path("benchmark_sets").iterdir():
        manifest = path / "manifest.yaml"
        if not manifest.is_file():
            continue
        document = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if "cases" in document:
            packs.add(path.name)
    return packs


@pytest.fixture
def benchmark_dir() -> Path:
    return Path("benchmark_sets/v1")
