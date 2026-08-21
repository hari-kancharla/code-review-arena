"""The minimum-deps constraints file must keep matching the declared floors.

CI's "Minimum deps" job installs with `-c constraints/minimum.pip` to prove the
project still works on the oldest dependencies it claims to support. That proof is
only real while the pins equal the floors in pyproject.toml. An automated bump (or
a hand edit) that raises them makes the job silently test something else, so it is
asserted here rather than trusted.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
CONSTRAINTS = Path("constraints/minimum.pip")

# Floors that the constraints file is required to pin exactly. Transitive pins
# (starlette, pydantic-core, httpcore, anyio) exist only to keep the resolution
# coherent and are deliberately not derived from pyproject.
FLOOR_PACKAGES = {"fastapi", "pydantic", "httpx"}


def _version_tuple(text: str) -> tuple[int, ...]:
    """Compare 0.110 and 0.110.0 as equal without depending on `packaging`."""
    return tuple(int(part) for part in re.findall(r"\d+", text))


def _padded(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple, tuple]:
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def _declared_floors() -> dict[str, str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    floors: dict[str, str] = {}
    for spec in data["project"]["dependencies"]:
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*>=\s*([0-9][0-9A-Za-z.*+!-]*)", spec)
        if match and match.group(1).lower() in FLOOR_PACKAGES:
            floors[match.group(1).lower()] = match.group(2)
    return floors


def _pinned() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, version = line.partition("==")
        assert sep, f"constraints must use exact == pins, got: {line!r}"
        pins[name.strip().lower()] = version.strip()
    return pins


def test_constraints_file_is_present_and_not_a_dependabot_manifest():
    assert CONSTRAINTS.is_file(), f"{CONSTRAINTS} is missing"
    # A requirements-shaped *.txt is picked up by Dependabot as a manifest to
    # upgrade, which is exactly what this file must never be.
    assert CONSTRAINTS.suffix != ".txt"


def test_every_declared_floor_is_pinned_at_that_floor():
    floors = _declared_floors()
    pins = _pinned()
    assert set(floors) == FLOOR_PACKAGES, f"unexpected floors parsed from pyproject: {floors}"
    for name, floor in floors.items():
        assert name in pins, f"{name} declares floor >={floor} but is not pinned in {CONSTRAINTS}"
        left, right = _padded(_version_tuple(pins[name]), _version_tuple(floor))
        assert left == right, (
            f"{name} is pinned at {pins[name]} but pyproject declares floor >={floor}; "
            f"the minimum-deps job would no longer be testing the minimum."
        )
