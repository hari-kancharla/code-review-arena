"""Runtime configuration helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path

# Pack directory names are identifiers: letters, digits, dot, underscore, dash,
# and never a name made only of dots (which would resolve to the root itself).
_PACK_NAME = re.compile(r"(?!\.+$)[A-Za-z0-9][A-Za-z0-9._-]*")

DEFAULT_BENCHMARK_SET = Path("benchmark_sets/v1")
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_DB_PATH = Path("arena.db")
# v2 removed test/static-analysis output from the default (blind) reviewer payload;
# it is now opt-in via --reveal-test-output and recorded as a test-assisted run.
PROMPT_VERSION = "v2"

# Version of the JSON the dashboard consumes (audit report + verification snapshot).
# Bump on any breaking shape change; the dashboard refuses to render a mismatch.
REPORT_SCHEMA_VERSION = "1.0"

# Version of the reviewer output contract (ReviewResult JSON). Bump on any
# breaking change to the shape a reviewer wrapper must emit.
REVIEW_SCHEMA_VERSION = "1.0"


def project_root() -> Path:
    """Anchor for default paths so commands behave the same from any directory.

    ARENA_PROJECT_ROOT wins; otherwise walk up from the working directory to
    the first directory containing a benchmark_sets dir or a pyproject.toml,
    falling back to the working directory itself.
    """
    env = os.getenv("ARENA_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "benchmark_sets").is_dir() or (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def database_path() -> Path:
    value = os.getenv("ARENA_DB_PATH")
    return Path(value) if value else project_root() / DEFAULT_DB_PATH


def runs_path() -> Path:
    value = os.getenv("ARENA_RUNS_DIR")
    return Path(value) if value else project_root() / DEFAULT_RUNS_DIR


def benchmark_root() -> Path:
    """Directory that contains the named benchmark packs the server may load."""
    value = os.getenv("ARENA_BENCHMARK_ROOT")
    return Path(value) if value else project_root() / "benchmark_sets"


def trusted_pack_hashes() -> set[str]:
    """Operator-allowlisted pack checksums permitted to run tests on the host.

    Local execution runs fixture-owned test commands on the host (a convenience,
    not a security boundary). When ARENA_TRUSTED_PACK_HASHES is set (a space- or
    comma-separated list of pack sha256 values), local execution is restricted to
    packs whose checksum is listed, so a single --allow-local-execution opt-in no
    longer trusts every pack. An empty value (the default) keeps the prior
    behavior where the flag alone permits local execution.
    """
    raw = os.getenv("ARENA_TRUSTED_PACK_HASHES", "")
    return {token.strip() for token in raw.replace(",", " ").split() if token.strip()}


def resolve_benchmark_set(name: str) -> Path | None:
    """Resolve a stored benchmark-set name to a directory under benchmark_root.

    Names are identifiers, not paths, so only identifier characters are allowed.
    An allowlist rather than a blocklist of separators: a blocklist admitted a
    bare "." (and " ."), which pathlib normalizes away, so the "pack" resolved to
    the benchmark root itself -- snapshotting and hashing every shipped pack
    before failing on the missing manifest.
    """
    if not name or not _PACK_NAME.fullmatch(name):
        return None
    candidate = benchmark_root() / name
    return candidate if candidate.is_dir() else None


def resolve_benchmark_path(path: Path) -> Path:
    """Resolve a CLI benchmark-set path against the project root when needed."""
    if path.is_absolute() or path.exists():
        return path
    candidate = project_root() / path
    return candidate if candidate.exists() else path
