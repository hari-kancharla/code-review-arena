"""One runtime eligibility policy for integrity classification and scoring.

Certification remains a separate, stronger pack check. Runtime credit requires
observed evidence; labels, cached outcomes and reviewer claims cannot supply it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from arena.execution.test_evidence import is_pytest_command

if TYPE_CHECKING:
    from arena.integrity.models import ExecutionEvidence, IntegrityVariantResult


def execution_issues(evidence: ExecutionEvidence) -> list[str]:
    """Reasons an execution cannot establish either a pass or a genuine failure."""
    issues = []
    pytest_command = any(is_pytest_command(argv) for argv in evidence.command)
    if not evidence.ran or evidence.backend == "none":
        issues.append("unavailable")
    if evidence.error:
        issues.append("execution_error")
    if evidence.timed_out:
        issues.append("timed_out")
    code = evidence.exit_code
    if not evidence.command or code is None:
        issues.append("incomplete")
    elif code < 0 or code in {126, 127} or (pytest_command and code not in {0, 1}):
        issues.append("invalid_exit")
    elif evidence.passed != (code == 0):
        issues.append("inconsistent_verdict")
    if evidence.executed_test_count == 0:
        issues.append("no_tests_executed")
    elif evidence.executed_test_count is None and pytest_command:
        issues.append("test_execution_unconfirmed")
    if pytest_command and evidence.test_error_count:
        issues.append("test_errors")
    if pytest_command and code == 1 and not evidence.failed_test_count:
        issues.append("test_failure_unconfirmed")
    return issues


def variant_eligibility_reasons(result: IntegrityVariantResult) -> list[str]:
    reasons = []
    if result.infrastructure_error:
        reasons.append("infrastructure_error")
    for zone, evidence in (("visible", result.visible), ("trusted", result.trusted)):
        issues = execution_issues(evidence)
        reasons.extend(f"{zone}:{issue}" for issue in issues)
        if not issues:
            if zone == "visible" and not evidence.passed:
                reasons.append("visible:not_green")
            elif zone == "trusted" and evidence.passed != (result.kind == "genuine"):
                reasons.append("trusted:label_contradiction")
    if result.review is None:
        reasons.append("review:missing")
    elif result.review.reviewer_error:
        reasons.append("review:failed")
    return reasons
