"""Deterministic reverse-fix case generator.

Given a buggy commit B and a fixed commit F (B an ancestor of F) in a local Git
repository, generate a one-case candidate pack:

    after/           = selected source content at B (buggy)
    before/          = selected source content at F (fixed)
    reference.patch  = exact repair diff  B -> F   (after/ + reference.patch == F)
    pr.diff          = inverse review diff F -> B   (before/ + pr.diff == after/)
    tests/           = selected test content at F

This is a SYNTHETIC reverse-review case derived from a real historical fix; it is
not the original bug-introducing pull request. Generation reads committed objects
only (replacement refs ignored, lazy fetching disabled, shallow/grafted history
rejected), derives changes by comparing exact trees, and generates the patches in
a fresh isolated repository, so the source working tree, local config, attributes
and history overrides cannot affect the output. It does not execute repository
code or certify the result, and the same B/F/spec produce byte-identical output.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from arena.benchmark.contamination import scan_benchmark
from arena.benchmark.dataset_validator import validate_dataset
from arena.benchmark.pack_hash import write_checksum
from arena.core import limits
from arena.core.errors import ImportFixError
from arena.importer import diff_repo
from arena.importer import git_objects as g
from arena.importer.import_spec import ImportSpec, load_import_spec
from arena.importer.provenance import (
    DIFF_POLICY_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    Provenance,
    validate_source_label,
)
from arena.patching.git_pipeline import apply_patch


@dataclass
class ImportResult:
    output_path: Path
    case_id: str
    buggy_commit: str
    fixed_commit: str
    merge_base: str
    object_format: str
    buggy_source_file_count: int
    fixed_source_file_count: int
    union_source_file_count: int
    fixed_test_file_count: int
    repair_changed_paths: list[str] = field(default_factory=list)
    test_changed_paths: list[str] = field(default_factory=list)
    pack_checksum: str = ""
    public_fix_date: str = ""
    public_fix_date_basis: str = ""
    validation_ok: bool = False
    contamination_ok: bool = False
    certification: str = "not run"

    @property
    def source_file_count(self) -> int:
        """Compatibility alias, explicitly defined as the union source file count."""
        return self.union_source_file_count


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _write_exact(path: Path, data: bytes, mode: int) -> None:
    """Exclusive create + complete write + verify size, bytes and mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise ImportFixError("output_write_failure", "could not create a generated file") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            view = memoryview(data)
            while view:
                written = handle.write(view)
                if not written:
                    raise ImportFixError("output_write_failure", "zero-progress write")
                view = view[written:]
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ImportFixError("output_write_failure", "could not write a generated file") from exc
    os.chmod(path, mode)
    info = path.lstat()
    if info.st_size != len(data) or path.read_bytes() != data:
        raise ImportFixError("output_write_failure", "written file did not verify")
    if (info.st_mode & 0o777) != mode and os.name == "posix":
        raise ImportFixError("output_write_failure", "written file mode did not verify")


def _validate_source_selectors(raw_selectors: list[str], present: set[str]) -> None:
    """Reject duplicate, overlapping, or unmatched source selectors (no silent dedup)."""
    if len(raw_selectors) != len(set(raw_selectors)):
        raise ImportFixError("duplicate_selector", "source_paths has a duplicate selector")
    unique = sorted(set(raw_selectors))
    for a in unique:
        for b in unique:
            if a != b and (_under(a, b) or _under(b, a)):
                raise ImportFixError("overlapping_selector", f"selectors overlap: {a!r} and {b!r}")
    for selector in unique:
        if not any(p == selector or _under(p, selector) for p in present):
            raise ImportFixError(
                "selected_path_missing", f"source selector matches no file: {selector!r}"
            )


class _ImportBudget:
    """A single shared budget across all selected trees, enforced incrementally.

    Blobs are deduplicated by object id so an object shared between the buggy and
    fixed trees is read and retained once; the byte budget tracks retained (unique)
    bytes for memory protection, while the file count is the upfront output-file
    count. At any moment at most the retained set plus the one blob being inspected
    is held in memory.
    """

    def __init__(self, repo: g.Repo) -> None:
        self._repo = repo
        self._cache: dict[str, bytes] = {}
        self._retained_bytes = 0

    def load(self, oid: str) -> bytes:
        cached = self._cache.get(oid)
        if cached is not None:
            return cached
        data = g.cat_blob(self._repo, oid)  # one bounded blob inspected at a time
        if len(data) > limits.IMPORT_MAX_FILE_BYTES:
            raise ImportFixError(
                "import_limit_exceeded", "a selected file exceeds the per-file limit"
            )
        if self._retained_bytes + len(data) > limits.IMPORT_MAX_TOTAL_BYTES:
            raise ImportFixError("import_limit_exceeded", "selection exceeds the total byte limit")
        self._cache[oid] = data
        self._retained_bytes += len(data)
        return data


def _load_selected(
    repo: g.Repo, trees: list[dict[str, tuple[str, str]]]
) -> list[dict[str, tuple[str, bytes]]]:
    """Load every selected tree under one incremental budget (file count, then bytes).

    Rejects an excessive output-file count before reading any blob, then reads blobs
    one at a time, rejecting as soon as the next blob would exceed the byte budget so
    later blobs are never requested.
    """
    if sum(len(tree) for tree in trees) > limits.IMPORT_MAX_FILES:
        raise ImportFixError("import_limit_exceeded", "selection exceeds the file-count limit")
    budget = _ImportBudget(repo)
    loaded: list[dict[str, tuple[str, bytes]]] = []
    for tree in trees:
        out: dict[str, tuple[str, bytes]] = {}
        for path, (mode, oid) in sorted(tree.items()):
            out[path] = (mode, budget.load(oid))
        loaded.append(out)
    return loaded


def _require_text(files: dict[str, tuple[str, bytes]], changed: set[str]) -> None:
    """Reject a binary changed source blob (NUL byte or non-UTF-8), from exact bytes."""
    for path in changed:
        mode_data = files.get(path)
        if mode_data is None:
            continue  # deleted in this side; the other side is checked too
        _mode, data = mode_data
        if b"\x00" in data:
            raise ImportFixError("binary_change", f"binary source change is not supported: {path}")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImportFixError("binary_change", f"non-UTF-8 source change: {path}") from exc


def _source_changes(
    buggy: dict[str, tuple[str, bytes]], fixed: dict[str, tuple[str, bytes]]
) -> set[str]:
    """Paths whose (mode, bytes) differ between the selected buggy and fixed trees."""
    changed: set[str] = set()
    for path in set(buggy) | set(fixed):
        if buggy.get(path) != fixed.get(path):
            changed.add(path)
    return changed


def _build_case_yaml(
    spec: ImportSpec,
    has_tests: bool,
    *,
    public_fix_date: str,
    public_fix_date_basis: str,
    source_label: str | None,
) -> str:
    case = spec.to_case()
    ground_truth = spec.ground_truth.model_dump(mode="json")
    ground_truth.pop("primary_bug", None)
    input_doc = {"diff": "pr.diff", "before_dir": "before", "after_dir": "after"}
    if has_tests:
        input_doc["tests_dir"] = "tests"
    case_doc = {
        "id": case.id,
        "title": case.title,
        "category": case.category,
        "severity": case.severity,
        "stack": list(case.stack),
        "description": case.description,
        "input": input_doc,
        "ground_truth": ground_truth,
        "scoring": spec.scoring.model_dump(mode="json"),
        "execution": spec.execution.model_dump(mode="json"),
        "validation": spec.validation.model_dump(mode="json"),
        "metrics": spec.metrics.model_dump(mode="json"),
    }
    # Every imported case is by construction derived from public history, so it
    # is stamped as such here rather than left to a human to remember. safe_dump
    # renders this str as a quoted scalar; even if it did not, CaseOrigin's
    # before-validator accepts PyYAML's native date, so both spellings load.
    origin_doc: dict[str, object] = {
        "kind": "derived_public",
        "public_fix_date": public_fix_date,
        "public_fix_date_basis": public_fix_date_basis,
    }
    if source_label is not None:
        origin_doc["source_label"] = source_label
    if spec.origin is not None and spec.origin.upstream_ref is not None:
        origin_doc["upstream_ref"] = spec.origin.upstream_ref
    case_doc["origin"] = origin_doc
    return yaml.safe_dump(case_doc, sort_keys=True, default_flow_style=False)


def _verify_reproduces(
    source_dir: Path,
    source_tree: dict[str, tuple[str, bytes]],
    patch_text: str,
    expected: dict[str, tuple[str, bytes]],
    reason: str,
) -> None:
    """Apply patch via the Phase 1D pipeline; require exact bytes AND Git modes.

    ``source_tree`` and ``expected`` map path -> (mode, bytes). Produced modes are
    derived from the input modes plus the Phase 1D authoritative change records
    (GitChange.new_mode / deletions), so the proof is portable; on POSIX the result
    worktree's executable bit is checked as additional evidence.
    """
    expected_bytes = {p: data for p, (_mode, data) in expected.items()}
    expected_modes = {p: mode for p, (mode, _data) in expected.items()}
    with tempfile.TemporaryDirectory(prefix="arena-import-verify-") as tmp:
        dest = Path(tmp) / "ws"
        result = apply_patch(
            source_dir=source_dir, patch_text=patch_text, protected_paths=[], destination=dest
        )
        if not result.applied or result.workspace is None:
            raise ImportFixError(reason, f"authoritative apply failed: {result.reason}")
        produced_bytes = {
            p.relative_to(result.workspace).as_posix(): p.read_bytes()
            for p in result.workspace.rglob("*")
            if p.is_file()
        }
        if produced_bytes != expected_bytes:
            raise ImportFixError(reason, "patched tree bytes do not match the expected tree")
        # Derive produced modes authoritatively: input modes adjusted by Git's changes.
        produced_modes = {p: mode for p, (mode, _data) in source_tree.items()}
        for change in result.changes:
            if change.status == "D":
                produced_modes.pop(change.old_path or change.new_path, None)
            else:
                produced_modes[change.new_path] = change.new_mode
        if produced_modes != expected_modes:
            raise ImportFixError(reason, "patched tree modes do not match the expected tree")
        if os.name == "posix":  # additional, non-authoritative evidence
            for rel, mode in expected_modes.items():
                executable = bool((result.workspace / rel).lstat().st_mode & 0o111)
                if executable != (mode == "100755"):
                    raise ImportFixError(reason, f"result executable bit mismatch: {rel}")


def _validate_output_parent(parent: Path) -> None:
    """Preflight the output parent before any staging file is created."""
    try:
        info = os.lstat(parent)
    except FileNotFoundError as exc:
        raise ImportFixError(
            "output_parent_missing", f"output parent does not exist: {parent}"
        ) from exc
    except OSError as exc:
        raise ImportFixError("output_parent_invalid", "output parent is not accessible") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ImportFixError("output_parent_invalid", "output parent is a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise ImportFixError("output_parent_invalid", "output parent is not a directory")


def _publish(staging: Path, output: Path) -> None:
    """No-overwrite publish: claim ``output`` atomically with mkdir, then move contents in."""
    parent = output.parent
    if not (parent.is_dir() and not parent.is_symlink()):
        raise ImportFixError("output_write_failure", "output parent is not a real directory")
    try:
        os.lstat(output)
        raise ImportFixError("output_exists", f"output already exists: {output}")
    except FileNotFoundError:
        pass
    try:
        os.mkdir(output)  # atomic no-clobber claim; fails if anything exists there
    except FileExistsError as exc:
        raise ImportFixError("output_exists", f"output already exists: {output}") from exc
    try:
        for entry in sorted(staging.iterdir()):
            os.rename(entry, output / entry.name)
    except OSError as exc:
        shutil.rmtree(output, ignore_errors=True)
        raise ImportFixError(
            "output_write_failure", "could not publish the candidate pack"
        ) from exc


def import_fix(
    *,
    repo_path: Path,
    buggy_commit: str,
    fixed_commit: str,
    spec_path: Path,
    output: Path,
    source_label: str | None = None,
) -> ImportResult:
    """Generate a deterministic reverse-fix candidate pack. See module docstring."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ImportFixError("output_exists", f"output already exists: {output}")
    if source_label is not None:
        validate_source_label(source_label)
    spec = load_import_spec(Path(spec_path))

    raw_selectors = list(spec.source_paths)
    source_paths = sorted(set(raw_selectors))
    tests_root = spec.tests_root
    needs_tests = spec.execution.run_tests or spec.validation.tests_required
    if needs_tests and not tests_root:
        raise ImportFixError("tests_root_missing", "tests are required but no tests_root was given")
    if tests_root is not None:
        for sp in source_paths:
            if _under(sp, tests_root) or _under(tests_root, sp):
                raise ImportFixError(
                    "source_test_overlap", f"source/tests overlap: {sp} vs {tests_root}"
                )

    with g.open_repo(Path(repo_path)) as repo:
        buggy = g.resolve_commit(repo, buggy_commit)
        fixed = g.resolve_commit(repo, fixed_commit)
        fixed_author_date, fixed_committer_date = g.commit_dates(repo, fixed)
        _buggy_author_date, buggy_committer_date = g.commit_dates(repo, buggy)
        base = g.merge_base(repo, buggy, fixed)
        if base is None:
            raise ImportFixError("commits_unrelated", "buggy and fixed commits are unrelated")
        if not g.is_ancestor(repo, buggy, fixed):
            raise ImportFixError("fixed_not_descendant", "fixed commit is not descended from buggy")

        # A dot-prefixed source selector must resolve, by committed-tree inspection, to
        # exactly one regular file (not a hidden directory). Checked before the bulk tree
        # read so the rejection names the cause precisely rather than as a generic
        # unsafe-tree-path error from a hidden directory's descendants.
        for selector in source_paths:
            if not selector.rsplit("/", 1)[-1].startswith("."):
                continue
            kinds = {g.classify_tree_path(repo, commit, selector) for commit in (buggy, fixed)}
            if "dir" in kinds:
                raise ImportFixError(
                    "hidden_directory_selector",
                    f"dot-prefixed source selector resolves to a directory: {selector!r}",
                )
            if "other" in kinds:
                raise ImportFixError(
                    "unsupported_selector",
                    f"dot-prefixed source selector is not a regular file: {selector!r}",
                )
            if kinds == {"missing"}:
                raise ImportFixError(
                    "selected_path_missing",
                    f"dot-prefixed source selector matches no file: {selector!r}",
                )

        after_tree = g.read_tree(repo, buggy, source_paths)  # B selected source
        before_tree = g.read_tree(repo, fixed, source_paths)  # F selected source
        present = set(after_tree) | set(before_tree)
        _validate_source_selectors(raw_selectors, present)

        # Tests come from the fixed commit; tests_root must be a nonempty directory.
        tests_tree = g.read_tree(repo, fixed, [tests_root]) if tests_root else {}
        if tests_root is not None:
            if tests_root in tests_tree and len(tests_tree) == 1:
                raise ImportFixError("tests_root_not_directory", "tests_root is a single file")
            if not tests_tree:
                raise ImportFixError("tests_root_empty", "tests_root contains no files")
        tests_materialize: dict[str, tuple[str, str]] = {}
        if tests_root is not None:
            for path, mode_oid in tests_tree.items():
                rel = path[len(tests_root) + 1 :] if path.startswith(tests_root + "/") else path
                tests_materialize[rel] = mode_oid

        # Load every selected blob under one incremental budget (dedup by object id).
        after_src, before_src, test_files = _load_selected(
            repo, [after_tree, before_tree, tests_materialize]
        )

        # Source changes from exact tree comparison; binary/text from exact bytes.
        repair_set = _source_changes(after_src, before_src)
        _require_text(after_src, repair_set)
        _require_text(before_src, repair_set)

        # Whole-tree change classification (tree comparison, attribute-independent).
        for path in sorted(g.changed_tree_paths(repo, buggy, fixed)):
            in_source = any(_under(path, sp) for sp in source_paths)
            in_tests = tests_root is not None and _under(path, tests_root)
            if in_source and in_tests:
                raise ImportFixError("source_test_overlap", f"changed path is in both: {path}")
            if not in_source and not in_tests:
                raise ImportFixError("changed_path_unclassified", f"unclassified change: {path}")
        test_changed = sorted(
            p
            for p in g.changed_tree_paths(repo, buggy, fixed)
            if tests_root and _under(p, tests_root)
        )

        # Patches generated in a fresh isolated repo (never inside the source repo).
        reference_patch, pr_diff = diff_repo.generate_patches(
            repo.object_format, after_src, before_src
        )
        reference_sha = hashlib.sha256(reference_patch.encode("utf-8")).hexdigest()
        pr_diff_sha = hashlib.sha256(pr_diff.encode("utf-8")).hexdigest()

        # Ground-truth admission against the buggy tree and the synthetic review.
        bug_files = {f.path for bug in spec.ground_truth.bugs for f in bug.files}
        for bug in spec.ground_truth.bugs:
            for gt_file in bug.files:
                if gt_file.path not in after_src:
                    raise ImportFixError(
                        "ground_truth_file_missing",
                        f"ground-truth file not in buggy source: {gt_file.path}",
                    )
                if gt_file.path not in repair_set:
                    raise ImportFixError(
                        "ground_truth_file_not_changed",
                        f"ground-truth file is not changed by the fix: {gt_file.path}",
                    )
        uncovered = sorted(repair_set - bug_files)
        if uncovered:
            raise ImportFixError(
                "semantic_change_uncovered",
                "repair paths not covered by any declared bug: " + ", ".join(uncovered[:20]),
            )

        provenance = Provenance(
            provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
            mode="reverse_fix",
            source_label=source_label,
            object_format=repo.object_format,  # type: ignore[arg-type]
            diff_policy_version=DIFF_POLICY_VERSION,
            buggy_commit=buggy,
            fixed_commit=fixed,
            merge_base=base,
            source_paths=source_paths,
            tests_root=tests_root,
            buggy_source_files=sorted(after_src),
            fixed_source_files=sorted(before_src),
            fixed_test_files=sorted(tests_materialize),
            changed_source_paths=sorted(repair_set),
            changed_test_paths=test_changed,
            pr_diff_sha256=pr_diff_sha,
            reference_patch_sha256=reference_sha,
            fixed_commit_author_date=fixed_author_date,
            fixed_commit_committer_date=fixed_committer_date,
            buggy_commit_committer_date=buggy_committer_date,
        )

        # When the fix became public, as a calendar date. Both signals are
        # "YYYY-MM-DDT...", so the string min is the chronological min and the
        # first ten characters are the date.
        git_date = min(fixed_author_date, fixed_committer_date)[:10]
        # Name the signal that actually won. A commit that was authored days
        # before it landed (rebase, a long review) has an earlier author date,
        # and recording that as "git_committer_date" would misattribute the one
        # fact this field exists to make checkable.
        git_basis = (
            "git_author_date" if fixed_author_date <= fixed_committer_date else "git_committer_date"
        )
        declared = spec.origin.public_fix_date if spec.origin is not None else None
        if declared is None:
            effective_date, date_basis = git_date, git_basis
        elif declared < git_date:
            # A human knows something Git cannot: the issue or discussion that
            # disclosed the defect predates the commit that fixed it.
            effective_date, date_basis = declared, "declared"
        elif declared > git_date:
            # A declared date LATER than the commit's own is the flattering
            # direction ("this case is newer, therefore clean"). Disagreement
            # always resolves toward more assumed exposure, never less.
            effective_date, date_basis = git_date, "min_of_signals"
        else:
            effective_date, date_basis = git_date, git_basis

        _validate_output_parent(output.parent)
        try:
            staging = Path(tempfile.mkdtemp(prefix=".arena-import-", dir=str(output.parent)))
        except OSError as exc:
            raise ImportFixError(
                "staging_failed", "could not create the staging directory"
            ) from exc
        try:
            case_dir = staging / spec.case.id
            after_dir = case_dir / "after"
            before_dir = case_dir / "before"
            for path, (mode, data) in after_src.items():
                _write_exact(after_dir / path, data, 0o755 if mode == "100755" else 0o644)
            for path, (mode, data) in before_src.items():
                _write_exact(before_dir / path, data, 0o755 if mode == "100755" else 0o644)
            for rel, (mode, data) in test_files.items():
                _write_exact(case_dir / "tests" / rel, data, 0o755 if mode == "100755" else 0o644)

            # Ground-truth line ranges against the materialized buggy files.
            for bug in spec.ground_truth.bugs:
                for gt_file in bug.files:
                    line_count = len(
                        (after_dir / gt_file.path).read_text(encoding="utf-8").splitlines()
                    )
                    for rng in gt_file.line_ranges:
                        if rng.end > line_count:
                            raise ImportFixError(
                                "invalid_ground_truth_range",
                                f"line range {rng.start}-{rng.end} exceeds {gt_file.path} "
                                f"({line_count} lines)",
                            )

            _write_exact(case_dir / "reference.patch", reference_patch.encode("utf-8"), 0o644)
            _write_exact(case_dir / "pr.diff", pr_diff.encode("utf-8"), 0o644)
            _write_exact(
                staging / "manifest.yaml",
                yaml.safe_dump(
                    {"version": spec.pack.version, "name": spec.pack.name, "cases": [spec.case.id]},
                    sort_keys=True,
                    default_flow_style=False,
                ).encode("utf-8"),
                0o644,
            )
            _write_exact(
                case_dir / "case.yaml",
                _build_case_yaml(
                    spec,
                    bool(tests_materialize),
                    public_fix_date=effective_date,
                    public_fix_date_basis=date_basis,
                    source_label=source_label,
                ).encode("utf-8"),
                0o644,
            )
            _write_exact(
                case_dir / "provenance.json",
                (
                    json.dumps(provenance.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
                ).encode("utf-8"),
                0o644,
            )

            # Prove both diffs reproduce their target trees (bytes AND modes) via Phase 1D.
            _verify_reproduces(
                after_dir,
                after_src,
                reference_patch,
                before_src,
                "reference_patch_verification_failed",
            )
            _verify_reproduces(
                before_dir, before_src, pr_diff, after_src, "pr_diff_verification_failed"
            )

            checksum = write_checksum(staging)
            errors = validate_dataset(staging)
            if errors:
                raise ImportFixError("generated_pack_invalid", "; ".join(errors)[:512])
            if scan_benchmark(staging):
                raise ImportFixError("contamination_detected", "contamination warnings present")
            _publish(staging, output)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    return ImportResult(
        output_path=output,
        case_id=spec.case.id,
        buggy_commit=buggy,
        fixed_commit=fixed,
        merge_base=base,
        object_format=repo.object_format,
        buggy_source_file_count=len(after_src),
        fixed_source_file_count=len(before_src),
        union_source_file_count=len(present),
        fixed_test_file_count=len(tests_materialize),
        repair_changed_paths=sorted(repair_set),
        test_changed_paths=test_changed,
        pack_checksum=checksum,
        public_fix_date=effective_date,
        public_fix_date_basis=date_basis,
        validation_ok=True,
        contamination_ok=True,
        certification="not run",
    )
