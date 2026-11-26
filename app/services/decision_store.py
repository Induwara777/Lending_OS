"""Where finished decisions are kept.

This first version keeps them in memory. The router only uses save() and latest(),
so the real database can replace it later without changing the router.
"""
from __future__ import annotations

from uuid import UUID

from app.domain.models import DecisionRecord, DecisionResponse


class InMemoryDecisionStore:
    def __init__(self) -> None:
        self._by_application: dict[UUID, list[DecisionRecord]] = {}

    def save(self, record: DecisionRecord) -> None:
        """Decisions are only ever added, never changed."""
        self._by_application.setdefault(record.decision.application_id, []).append(record)

    def latest_record(self, application_id: UUID) -> DecisionRecord | None:
        history = self._by_application.get(application_id)
        return history[-1] if history else None

    def latest(self, application_id: UUID) -> DecisionResponse | None:
        record = self.latest_record(application_id)
        return record.decision if record else None