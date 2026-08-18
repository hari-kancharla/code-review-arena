"""Strict, bounded, deterministic provenance for an imported case."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, StringConstraints

from arena.core import limits
from arena.core.errors import ImportFixError
from arena.core.models import _StrictExternal
from arena.security.paths import SafeDirPath, SafeFilePath

PROVENANCE_SCHEMA_VERSION: Literal["3"] = "3"
DIFF_POLICY_VERSION = "1"

_LABEL_COMPONENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _check_source_label(value: str) -> str:
    """A stable ``owner/repository``-style label: no path/scheme/whitespace/control."""
    if not value or len(value) > limits.IMPORT_SOURCE_LABEL_LEN:
        raise ValueError("source label has an invalid length")
    if any(ord(ch) < 0x20 or ch.isspace() for ch in value):
        raise ValueError("source label contains whitespace or control characters")
    if ":" in value or "\\" in value or value.startswith("/"):
        raise ValueError("source label must not contain a scheme, drive, or absolute path")
    components = value.split("/")
    if not 1 <= len(components) <= 3:
        raise ValueError("source label must have 1 to 3 portable components")
    for component in components:
        if component in {".", ".."} or not _LABEL_COMPONENT.match(component):
            raise ValueError(f"source label component is not portable: {component!r}")
    return value


def validate_source_label(value: str) -> None:
    """Eagerly validate a source label, raising a stable ImportFixError on failure."""
    try:
        _check_source_label(value)
    except ValueError as exc:
        raise ImportFixError("invalid_source_label", str(exc)) from exc


SourceLabel = Annotated[str, AfterValidator(_check_source_label)]
_Hex = Annotated[str, StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
# A commit date, normalized to UTC so the recorded text does not vary with the
# committer's local offset.
_IsoUtc = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")]


class Provenance(_StrictExternal):
    """Deterministic provenance: no generation time, host, user, or local paths.

    A commit's OWN author and committer dates are recorded. They are properties of
    the commit object already named by the recorded object id, not properties of
    the import run, so they are identical on every re-import of the same pair and
    the pack stays byte-reproducible. The banned "time" is the importer's wall
    clock, which would differ per run.

    Schema "2" predates those dates and stays loadable: packs already shipped are
    v2 on disk and must never become unreadable.
    """

    provenance_schema_version: Literal["2", "3"]
    mode: Literal["reverse_fix"]
    source_label: SourceLabel | None
    object_format: Literal["sha1", "sha256"]
    diff_policy_version: str
    buggy_commit: _Hex
    fixed_commit: _Hex
    merge_base: _Hex
    source_paths: list[SafeFilePath]
    tests_root: SafeDirPath | None
    buggy_source_files: list[SafeFilePath]
    fixed_source_files: list[SafeFilePath]
    fixed_test_files: list[SafeFilePath]
    changed_source_paths: list[SafeFilePath]
    changed_test_paths: list[SafeFilePath]
    pr_diff_sha256: str
    reference_patch_sha256: str
    # Optional so a v2 record still validates. Field names deliberately avoid the
    # substring "time": tests/test_import_fix.py asserts the provenance document
    # contains no wall-clock reading, and matches on that substring.
    fixed_commit_author_date: _IsoUtc | None = None
    fixed_commit_committer_date: _IsoUtc | None = None
    buggy_commit_committer_date: _IsoUtc | None = None
