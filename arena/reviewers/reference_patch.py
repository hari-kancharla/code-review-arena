"""Load static reference.patch artifacts shipped with each benchmark case."""

from __future__ import annotations

import json
import time
from pathlib import Path

from arena.benchmark.artifacts import load_reference_patch
from arena.core.models import CaseContext, Finding, ReviewerResponse, ReviewResult
from arena.patching.patch_parser import touched_files
from arena.reviewers.base import BaseReviewer

REFERENCE_PATCH_FILENAME = "reference.patch"

# Reviewer-visible localization hints per case (not used to synthesize patch bytes).
LOCALIZATION_HINTS: dict[str, tuple[str, str, str, tuple[int, int], str]] = {
    "security_fastapi_multitenant_admin_bypass_001": (
        "Tenant admin route lacks tenant-scoped administrator authorization.",
        "authorization multi-tenant tenant admin tenant_id privilege escalation",
        "app/routes/tenant_admin.py",
        (9, 13),
        "Enforce tenant-scoped admin authorization on the reset route.",
    ),
    "distributed_kafka_duplicate_event_001": (
        "Kafka payment handler does not guard duplicate event delivery.",
        "idempotency duplicate event event_id at-least-once delivery kafka",
        "consumer/payments.py",
        (5, 6),
        "Deduplicate events by event_id before mutating the ledger.",
    ),
    "rag_fabricated_citation_001": (
        "Generated citations are not validated against retrieved context.",
        "citation validation retrieved context fabricated citation valid_ids",
        "rag/answer.py",
        (1, 2),
        "Reject citation IDs that are not present in retrieved chunks.",
    ),
    "async_balance_race_001": (
        "Concurrent balance updates are not atomic.",
        "race condition concurrent balance lock atomic lost write",
        "app/balance.py",
        (8, 11),
        "Guard balance mutations with an asyncio lock.",
    ),
    "idempotency_key_tenant_scope_001": (
        "Idempotency cache keys are not scoped per tenant.",
        "idempotency tenant tenant_id scope cache collision multi-tenant",
        "app/idempotency.py",
        (5, 9),
        "Scope idempotency lookup and storage by tenant plus key.",
    ),
    "security_sql_join_ownership_leak_001": (
        "Document lookup omits organization ownership enforcement.",
        "authorization tenant organization_id document ownership sql",
        "app/documents.py",
        (1, 16),
        "Require organization ownership in the document lookup path.",
    ),
    "security_jwt_audience_validation_001": (
        "JWT verification omits audience and issuer checks.",
        "jwt audience issuer aud iss token validation authentication",
        "app/auth/jwt_verifier.py",
        (4, 5),
        "Validate audience and issuer claims during token verification.",
    ),
    "distributed_out_of_order_event_001": (
        "Projector applies stale events without a version guard.",
        "event ordering version stale out-of-order projection",
        "app/projector.py",
        (10, 14),
        "Ignore events when their version is not newer than current state.",
    ),
    "api_pagination_cursor_skip_001": (
        "Pagination cursor lacks a stable id tiebreaker.",
        "pagination cursor created_at id tiebreaker duplicate timestamp",
        "app/pagination.py",
        (8, 21),
        "Use a composite cursor of created_at and record id.",
    ),
    "rag_prompt_injection_policy_override_001": (
        "Retrieved context is merged into trusted system instructions.",
        "prompt injection rag untrusted retrieved context policy override",
        "app/rag/prompt_builder.py",
        (4, 8),
        "Isolate retrieved context as untrusted data separate from system instructions.",
    ),
    # benchmark_sets/v1
    "fastapi_auth_bypass_001": (
        "Admin delete endpoint is missing an authorization check.",
        "authorization role-based access control privilege escalation admin",
        "app/routes/admin.py",
        (10, 14),
        "Require an admin dependency and enforce the admin role.",
    ),
    "graphql_n_plus_one_001": (
        "Customer lookups inside the order map create N+1 queries.",
        "N+1 batching dataloader query loop",
        "src/resolvers/orders.ts",
        (4, 10),
        "Batch customer lookups with a DataLoader.",
    ),
    "react_stale_state_001": (
        "Async updates close over stale state and can drop messages.",
        "stale state functional update asynchronous setState",
        "src/components/Notifications.tsx",
        (6, 9),
        "Use a functional state update.",
    ),
    "kafka_idempotency_bug_001": (
        "Duplicate Kafka events are not deduplicated before crediting.",
        "idempotency duplicate event event_id at-least-once delivery",
        "consumer/payments.py",
        (4, 6),
        "Track processed event ids before crediting.",
    ),
    "redis_cache_key_collision_001": (
        "Cache keys are under-specified and leak data across tenants.",
        "cache key collision tenant isolation data leakage",
        "app/search_cache.py",
        (1, 2),
        "Include tenant and query in the cache key.",
    ),
    "sql_permission_leak_001": (
        "Document query omits organization ownership filtering.",
        "authorization tenant organization_id document ownership sql",
        "sql/documents.sql",
        (3, 3),
        "Filter the query by organization ownership.",
    ),
    "rag_fake_citation_001": (
        "Generated citations are not validated against retrieved context.",
        "citation validation retrieved context fabricated valid_ids",
        "rag/answer.py",
        (1, 2),
        "Reject citation ids absent from retrieved chunks.",
    ),
    "async_race_condition_001": (
        "Concurrent balance updates are not atomic.",
        "race condition concurrent balance lock atomic lost write",
        "app/balance.py",
        (8, 11),
        "Guard balance mutations with an asyncio lock.",
    ),
    "api_contract_regression_001": (
        "Renamed response fields break existing snake_case clients.",
        "backward compatibility response schema api contract snake_case",
        "app/routes/profile.py",
        (2, 6),
        "Retain the original field names or add aliases.",
    ),
    # benchmark_sets/audit_v2
    "money_discount_rounding_001": (
        "Percentage reduction is applied per unit and floored before multiplying.",
        "rounding discount per unit reduction currency precision aggregate gross",
        "app/pricing.py",
        (3, 4),
        "Reduce the full amount once instead of flooring each unit, so no money is lost.",
    ),
    "ratelimit_window_boundary_001": (
        "Fixed-window admission uses a non-strict comparison and admits one over the cap.",
        "rate limit boundary off-by-one strict comparison fewer than window cap",
        "app/limiter.py",
        (4, 4),
        "Admit only when the active count is strictly fewer than the cap.",
    ),
    "permission_precedence_001": (
        "Publish check drops grouping so operator precedence bypasses the frozen guard.",
        "operator precedence grouping parentheses boolean logic authorization bypass",
        "app/permissions.py",
        (3, 3),
        "Group the role test in parentheses so the frozen guard always applies.",
    ),
    "overdraft_min_balance_001": (
        "Lowest-balance tracker keeps the peak instead of the trough.",
        "running minimum smallest comparison direction overdraft less than",
        "app/ledger.py",
        (7, 7),
        "Update the result when the balance falls, not when it rises.",
    ),
    "progress_zero_division_001": (
        "Completion percentage divides by zero when the workload is empty.",
        "division by zero empty input missing guard denominator",
        "app/progress.py",
        (3, 3),
        "Return a defined percentage for an empty workload before dividing.",
    ),
    "page_count_ceil_001": (
        "Page count uses floor division and drops the final partial page.",
        "ceiling division integer division round up remainder off by one",
        "app/paging.py",
        (3, 3),
        "Round the division up so a partial final page is counted.",
    ),
    "truthiness_default_001": (
        "Timeout fallback uses truthiness and discards an explicit zero.",
        "truthiness falsy value none handling explicit zero identity check",
        "app/config.py",
        (3, 3),
        "Fall back only when the value is None, not when it is falsy.",
    ),
    "preview_truncation_001": (
        "Preview slice stops one character short of the limit.",
        "string truncation off by one slice length full limit",
        "app/text.py",
        (4, 4),
        "Slice up to the full limit so the preview is not a character short.",
    ),
    "retry_backoff_cap_001": (
        "Backoff delay drops its clamp and grows without bound.",
        "maximum delay backoff clamp unbounded growth cap upper bound",
        "app/backoff.py",
        (3, 3),
        "Clamp the grown delay to the provided ceiling.",
    ),
    "eligibility_and_or_001": (
        "Eligibility check uses or where both conditions are required.",
        "boolean conjunction access control logical connective require all",
        "app/eligibility.py",
        (3, 3),
        "Join the age and consent conditions with a conjunction.",
    ),
}


class ReferencePatchReviewer(BaseReviewer):
    # Reads the case's reference.patch by design: a fixture/pipeline control,
    # never a reviewer measurement, so it is marked as an oracle reader.
    oracle_reachable = True
    name = "reference-patch"
    model = None

    def _reference_patch_path(self, context: CaseContext) -> Path | None:
        if context.case_dir is None:
            return None
        return context.case_dir / REFERENCE_PATCH_FILENAME

    def review(self, context: CaseContext) -> ReviewerResponse:
        started = time.perf_counter()
        patch_path = self._reference_patch_path(context)
        patch_text = ""
        if patch_path is not None and patch_path.is_file():
            patch_text = load_reference_patch(patch_path)
        hints = LOCALIZATION_HINTS.get(context.case.id)
        if hints is None:
            title = f"Reference patch review for {context.case.id}"
            summary = (
                f"No localization hints configured for {context.case.id}. "
                f"Apply the shipped reference patch when available."
            )
            path = touched_files(patch_text)[0] if patch_text.strip() else "unknown"
            line_range = (1, 1)
            fix = "Apply the reference patch for this case."
        else:
            title, terms, path, line_range, fix = hints
            if patch_text.strip():
                touched = touched_files(patch_text)
                if touched:
                    path = touched[0]
            summary = (
                f"{title} This concerns {terms}. "
                "The reference.patch artifact contains the canonical known-good repair."
            )
        finding = Finding(
            title=title,
            summary=summary,
            category=context.case.category,
            severity=context.case.severity,
            file=path,
            line_start=line_range[0],
            line_end=line_range[1],
            evidence="Reference patch baseline loaded from the case directory.",
            suggested_fix=fix,
            suggested_patch=patch_text if patch_text.strip() else None,
            patch_confidence=0.99 if patch_text.strip() else None,
            confidence=0.99,
        )
        result = ReviewResult(
            findings=[finding],
            overall_risk=context.case.severity,
            review_summary=(
                f"Reference-patch baseline for {context.case.id} "
                f"({'patch present' if patch_text.strip() else 'patch missing'})."
            ),
            proposed_patch=patch_text if patch_text.strip() else None,
        )
        raw = json.dumps(result.model_dump())
        return ReviewerResponse(
            raw_response=raw,
            parsed_response=result,
            invalid_output=False,
            parse_attempts=1,
            parse_status="exact",
            input_finding_count=len(result.findings),
            retained_finding_count=len(result.findings),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
