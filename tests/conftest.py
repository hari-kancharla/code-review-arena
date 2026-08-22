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


@pytest.fixture
def benchmark_dir() -> Path:
    return Path("benchmark_sets/v1")
