"""Deterministic control reviewers used for calibration and regression tests."""

from __future__ import annotations

import difflib
import json
import time

from arena.core.models import CaseContext, Finding, ReviewerResponse, ReviewResult
from arena.reviewers.base import BaseReviewer
from arena.reviewers.response_parser import parse_reviewer_output, response_from_outcome


class ControlReviewer(BaseReviewer):
    name = "control"

    ANSWERS = {
        "fastapi_auth_bypass_001": (
            "Missing authorization check on admin endpoint.",
            "authorization role-based access control privilege escalation admin role authorization",
            "app/routes/admin.py",
            (10, 14),
            "Use require_admin and enforce RBAC.",
        ),
        "graphql_n_plus_one_001": (
            "Customer lookups inside the order map introduce N+1 database queries.",
            "N+1 batching database query customer loop query",
            "src/resolvers/orders.ts",
            (4, 10),
            "Restore DataLoader loadMany batching.",
        ),
        "react_stale_state_001": (
            "Async updates close over stale messages state and can drop notifications.",
            "stale state functional update asynchronous update messages stale state",
            "src/components/Notifications.tsx",
            (6, 9),
            "Use functional setMessages(previous => [...previous, message]).",
        ),
        "kafka_idempotency_bug_001": (
            "Payment updates are no longer idempotent for duplicate Kafka events.",
            "idempotency duplicate event at-least-once delivery event_id duplicate credit",
            "consumer/payments.py",
            (4, 6),
            "Persist processed_event_ids keyed by event_id for idempotency.",
        ),
        "redis_cache_key_collision_001": (
            "Under-specified Redis keys leak cached search data across tenants and queries.",
            "cache key collision tenant isolation data leakage tenant_id query cache",
            "app/search_cache.py",
            (1, 2),
            "Include tenant_id and query in cache_key.",
        ),
        "sql_permission_leak_001": (
            "Document access omits organization ownership filtering.",
            "authorization tenant isolation data exposure organization_id document ownership",
            "sql/documents.sql",
            (3, 3),
            "Require organization_id ownership tenant filtering.",
        ),
        "rag_fake_citation_001": (
            "Generated citation IDs are not validated against retrieved context.",
            "citation validation retrieved context fabricated citation "
            "citation_ids retrieved_chunks validate",
            "rag/answer.py",
            (1, 2),
            "Validate citation_ids against valid_ids from retrieved_chunks.",
        ),
        "async_race_condition_001": (
            "Concurrent balance mutations are non-atomic and can lose writes.",
            "race condition atomic update lost write concurrent balance lock",
            "app/balance.py",
            (8, 11),
            "Guard updates with asyncio.Lock for an atomic operation.",
        ),
        "api_contract_regression_001": (
            "Renamed response fields break existing clients expecting snake_case keys.",
            "backward compatibility response schema API contract snake_case camelCase client",
            "app/routes/profile.py",
            (2, 6),
            "Retain user_id or use an alias with a versioned response.",
        ),
        "security_fastapi_multitenant_admin_bypass_001": (
            "Tenant admin route does not verify tenant-scoped administrator privileges.",
            "authorization multi-tenant privilege escalation tenant admin role tenant_id",
            "app/routes/tenant_admin.py",
            (9, 13),
            "Use require_tenant_admin and enforce tenant-scoped admin RBAC.",
        ),
        "distributed_kafka_duplicate_event_001": (
            "Duplicate Kafka events are no longer deduplicated before ledger mutation.",
            "idempotency duplicate event at-least-once delivery event_id duplicate credit kafka",
            "consumer/payments.py",
            (5, 6),
            "Persist processed_event_ids keyed by event_id before crediting.",
        ),
        "rag_fabricated_citation_001": (
            "Generated citation IDs are not validated against retrieved context.",
            "citation validation retrieved context fabricated citation citation_ids valid_ids",
            "rag/answer.py",
            (1, 2),
            "Validate citation_ids against valid_ids from retrieved_chunks.",
        ),
        "async_balance_race_001": (
            "Concurrent balance mutations are non-atomic and can lose writes.",
            "race condition atomic update lost write concurrent balance lock asyncio",
            "app/balance.py",
            (8, 11),
            "Guard updates with asyncio.Lock for an atomic operation.",
        ),
        "idempotency_key_tenant_scope_001": (
            "Idempotency storage ignores tenant scope when looking up cached responses.",
            "idempotency tenant isolation cache collision multi-tenant tenant_id scope",
            "app/idempotency.py",
            (5, 9),
            "Scope idempotency lookup and storage by tenant_id plus key.",
        ),
        "security_sql_join_ownership_leak_001": (
            "Document lookup omits organization ownership filtering in the SQL join query.",
            "authorization tenant isolation data exposure organization_id document ownership sql",
            "app/documents.py",
            (1, 16),
            "Require organization_id ownership filtering in the document lookup.",
        ),
        "security_jwt_audience_validation_001": (
            "JWT verifier checks signature but not audience and issuer claims.",
            "jwt audience issuer authentication aud iss token validation",
            "app/auth/jwt_verifier.py",
            (4, 5),
            "Validate aud and iss against expected audience and issuer settings.",
        ),
        "distributed_out_of_order_event_001": (
            "Projector applies stale events without a monotonic version guard.",
            "event ordering version stale out-of-order state projection",
            "app/projector.py",
            (10, 14),
            "Ignore events when event.version is not newer than current state.",
        ),
        "api_pagination_cursor_skip_001": (
            "Pagination cursor omits a stable id tiebreaker for duplicate created_at values.",
            "pagination cursor created_at id tiebreaker duplicate timestamp api",
            "app/pagination.py",
            (8, 21),
            "Use a composite cursor of created_at plus record id.",
        ),
        "rag_prompt_injection_policy_override_001": (
            "Retrieved context is merged into trusted system instructions without isolation.",
            "prompt injection rag untrusted context policy override system instructions",
            "app/rag/prompt_builder.py",
            (4, 8),
            "Wrap retrieved documents as untrusted context separate from system instructions.",
        ),
    }

    KEYWORD_GAMER_NEARBY = {
        "security_fastapi_multitenant_admin_bypass_001": "app/auth.py",
        "distributed_kafka_duplicate_event_001": "consumer/ledger.py",
        "rag_fabricated_citation_001": "rag/retriever.py",
        "async_balance_race_001": "app/models.py",
        "idempotency_key_tenant_scope_001": "app/middleware.py",
        "security_sql_join_ownership_leak_001": "app/db.py",
        "security_jwt_audience_validation_001": "app/auth/settings.py",
        "distributed_out_of_order_event_001": "app/events.py",
        "api_pagination_cursor_skip_001": "app/models.py",
        "rag_prompt_injection_policy_override_001": "app/rag/models.py",
    }

    KEYWORD_GAMER_VOICE = {
        "security_fastapi_multitenant_admin_bypass_001": (
            "tenant-scoped admin authorization, require_tenant_admin, tenant_id RBAC, "
            "citation validation, structural validator fastapi_tenant_admin_authorization"
        ),
        "distributed_kafka_duplicate_event_001": (
            "event_id idempotency duplicate at-least-once delivery, processed_event_ids lock, "
            "kafka consumer guard before ledger credit"
        ),
        "rag_fabricated_citation_001": (
            "citation validation, retrieved_chunks, valid_ids grounding, audience of citations, "
            "reject fabricated citation_ids"
        ),
        "async_balance_race_001": (
            "asyncio.Lock atomic concurrent balance update, lost-write race, lock guard "
            "structural atomicity validator"
        ),
        "idempotency_key_tenant_scope_001": (
            "tenant_id scoped idempotency key, organization_id cache collision, multi-tenant "
            "lookup tuple guard"
        ),
        "security_sql_join_ownership_leak_001": (
            "organization_id tenant ownership filter in SQL join, document authorization leak"
        ),
        "security_jwt_audience_validation_001": (
            "jwt audience issuer aud iss expected_audience expected_issuer token validation"
        ),
        "distributed_out_of_order_event_001": (
            "event version monotonic stale out-of-order projection, sequence cursor guard"
        ),
        "api_pagination_cursor_skip_001": (
            "cursor pagination created_at id tiebreaker, duplicate timestamp stable ordering"
        ),
        "rag_prompt_injection_policy_override_001": (
            "untrusted retrieved context, prompt injection policy override, system instructions "
            "isolation wrap"
        ),
    }

    KEYWORD_GAMER_PATCH_MARKERS: dict[str, list[tuple[str, str]]] = {
        "security_fastapi_multitenant_admin_bypass_001": [
            (
                "current_user=Depends(get_current_user)",
                "    # tenant require_tenant_admin authorization guard for tenant_id RBAC\n",
            ),
        ],
        "distributed_kafka_duplicate_event_001": [
            (
                "self.ledger.credit",
                '        if event.get("event_id"):  # idempotency duplicate processed_event guard\n',
            ),
        ],
        "rag_fabricated_citation_001": [
            (
                "return generator(retrieved_chunks)",
                "    _valid_ids = {chunk['id'] for chunk in retrieved_chunks}  # citation validation\n",
            ),
        ],
        "async_balance_race_001": [
            (
                "self.balance = 0",
                "        self._lock = asyncio.Lock()  # atomic concurrent balance guard\n",
            ),
        ],
        "idempotency_key_tenant_scope_001": [
            (
                "self._records[key] = response",
                "        # tenant_id scoped idempotency lookup tuple guard\n",
            ),
        ],
        "security_sql_join_ownership_leak_001": [
            (
                "return repo.fetch(document_id)",
                "    # organization_id tenant ownership filter in SQL join\n",
            ),
        ],
        "security_jwt_audience_validation_001": [
            (
                'return bool(token.get("signature_valid"))',
                "    # aud iss audience issuer validation (structural check pending)\n",
            ),
        ],
        "distributed_out_of_order_event_001": [
            (
                "self.state.status = event.status",
                "        # version stale out-of-order event ordering guard\n",
            ),
        ],
        "api_pagination_cursor_skip_001": [
            (
                "next_cursor = None if len(page) < limit else last.created_at",
                "    # cursor tiebreaker: created_at plus record id for stable pagination\n",
            ),
        ],
        "rag_prompt_injection_policy_override_001": [
            (
                'return system_instructions + "\\n" + retrieved_text',
                "    # wrap untrusted retrieved context; policy override isolation pending\n",
            ),
        ],
    }

    FIXED_FILES = {
        "fastapi_auth_bypass_001": """from fastapi import APIRouter, Depends

router = APIRouter()


def require_admin():
    return {"id": 1, "role": "admin"}


@router.delete("/admin/users/{user_id}")
def delete_user(user_id: int, current_user=Depends(require_admin)):
    if current_user.get("role") != "admin":
        return None
    return {"deleted_user_id": user_id, "deleted_by": current_user["id"]}
""",
        "graphql_n_plus_one_001": """export const resolvers = {
  Query: {
    orders: async (_parent: unknown, _args: unknown, { db, loaders }: Context) => {
      const orders = await db.orders.list();
      const customers = await loaders.customer.loadMany(orders.map(order => order.customerId));
      return orders.map((order, index) => ({ ...order, customer: customers[index] }));
    },
  },
};
""",
        "react_stale_state_001": """import { useState } from "react";

export function Notifications() {
  const [messages, setMessages] = useState<string[]>([]);

  async function receive(message: string) {
    await Promise.resolve();
    setMessages(previous => [...previous, message]);
  }

  return <button onClick={() => receive("new")}>{messages.length}</button>;
}
""",
        "kafka_idempotency_bug_001": """class PaymentConsumer:
    def __init__(self, ledger):
        self.ledger = ledger
        self.processed_event_ids = set()

    def handle(self, event):
        if event["event_id"] in self.processed_event_ids:
            return
        self.ledger.credit(event["account_id"], event["amount"])
        self.processed_event_ids.add(event["event_id"])
""",
        "redis_cache_key_collision_001": """def cache_key(
    tenant_id: str, user_id: str, query: str
) -> str:
    return f"search:{tenant_id}:{user_id}:{query}"


def search(redis, tenant_id: str, user_id: str, query: str, load):
    key = cache_key(tenant_id, user_id, query)
    cached = redis.get(key)
    if cached is not None:
        return cached
    result = load(tenant_id, user_id, query)
    redis.set(key, result)
    return result
""",
        "sql_permission_leak_001": """SELECT id, title, body
FROM documents
WHERE id = :document_id
  AND organization_id = :organization_id;
""",
        "rag_fake_citation_001": """def build_answer(generator, retrieved_chunks):
    answer = generator(retrieved_chunks)
    valid_ids = {chunk["id"] for chunk in retrieved_chunks}
    if any(citation not in valid_ids for citation in answer["citation_ids"]):
        raise ValueError("Answer contained an unsupported citation")
    return answer
""",
        "async_race_condition_001": """import asyncio


class BalanceService:
    def __init__(self):
        self.balance = 0
        self.lock = asyncio.Lock()

    async def add(self, amount: int) -> None:
        async with self.lock:
            current = self.balance
            await asyncio.sleep(0)
            self.balance = current + amount
""",
        "api_contract_regression_001": """def serialize_profile(user):
    return {
        "user_id": user.id,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
    }
""",
        "security_fastapi_multitenant_admin_bypass_001": """from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user, require_tenant_admin

router = APIRouter()


@router.post("/tenants/{tenant_id}/admin/reset")
def reset_tenant(tenant_id: str, current_user=Depends(require_tenant_admin)):
    user = current_user or get_current_user()
    if user.get("tenant_id") != tenant_id or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Tenant admin required")
    return {"reset": tenant_id, "by": user["id"]}
""",
        "distributed_kafka_duplicate_event_001": """class PaymentConsumer:
    def __init__(self, ledger):
        self.ledger = ledger
        self.processed_event_ids = set()

    def handle(self, event):
        if event["event_id"] in self.processed_event_ids:
            return
        self.ledger.credit(event["account_id"], event["amount"])
        self.processed_event_ids.add(event["event_id"])
""",
        "rag_fabricated_citation_001": """def build_answer(generator, retrieved_chunks):
    answer = generator(retrieved_chunks)
    valid_ids = {chunk["id"] for chunk in retrieved_chunks}
    if any(citation not in valid_ids for citation in answer["citation_ids"]):
        raise ValueError("Answer contained an unsupported citation")
    return answer
""",
        "async_balance_race_001": """import asyncio


class BalanceService:
    def __init__(self):
        self.balance = 0
        self.lock = asyncio.Lock()

    async def add(self, amount: int) -> None:
        async with self.lock:
            current = self.balance
            await asyncio.sleep(0)
            self.balance = current + amount
""",
        "idempotency_key_tenant_scope_001": """class IdempotencyStore:
    def __init__(self):
        self._records: dict[tuple[str, str], dict] = {}

    def lookup(self, tenant_id: str, key: str):
        return self._records.get((tenant_id, key))

    def store(self, tenant_id: str, key: str, response: dict):
        self._records[(tenant_id, key)] = response
""",
        "security_sql_join_ownership_leak_001": """DOCUMENT_LOOKUP_SQL = \"\"\"
SELECT d.id, d.title
FROM documents d
JOIN projects p ON p.id = d.project_id
WHERE d.id = :document_id
  AND d.organization_id = :organization_id
\"\"\"

from app.db import DocumentRepository
from app.models import Document, User


def get_document_for_user(
    repo: DocumentRepository, document_id: str, user: User
) -> Document | None:
    document = repo.fetch(document_id)
    if document is None or document.organization_id != user.organization_id:
        return None
    return document
""",
        "security_jwt_audience_validation_001": """from app.auth.settings import EXPECTED_AUDIENCE, EXPECTED_ISSUER


def verify_token(token: dict) -> bool:
    if not token.get("signature_valid"):
        return False
    if token.get("aud") != EXPECTED_AUDIENCE:
        return False
    if token.get("iss") != EXPECTED_ISSUER:
        return False
    return True
""",
        "distributed_out_of_order_event_001": """from dataclasses import dataclass

from app.events import OrderStatusChanged


@dataclass
class OrderState:
    status: str
    version: int


class OrderProjector:
    def __init__(self) -> None:
        self.state = OrderState(status="CREATED", version=0)

    def apply(self, event: OrderStatusChanged) -> OrderState:
        if event.version <= self.state.version:
            return self.state
        self.state.status = event.status
        self.state.version = event.version
        return self.state
""",
        "api_pagination_cursor_skip_001": """from app.models import Record


def fetch_page(
    records: list[Record], *, cursor: tuple[int, str] | None, limit: int
) -> tuple[list[Record], tuple[int, str] | None]:
    ordered = sorted(records, key=lambda item: (item.created_at, item.id))
    start = 0
    if cursor is not None:
        created_at, record_id = cursor
        for index, item in enumerate(ordered):
            if (item.created_at, item.id) > (created_at, record_id):
                start = index
                break
        else:
            return [], None
    page = ordered[start : start + limit]
    if not page:
        return [], None
    last = page[-1]
    next_cursor = None if len(page) < limit else (last.created_at, last.id)
    return page, next_cursor
""",
        "rag_prompt_injection_policy_override_001": """from app.rag.models import RetrievedDocument


def wrap_retrieved_context(documents: list[RetrievedDocument]) -> str:
    blocks = []
    for document in documents:
        blocks.append(
            f"[UNTRUSTED_RETRIEVED_CONTEXT id={document.id}]\\n"
            f"{document.text}\\n"
            "[/UNTRUSTED_RETRIEVED_CONTEXT]"
        )
    return "\\n".join(blocks)


def build_prompt(
    *, system_instructions: str, retrieved: list[RetrievedDocument]
) -> str:
    context_block = wrap_retrieved_context(retrieved)
    return (
        f"SYSTEM_INSTRUCTIONS:\\n{system_instructions}\\n\\n"
        f"RETRIEVED_CONTEXT_DATA:\\n{context_block}"
    )
""",
    }

    def __init__(self, mode: str = "perfect") -> None:
        self.mode = mode.replace("-", "_")
        self.model = self.mode

    def safe_config(self) -> dict[str, object]:
        return {"mode": self.mode}

    def _patch(self, context: CaseContext, *, bad: bool = False) -> str:
        _, _, path, _, _ = self.ANSWERS[context.case.id]
        original = context.relevant_files[path]
        replacement = self.FIXED_FILES[context.case.id]
        if bad:
            comment = (
                "-- patch attempted\n"
                if path.endswith(".sql")
                else (
                    "// patch attempted\n"
                    if path.endswith((".ts", ".tsx", ".java"))
                    else "# patch attempted\n"
                )
            )
            replacement = original + comment
        return "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                replacement.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )

    def _keyword_gamer_patch(self, context: CaseContext) -> str:
        _, _, path, _, _ = self.ANSWERS[context.case.id]
        original = context.relevant_files[path]
        lines = original.splitlines(keepends=True)
        markers = self.KEYWORD_GAMER_PATCH_MARKERS.get(context.case.id, [])
        for needle, insertion in markers:
            for index, line in enumerate(lines):
                if needle in line:
                    lines.insert(index, insertion)
                    break
        return "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )

    def _primary_finding(self, context: CaseContext, partial: bool = False) -> Finding:
        summary, terms, path, line_range, fix = self.ANSWERS[context.case.id]
        if partial:
            fix = "Add an appropriate guard before this operation."
        patch = None
        if self.mode == "perfect_patch":
            patch = self._patch(context)
        elif self.mode == "bad_patch":
            patch = self._patch(context, bad=True)
        elif self.mode == "malformed_patch":
            patch = "this is not a unified diff"
        elif self.mode == "false_positive_patch":
            patch = self._patch(context)
        elif self.mode == "keyword_gamer":
            patch = self._keyword_gamer_patch(context)
        nearby = self.KEYWORD_GAMER_NEARBY.get(context.case.id, "")
        gamer_voice = self.KEYWORD_GAMER_VOICE.get(context.case.id, "")
        if self.mode == "keyword_gamer":
            summary_text = (
                f"{summary} In `{path}` (see also `{nearby}`): {terms}. "
                f"Structural validation context: {gamer_voice}. "
                f"Recommended fix: {fix}."
            )
        else:
            summary_text = f"{summary} This concerns {terms}."
        return Finding(
            title=summary,
            summary=summary_text,
            category=context.case.category,
            severity=context.case.severity if not partial else "medium",
            file=path,
            line_start=line_range[0] if not partial else max(1, line_range[0] - 2),
            line_end=line_range[1] if not partial else line_range[0],
            evidence="The newly changed production path introduces the seeded behavior.",
            suggested_fix=fix,
            suggested_patch=patch,
            patch_confidence=0.99 if patch else None,
            confidence=0.99 if not partial else 0.62,
        )

    def review(self, context: CaseContext) -> ReviewerResponse:
        started = time.perf_counter()
        if self.mode == "invalid_json":
            raw = "{not-valid-review-output"
            outcome = parse_reviewer_output(raw)
            return response_from_outcome(
                outcome, raw=raw, latency_ms=int((time.perf_counter() - started) * 1000)
            )
        findings = [self._primary_finding(context, partial=self.mode in {"partial", "bad"})]
        if self.mode in {"false_positive", "bad", "false_positive_patch"}:
            findings.append(
                Finding(
                    title="Rename local variable",
                    summary="The name could be clearer, but this is only a style preference.",
                    category="correctness",
                    severity="low",
                    file="unrelated/style.py",
                    line_start=1,
                    line_end=1,
                    evidence="No behavioral evidence.",
                    suggested_fix="Rename the variable.",
                    confidence=0.3,
                )
            )
        result = ReviewResult(
            findings=findings,
            overall_risk=context.case.severity,
            review_summary=f"Control {self.mode} review for {context.case.id}.",
            # The case-level repair is the primary finding's patch (None for the
            # review-only and false-positive modes that propose no fix).
            proposed_patch=findings[0].suggested_patch if findings else None,
        )
        # A built-in control emits a known-valid ReviewResult: record it as exact
        # without round-tripping through text salvage.
        # A built-in control emits a known-valid ReviewResult: record it as exact
        # without round-tripping through text salvage.
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
            output_tokens=max(1, len(raw) // 4),
        )
