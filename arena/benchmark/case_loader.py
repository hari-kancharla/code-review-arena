"""Load benchmark manifests and complete case contexts from disk."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError as SchemaError

from arena.benchmark.diff_loader import load_diff
from arena.core.bounded_io import read_text_capped, read_yaml_mapping_bounded
from arena.core.errors import ValidationError
from arena.core.limits import CASE_YAML_BYTES, MANIFEST_BYTES, PACK_FILE_BYTES
from arena.core.models import (
    BenchmarkCase,
    CaseContext,
    CaseManifest,
    CaseOrigin,
    ReviewerCaseMetadata,
)
from arena.execution.integrity import find_unsafe_files
from arena.patching.patch_parser import touched_files
from arena.security.paths import resolve_under, validate_case_id


@dataclass(frozen=True)
class ContextLimits:
    """Bounds on reviewer-visible file content injected per case."""

    max_files: int = 40
    max_total_bytes: int = 262_144
    max_file_bytes: int = 65_536

    @classmethod
    def from_env(cls) -> ContextLimits:
        return cls(
            max_files=int(os.getenv("ARENA_MAX_CONTEXT_FILES", str(cls.max_files))),
            max_total_bytes=int(os.getenv("ARENA_MAX_CONTEXT_BYTES", str(cls.max_total_bytes))),
            max_file_bytes=int(os.getenv("ARENA_MAX_CONTEXT_FILE_BYTES", str(cls.max_file_bytes))),
        )


def load_manifest(benchmark_dir: Path) -> CaseManifest:
    manifest_path = benchmark_dir / "manifest.yaml"
    # Bound the raw bytes and reject aliases/non-mapping roots BEFORE schema parsing.
    # Byte, encoding, and YAML errors propagate as typed ValidationError subclasses;
    # only the schema error is rewrapped so Pydantic field locations survive.
    manifest_data = read_yaml_mapping_bounded(manifest_path, MANIFEST_BYTES, label="manifest.yaml")
    try:
        return CaseManifest.model_validate(manifest_data)
    except SchemaError as exc:
        raise ValidationError(f"Invalid manifest {manifest_path}: {exc}") from exc


def load_case(case_dir: Path) -> BenchmarkCase:
    # A symlinked case directory would let the loader read and (after copy) execute
    # files outside the pack. find_unsafe_files walks the contents but os.walk
    # follows a symlinked root, so the root itself must be rejected here.
    if case_dir.is_symlink():
        raise ValidationError(f"case directory is a symlink: {case_dir}")
    config_path = case_dir / "case.yaml"
    case_data = read_yaml_mapping_bounded(config_path, CASE_YAML_BYTES, label="case.yaml")
    try:
        case = BenchmarkCase.model_validate(case_data)
    except SchemaError as exc:
        raise ValidationError(f"Invalid case metadata in {config_path}: {exc}") from exc
    unsafe = find_unsafe_files(case_dir)
    if unsafe:
        raise ValidationError(
            f"Case {case_dir.name} contains unsafe paths (symlinks or special files): "
            f"{', '.join(unsafe)}"
        )
    # The case id and its input path fields are attacker-controlled and become
    # physical paths; reject any that are not slugs or that escape the case dir.
    validate_case_id(case.id)
    for field in (case.input.diff, case.input.before_dir, case.input.after_dir):
        resolve_under(case_dir, field)
    if case.input.tests_dir is not None:
        resolve_under(case_dir, case.input.tests_dir)
    case.case_dir = case_dir
    return case


def load_cases(benchmark_dir: Path) -> list[BenchmarkCase]:
    if benchmark_dir.is_symlink():
        raise ValidationError(f"benchmark root is a symlink: {benchmark_dir}")
    manifest = load_manifest(benchmark_dir)
    seen_folded: dict[str, str] = {}
    for case_id in manifest.cases:
        # Slug-validate, reject a symlinked case directory, and resolve the path
        # under the benchmark root so a manifest entry cannot escape the pack
        # (a symlinked case dir would otherwise become an accepted outside root).
        validate_case_id(case_id)
        if (benchmark_dir / case_id).is_symlink():
            raise ValidationError(f"case directory is a symlink: {case_id}")
        resolve_under(benchmark_dir, case_id)
        # Two ids that differ only in case collide on a case-insensitive filesystem
        # (the directory name is the id), so reject the collision up front.
        folded = case_id.casefold()
        if folded in seen_folded:
            raise ValidationError(
                f"case ids collide case-insensitively: {seen_folded[folded]!r} and {case_id!r}"
            )
        seen_folded[folded] = case_id
    cases = []
    for case_id in manifest.cases:
        case = load_case(benchmark_dir / case_id)
        # Identity invariant: manifest id == directory name (by construction here)
        # == BenchmarkCase.id. A case.yaml whose id disagrees with its directory is
        # rejected rather than silently scored under the wrong identity.
        if case.id != case_id:
            raise ValidationError(
                f"case id mismatch: manifest/directory is {case_id!r} but its case.yaml "
                f"declares id {case.id!r}"
            )
        cases.append(case)
    if manifest.default_docker_image:
        for case in cases:
            if case.execution.docker_image is None:
                case.execution.docker_image = manifest.default_docker_image
    if manifest.default_origin_kind:
        for case in cases:
            # Only ever fills in an UNDECLARED origin; a case that states its own
            # kind always wins over the pack default.
            declared = case.origin
            if declared is not None and declared.kind != "unknown":
                continue
            if manifest.default_origin_kind == "authored":
                # An authored case carries no upstream date, so this is always a
                # coherent CaseOrigin.
                case.origin = CaseOrigin(kind="authored")
            elif declared is not None and declared.public_fix_date is not None:
                # derived_public requires a date, which only an already-dated
                # case can supply. Promoting a dateless case would build a
                # CaseOrigin its own validator rejects.
                case.origin = declared.model_copy(update={"kind": "derived_public"})
    return cases


def _read_relevant_files(
    case: BenchmarkCase, diff_text: str, limits: ContextLimits
) -> tuple[dict[str, str], bool, list[str]]:
    """Read after-dir files bounded by limits, diff-referenced files first."""
    assert case.case_dir is not None
    after_dir = case.case_dir / case.input.after_dir
    all_paths = sorted(
        path.relative_to(after_dir).as_posix() for path in after_dir.rglob("*") if path.is_file()
    )
    available = set(all_paths)
    diff_first = [path for path in touched_files(diff_text) if path in available]
    ordered = diff_first + [path for path in all_paths if path not in set(diff_first)]

    contents: dict[str, str] = {}
    omitted: list[str] = []
    truncated = False
    total_bytes = 0
    for relative in ordered:
        if len(contents) >= limits.max_files or total_bytes >= limits.max_total_bytes:
            omitted.append(relative)
            continue
        # Cap the read at the per-file budget so a pathologically large pack file
        # is never pulled fully into memory before truncation.
        text, file_truncated = read_text_capped(
            after_dir / relative, min(limits.max_file_bytes, PACK_FILE_BYTES), label="pack file"
        )
        if file_truncated:
            text = text + "\n…[truncated by arena: file exceeds max_file_bytes]"
            truncated = True
        size = len(text.encode("utf-8"))
        if total_bytes + size > limits.max_total_bytes:
            omitted.append(relative)
            total_bytes = limits.max_total_bytes  # budget exhausted; omit the rest too
            continue
        contents[relative] = text
        total_bytes += size
    return contents, truncated or bool(omitted), omitted


def build_context(
    case: BenchmarkCase,
    test_output: str = "",
    static_analysis_output: str = "",
    limits: ContextLimits | None = None,
) -> CaseContext:
    assert case.case_dir is not None
    diff_text = load_diff(case.case_dir / case.input.diff)
    relevant_files, truncated, omitted = _read_relevant_files(
        case, diff_text, limits or ContextLimits.from_env()
    )
    return CaseContext(
        case=ReviewerCaseMetadata(
            id=case.id,
            title=case.title,
            category=case.category,
            severity=case.severity,
            stack=case.stack,
            description=case.description,
        ),
        diff=diff_text,
        relevant_files=relevant_files,
        context_truncated=truncated,
        omitted_files=omitted,
        test_output=test_output,
        static_analysis_output=static_analysis_output,
        case_dir=case.case_dir,
    )
