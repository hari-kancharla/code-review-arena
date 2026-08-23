"""Load integrity packs, pairs and their trust zones from disk.

The loader is the single place the three trust zones become physical paths, so
it is also the single place their separation is enforced:

* a variant workspace may never contain the oracle mount path;
* the oracle directory may never live inside a variant workspace;
* every declared candidate-validation path must exist in at least one workspace.

Everything else mirrors the ordinary pack loader: symlinks and special files are
rejected, byte and structure limits are applied before schema parsing, and every
attacker-controlled path is resolved back under its declared root.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError as SchemaError

from arena.core.bounded_io import read_text_bounded, read_yaml_mapping_bounded
from arena.core.errors import ValidationError
from arena.core.limits import CASE_YAML_BYTES, DIFF_BYTES, MANIFEST_BYTES, PACK_FILE_BYTES
from arena.execution.integrity import find_unsafe_files
from arena.integrity.models import (
    AlternateImplementation,
    CandidateVariant,
    IntegrityManifest,
    IntegrityPair,
)
from arena.security.paths import resolve_under, validate_case_id

PAIR_FILENAME = "pair.yaml"
MANIFEST_FILENAME = "manifest.yaml"


def load_integrity_manifest(pack_dir: Path) -> IntegrityManifest:
    data = read_yaml_mapping_bounded(
        pack_dir / MANIFEST_FILENAME, MANIFEST_BYTES, label="integrity manifest.yaml"
    )
    try:
        return IntegrityManifest.model_validate(data)
    except SchemaError as exc:
        raise ValidationError(
            f"Invalid integrity manifest {pack_dir / MANIFEST_FILENAME}: {exc}"
        ) from exc


def _require_dir(root: Path, relative: str, label: str) -> Path:
    target = resolve_under(root, relative)
    if not target.is_dir():
        raise ValidationError(f"{label} directory is missing: {relative}")
    return target


def _require_file(root: Path, relative: str, label: str) -> Path:
    target = resolve_under(root, relative)
    if not target.is_file():
        raise ValidationError(f"{label} file is missing: {relative}")
    return target


def workspace_dir(pair: IntegrityPair, variant: CandidateVariant) -> Path:
    assert pair.pair_dir is not None
    return resolve_under(pair.pair_dir / variant.id, variant.workspace_dir)


def alternate_dir(pair: IntegrityPair, alternate: AlternateImplementation) -> Path:
    assert pair.pair_dir is not None
    return resolve_under(pair.pair_dir, alternate.workspace_dir)


def base_dir(pair: IntegrityPair) -> Path:
    assert pair.pair_dir is not None
    return resolve_under(pair.pair_dir, pair.base_dir)


def oracle_dir(pair: IntegrityPair) -> Path:
    assert pair.pair_dir is not None
    return resolve_under(pair.pair_dir, pair.trusted_oracle.dir)


def variant_diff(pair: IntegrityPair, variant: CandidateVariant) -> str:
    assert pair.pair_dir is not None
    path = _require_file(pair.pair_dir / variant.id, variant.diff, "variant diff")
    return read_text_bounded(path, DIFF_BYTES, label="pr.diff")


def repair_patch(pair: IntegrityPair, variant: CandidateVariant, kind: str) -> str | None:
    """Read one shipped repair artifact (``reference`` / ``test_only`` / ``product_overfit``)."""
    assert pair.pair_dir is not None
    relative = getattr(variant.repairs, kind, None)
    if not relative:
        return None
    path = _require_file(pair.pair_dir / variant.id, relative, f"{kind} repair")
    return read_text_bounded(path, PACK_FILE_BYTES, label=f"{kind}.patch")


def relative_files(root: Path) -> list[str]:
    """Every regular file under ``root`` as sorted POSIX-relative paths."""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


#: Build artifacts that must never be shipped inside a pair. A ``.pyc`` embeds the
#: absolute path of the machine that produced it, so a stray bytecode cache is both
#: non-reproducible and a real leak channel into the reviewer payload.
_REJECTED_ARTIFACT_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"})
_REJECTED_ARTIFACT_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".class")


def _check_no_build_artifacts(pair_dir: Path) -> None:
    """Reject compiled or cached build output anywhere in a pair."""
    offenders: list[str] = []
    for path in pair_dir.rglob("*"):
        relative = path.relative_to(pair_dir).as_posix()
        if path.is_dir():
            if path.name in _REJECTED_ARTIFACT_DIRS:
                offenders.append(relative)
        elif path.name.endswith(_REJECTED_ARTIFACT_SUFFIXES):
            offenders.append(relative)
    if offenders:
        raise ValidationError(
            f"pair {pair_dir.name} ships build artifacts, which are not reproducible and can "
            f"carry absolute paths into a reviewer payload: {', '.join(sorted(offenders)[:5])}"
        )


def _check_zone_separation(pair: IntegrityPair) -> None:
    """Prove the oracle is unreachable from, and invisible in, every workspace."""
    assert pair.pair_dir is not None
    mount = pair.trusted_oracle.mount
    oracle_root = oracle_dir(pair)
    if not oracle_root.is_dir():
        raise ValidationError(f"trusted oracle directory is missing: {pair.trusted_oracle.dir}")
    if not relative_files(oracle_root):
        raise ValidationError("trusted oracle directory contains no files")

    workspaces: list[tuple[str, Path]] = [
        (variant.id, workspace_dir(pair, variant)) for variant in pair.variants()
    ]
    workspaces += [(item.id, alternate_dir(pair, item)) for item in pair.alternates]
    workspaces.append(("base", base_dir(pair)))

    oracle_resolved = oracle_root.resolve()
    for label, root in workspaces:
        if not root.is_dir():
            raise ValidationError(f"workspace directory is missing for {label}")
        resolved = root.resolve()
        if resolved == oracle_resolved or resolved in oracle_resolved.parents:
            raise ValidationError(
                f"trusted oracle directory sits inside the {label} workspace; the candidate "
                "author would own it"
            )
        files = relative_files(root)
        clash = [path for path in files if path == mount or path.startswith(f"{mount}/")]
        if clash:
            raise ValidationError(
                f"workspace {label} contains the trusted-oracle mount path {mount!r}: "
                f"{', '.join(clash[:5])}"
            )
        if (root / mount).exists():
            raise ValidationError(f"workspace {label} already contains a {mount!r} entry")


def _check_validation_paths(pair: IntegrityPair) -> None:
    """Every declared candidate-validation path must match something real."""
    declared = list(pair.candidate_validation.paths)
    seen: set[str] = set()
    for variant in pair.variants():
        for relative in relative_files(workspace_dir(pair, variant)):
            for prefix in declared:
                if relative == prefix or relative.startswith(f"{prefix}/"):
                    seen.add(prefix)
    missing = [prefix for prefix in declared if prefix not in seen]
    if missing:
        raise ValidationError(
            "candidate validation paths match no workspace file: " + ", ".join(missing)
        )


def load_pair(pair_dir: Path) -> IntegrityPair:
    if pair_dir.is_symlink():
        raise ValidationError(f"pair directory is a symlink: {pair_dir}")
    data = read_yaml_mapping_bounded(pair_dir / PAIR_FILENAME, CASE_YAML_BYTES, label=PAIR_FILENAME)
    try:
        pair = IntegrityPair.model_validate(data)
    except SchemaError as exc:
        raise ValidationError(
            f"Invalid integrity pair in {pair_dir / PAIR_FILENAME}: {exc}"
        ) from exc
    unsafe = find_unsafe_files(pair_dir)
    if unsafe:
        raise ValidationError(
            f"Pair {pair_dir.name} contains unsafe paths (symlinks or special files): "
            f"{', '.join(unsafe)}"
        )
    validate_case_id(pair.id)
    pair.pair_dir = pair_dir
    _require_dir(pair_dir, pair.base_dir, "baseline")
    for variant in pair.variants():
        validate_case_id(variant.id)
        variant_root = _require_dir(pair_dir, variant.id, f"variant {variant.id}")
        _require_dir(variant_root, variant.workspace_dir, f"variant {variant.id} workspace")
        _require_file(variant_root, variant.diff, f"variant {variant.id} diff")
        for kind in ("reference", "test_only", "product_overfit"):
            relative = getattr(variant.repairs, kind)
            if relative:
                _require_file(variant_root, relative, f"variant {variant.id} {kind} repair")
    for alternate in pair.alternates:
        _require_dir(pair_dir, alternate.workspace_dir, f"alternate {alternate.id}")
    _check_no_build_artifacts(pair_dir)
    _check_zone_separation(pair)
    _check_validation_paths(pair)
    return pair


def load_pairs(pack_dir: Path) -> list[IntegrityPair]:
    if pack_dir.is_symlink():
        raise ValidationError(f"integrity pack root is a symlink: {pack_dir}")
    manifest = load_integrity_manifest(pack_dir)
    seen: dict[str, str] = {}
    for pair_id in manifest.pairs:
        validate_case_id(pair_id)
        if (pack_dir / pair_id).is_symlink():
            raise ValidationError(f"pair directory is a symlink: {pair_id}")
        resolve_under(pack_dir, pair_id)
        folded = pair_id.casefold()
        if folded in seen:
            raise ValidationError(
                f"pair ids collide case-insensitively: {seen[folded]!r} and {pair_id!r}"
            )
        seen[folded] = pair_id
    pairs: list[IntegrityPair] = []
    for pair_id in manifest.pairs:
        pair = load_pair(pack_dir / pair_id)
        if pair.id != pair_id:
            raise ValidationError(
                f"pair id mismatch: manifest/directory is {pair_id!r} but its {PAIR_FILENAME} "
                f"declares id {pair.id!r}"
            )
        pairs.append(pair)
    if manifest.default_docker_image:
        for pair in pairs:
            if pair.docker_image is None:
                pair.docker_image = manifest.default_docker_image
    return pairs


def is_validation_path(relative: str, validation_paths: list[str]) -> bool:
    """True when a workspace-relative path is a candidate-owned validation artifact."""
    return any(
        relative == prefix or relative.startswith(f"{prefix}/") for prefix in validation_paths
    )
