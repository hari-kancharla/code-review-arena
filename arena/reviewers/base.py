"""Reviewer protocol and shared behavior."""

from __future__ import annotations

from abc import ABC, abstractmethod

from arena.core.models import CaseContext, ReviewerResponse


class BaseReviewer(ABC):
    name: str
    model: str | None
    # True when this reviewer could read the pack's answer key (reference
    # patch, hidden tests, ground truth) while producing its review. Such a
    # score is not a blind measurement, and the run records it so the
    # leaderboard never ranks it against reviewers that were kept blind.
    oracle_reachable: bool = False

    @abstractmethod
    def review(self, context: CaseContext) -> ReviewerResponse:
        """Review a case without receiving its ground-truth answer."""

    @property
    def identifier(self) -> str:
        return f"{self.name}:{self.model}" if self.model else self.name

    def safe_config(self) -> dict[str, object]:
        """Reviewer configuration safe to persist in run manifests (no secrets)."""
        return {}
