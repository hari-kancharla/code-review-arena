"""Build the reviewer payload for one candidate variant, and prove what is in it.

Two things matter here and nothing else does.

**The reviewer must not be told which variant it received.** The payload carries
an opaque ``submission_id`` derived from a digest, never the grader-side variant
id, never the words genuine/compromised, never the mechanism, never the intent,
and never the trusted verdict.

**The reviewer must not be able to read the oracle.** The payload is assembled
exclusively from the variant workspace, the reviewer-visible diff and the task
contract. The oracle directory is not consulted by any code path in this module.
``assert_no_oracle_leak`` re-checks that claim against the assembled payload by
searching for oracle filenames and oracle content, so the guarantee is tested
rather than asserted.

Information conditions (ablations) are applied here so that later experiments can
vary what a reviewer sees without touching execution, scoring, or the pairs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from arena.core.bounded_io import read_text_capped
from arena.core.errors import ValidationError
from arena.core.limits import PACK_FILE_BYTES
from arena.integrity.loader import (
    base_dir,
    is_validation_path,
    oracle_dir,
    relative_files,
    variant_diff,
    workspace_dir,
)
from arena.integrity.models import (
    CandidateVariant,
    ExecutionEvidence,
    IntegrityPair,
    IntegrityReviewContext,
    ReviewerContextCondition,
    ValidationChangeAnalysis,
    VisibleCISummary,
)

#: Grader-side words that must never appear in a reviewer payload, because they
#: would answer the question the benchmark is asking.
FORBIDDEN_PAYLOAD_TOKENS: tuple[str, ...] = (
    "compromised",
    "trusted_oracle",
    "trusted oracle",
    "hidden test",
    "hidden oracle",
    "integrity_failure_class:",
)


@dataclass(frozen=True)
class ContextBounds:
    """Bounds on reviewer-visible file content, mirroring the ordinary track."""

    max_files: int = 60
    max_total_bytes: int = 262_144
    max_file_bytes: int = 65_536


def submission_id(pack: str, pair: IntegrityPair, variant: CandidateVariant) -> str:
    """A stable, opaque id that encodes no grader-side truth.

    Deterministic so a run is reproducible, and a digest so neither the variant
    kind nor the on-disk directory name can be recovered from it by inspection.
    """
    payload = f"{pack}\x00{pair.id}\x00{variant.id}".encode()
    return "sub-" + hashlib.sha256(payload).hexdigest()[:16]


def _condition_flags(condition: ReviewerContextCondition) -> tuple[bool, bool, bool, bool]:
    """(task, validation_files, product_files, visible_ci) for one condition."""
    return {
        "DIFF_ONLY": (False, False, False, False),
        "DIFF_PLUS_TASK": (True, False, False, False),
        "DIFF_PLUS_TASK_PLUS_TESTS": (True, True, False, False),
        "FULL_REPOSITORY": (True, True, True, False),
        "FULL_REPOSITORY_PLUS_VISIBLE_CI": (True, True, True, True),
    }[condition]


def _read_files(
    root: Path, relatives: list[str], bounds: ContextBounds
) -> tuple[dict[str, str], bool, list[str]]:
    contents: dict[str, str] = {}
    omitted: list[str] = []
    truncated = False
    total = 0
    for relative in relatives:
        if len(contents) >= bounds.max_files or total >= bounds.max_total_bytes:
            omitted.append(relative)
            continue
        text, capped = read_text_capped(
            root / relative, min(bounds.max_file_bytes, PACK_FILE_BYTES), label="workspace file"
        )
        if capped:
            text += "\n…[truncated by arena: file exceeds max_file_bytes]"
            truncated = True
        size = len(text.encode("utf-8"))
        if total + size > bounds.max_total_bytes:
            omitted.append(relative)
            total = bounds.max_total_bytes
            continue
        contents[relative] = text
        total += size
    return contents, truncated or bool(omitted), omitted


def visible_ci_summary(evidence: ExecutionEvidence) -> VisibleCISummary:
    """The shallow green badge a pull-request page would show.

    Carries the command, the verdict, the executed/skipped counts and the
    duration -- and deliberately nothing about the trusted surface.
    """
    return VisibleCISummary(
        command=" && ".join(" ".join(argv) for argv in evidence.command),
        passed=evidence.passed,
        exit_code=evidence.exit_code,
        executed_test_count=evidence.executed_test_count,
        skipped_test_count=evidence.skipped_test_count,
        duration_ms=evidence.duration_ms,
    )


def build_review_context(
    pack: str,
    pair: IntegrityPair,
    variant: CandidateVariant,
    *,
    condition: ReviewerContextCondition | None = None,
    visible: ExecutionEvidence | None = None,
    validation_change: ValidationChangeAnalysis | None = None,
    bounds: ContextBounds | None = None,
) -> IntegrityReviewContext:
    """Assemble exactly what the reviewer receives for one variant."""
    resolved = condition or pair.context_condition
    bounds = bounds or ContextBounds()
    show_task, show_validation, show_product, show_ci = _condition_flags(resolved)

    root = workspace_dir(pair, variant)
    every = relative_files(root)
    validation_paths = list(pair.candidate_validation.paths)
    validation_relatives = [p for p in every if is_validation_path(p, validation_paths)]
    product_relatives = [p for p in every if not is_validation_path(p, validation_paths)]

    validation_files: dict[str, str] = {}
    product_files: dict[str, str] = {}
    truncated = False
    omitted: list[str] = []
    if show_validation:
        validation_files, part_trunc, part_omitted = _read_files(root, validation_relatives, bounds)
        truncated = truncated or part_trunc
        omitted += part_omitted
    if show_product:
        product_files, part_trunc, part_omitted = _read_files(root, product_relatives, bounds)
        truncated = truncated or part_trunc
        omitted += part_omitted

    context = IntegrityReviewContext(
        submission_id=submission_id(pack, pair, variant),
        pair_title=pair.title,
        stack=list(pair.stack),
        context_condition=resolved,
        task_contract=pair.task_contract if show_task else None,
        pr_description=variant.pr_description if show_task else "",
        diff=variant_diff(pair, variant),
        product_files=product_files,
        validation_files=validation_files,
        validation_change=validation_change if show_validation else None,
        visible_ci=visible_ci_summary(visible) if (show_ci and visible is not None) else None,
        context_truncated=truncated,
        omitted_files=sorted(set(omitted)),
    )
    assert_no_oracle_leak(context, pair, variant)
    return context


def payload_text(context: IntegrityReviewContext) -> str:
    """Every reviewer-visible string, concatenated, for leakage inspection."""
    parts: list[str] = [
        context.submission_id,
        context.pair_title,
        " ".join(context.stack),
        context.pr_description,
        context.diff,
    ]
    if context.task_contract is not None:
        parts += [
            context.task_contract.title,
            context.task_contract.statement,
            " ".join(context.task_contract.invariants),
            " ".join(context.task_contract.out_of_scope),
            context.task_contract.source,
        ]
    for mapping in (context.product_files, context.validation_files):
        for name, body in mapping.items():
            parts += [name, body]
    if context.validation_change is not None:
        change = context.validation_change
        parts += [
            " ".join(change.changed_validation_files),
            " ".join(change.added_validation_files),
            " ".join(change.removed_validation_files),
            " ".join(change.tests_added),
            " ".join(change.tests_removed),
            " ".join(change.discovery_or_config_changed),
            " ".join(change.fixture_or_golden_changed),
            " ".join(change.mock_or_stub_changed),
            " ".join(change.changed_product_files),
        ]
    if context.visible_ci is not None:
        parts.append(context.visible_ci.command)
    parts += context.omitted_files
    return "\n".join(parts)


#: Shortest token worth searching for. A short string such as "oracle" or
#: "import pytest" occurs in ordinary code, so matching on it would produce
#: meaningless failures rather than evidence of a leak.
MIN_LEAK_TOKEN_LENGTH = 8


def _baseline_lines(pair: IntegrityPair) -> set[str]:
    """Every stripped line already present in the pre-PR tree.

    A line that existed before the pull request cannot be evidence that the oracle
    leaked into the payload, however long it is.
    """
    root = base_dir(pair)
    lines: set[str] = set()
    for relative in relative_files(root):
        try:
            text, _ = read_text_capped(root / relative, PACK_FILE_BYTES, label="baseline file")
        except ValidationError:
            continue
        lines.update(line.strip() for line in text.splitlines())
    return lines


def _is_import(line: str) -> bool:
    return line.startswith("import ") or (line.startswith("from ") and " import " in line)


def oracle_tokens(pair: IntegrityPair) -> list[str]:
    """Distinctive filenames and content lines from the trusted oracle.

    Used to prove, by search, that no oracle-derived string reached a reviewer
    payload. Three kinds of line are excluded because a match on them would be
    noise rather than evidence: anything too short to be distinctive, an import
    statement (which names the product surface the reviewer is already shown), and
    a line that already exists in the baseline tree.
    """
    root = oracle_dir(pair)
    baseline = _baseline_lines(pair)
    tokens: list[str] = [pair.trusted_oracle.mount]
    for relative in relative_files(root):
        tokens.append(relative)
        tokens.append(relative.rsplit("/", 1)[-1])
        try:
            text, _ = read_text_capped(root / relative, PACK_FILE_BYTES, label="oracle file")
        except ValidationError:
            # A binary or unreadable oracle artifact has no text to leak.
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) >= 40 and not _is_import(stripped) and stripped not in baseline:
                tokens.append(stripped)
    return sorted({token for token in tokens if len(token) >= MIN_LEAK_TOKEN_LENGTH})


def assert_no_oracle_leak(
    context: IntegrityReviewContext, pair: IntegrityPair, variant: CandidateVariant
) -> None:
    """Fail closed if any grader-side or oracle-derived string reached the payload."""
    haystack = payload_text(context)
    lowered = haystack.casefold()
    for token in FORBIDDEN_PAYLOAD_TOKENS:
        if token in lowered:
            raise ValidationError(
                f"reviewer payload for {context.submission_id} leaks grader-side token {token!r}"
            )
    if variant.id.casefold() in lowered:
        raise ValidationError(
            f"reviewer payload for {context.submission_id} leaks the variant id {variant.id!r}"
        )
    if variant.mechanism_explanation and variant.mechanism_explanation in haystack:
        raise ValidationError("reviewer payload leaks the grader-side mechanism explanation")
    for mechanism in variant.mechanisms:
        if mechanism.casefold() in lowered:
            raise ValidationError(f"reviewer payload leaks the mechanism label {mechanism!r}")
    for keyword in variant.mechanism_keywords:
        if keyword.casefold() in lowered:
            raise ValidationError(
                f"reviewer payload leaks the grading keyword {keyword!r}; the reviewer would be "
                "echoing the answer rather than reasoning to it"
            )
    for token in oracle_tokens(pair):
        if token and token in haystack:
            raise ValidationError(
                f"reviewer payload for {context.submission_id} leaks trusted-oracle content "
                f"({token[:60]!r})"
            )
