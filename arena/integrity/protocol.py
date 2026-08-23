"""The versioned, hashed CRA-Integrity experiment protocol.

A benchmark whose semantics can drift silently produces numbers that cannot be
compared across time. This module pins every decision that changes what a result
means -- which pairs, which reviewer information condition, which visible command,
which oracle bytes, which taxonomy, which resource and network policy, which
scoring version -- canonicalizes it, hashes it, and stores the digest in the run.

Two runs with the same digest measured the same thing. Two runs with different
digests did not, and no amount of similar-looking metrics makes them comparable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from arena.core.bounded_io import read_bytes_bounded
from arena.core.limits import PACK_FILE_BYTES
from arena.integrity import INTEGRITY_TRACK_VERSION
from arena.integrity.loader import oracle_dir, relative_files
from arena.integrity.models import (
    INTEGRITY_TAXONOMY_VERSION,
    IntegrityPair,
    ReviewerContextCondition,
)

#: Bumped whenever the meaning of a recorded integrity result changes.
INTEGRITY_PROTOCOL_VERSION = "1.0"
#: Bumped whenever a metric definition in arena.integrity.scoring changes.
INTEGRITY_SCORING_VERSION = "1.0"

#: The pilot runs every variant exactly once and never retries. A retried review
#: would silently become a best-of-N measurement, which is a different experiment.
RETRY_POLICY = "none: one review per variant, failures recorded not retried"
#: Both surfaces execute with the network disabled under Docker; trusted-local
#: execution inherits the harness sandbox (allowlisted env, isolated HOME/TMPDIR,
#: bounded CPU/memory) but is not a security boundary.
NETWORK_POLICY = "docker: --network none; trusted-local: no network isolation enforced"


def canonical_json(payload: Any) -> str:
    """Stable serialization: sorted keys, no insignificant whitespace, ASCII-escaped."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_of(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def oracle_digest(pair: IntegrityPair) -> str:
    """Content digest over every trusted-oracle file (path, mode-independent bytes)."""
    root = oracle_dir(pair)
    hasher = hashlib.sha256()
    for relative in relative_files(root):
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(
            hashlib.sha256(
                read_bytes_bounded(root / relative, PACK_FILE_BYTES, label="oracle file")
            ).digest()
        )
    return hasher.hexdigest()


def visible_validation_definition(pair: IntegrityPair) -> dict[str, Any]:
    return {
        "command": pair.candidate_validation.command,
        "validation_paths": sorted(pair.candidate_validation.paths),
        "timeout_seconds": pair.candidate_validation.timeout_seconds,
    }


def trusted_oracle_definition(pair: IntegrityPair) -> dict[str, Any]:
    return {
        "command": pair.trusted_oracle.command,
        "mount": pair.trusted_oracle.mount,
        "timeout_seconds": pair.trusted_oracle.timeout_seconds,
        "oracle_digest": oracle_digest(pair),
    }


def pair_definition(pair: IntegrityPair) -> dict[str, Any]:
    """Everything about one pair that changes what a result means."""
    return {
        "id": pair.id,
        "context_condition": pair.context_condition,
        "docker_image": pair.docker_image,
        "task_contract": digest_of(pair.task_contract.model_dump(mode="json")),
        "visible_validation": visible_validation_definition(pair),
        "trusted_oracle": trusted_oracle_definition(pair),
        "variants": [
            {
                "id": variant.id,
                "kind": variant.kind,
                "origin": variant.origin,
                "mechanisms": sorted(variant.mechanisms),
                "intent": variant.intent,
            }
            for variant in pair.variants()
        ],
        "alternates": sorted(item.id for item in pair.alternates),
    }


def pair_set_digest(pairs: list[IntegrityPair]) -> str:
    return digest_of([pair_definition(pair) for pair in sorted(pairs, key=lambda p: p.id)])


def build_protocol(
    *,
    pack: str,
    pairs: list[IntegrityPair],
    condition: ReviewerContextCondition,
    execution_backend: str,
    docker_image: str | None,
    docker_image_digest: str | None,
    resource_limits: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the complete protocol document for one run."""
    return {
        "protocol_version": INTEGRITY_PROTOCOL_VERSION,
        "track_version": INTEGRITY_TRACK_VERSION,
        "taxonomy_version": INTEGRITY_TAXONOMY_VERSION,
        "scoring_version": INTEGRITY_SCORING_VERSION,
        "pack": pack,
        "pair_set_digest": pair_set_digest(pairs),
        "pairs": [pair_definition(pair) for pair in sorted(pairs, key=lambda p: p.id)],
        "reviewer_context_condition": condition,
        "execution_backend": execution_backend,
        "docker_image": docker_image,
        "docker_image_digest": docker_image_digest,
        "resource_limits": dict(sorted(resource_limits.items())),
        "network_policy": NETWORK_POLICY,
        "retry_policy": RETRY_POLICY,
    }


def protocol_digest(protocol: dict[str, Any]) -> str:
    return digest_of(protocol)
