"""Risk scoring (DEC-02).

The decision service only knows the Scorer interface. StubScorer is the placeholder used until the
baseline scorecard exists: it takes scores that are already in the snapshot. The real scorer will
implement the same score() method, so nothing else changes.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.domain.models import DecisionRequest


class Scores(BaseModel):
    pd_pct: float                       # probability of default, 0-100
    lgd_pct: float                      # loss given default, 0-100
    fraud_pct: float | None = None
    internal_score: float | None = None
    risk_grade: str | None = None       # A (best) ... D (worst)
    model_version: str

    def as_features(self) -> dict:
        """The values the rules after scoring read."""
        data = {"pd_pct": self.pd_pct, "lgd_pct": self.lgd_pct, "fraud_pct": self.fraud_pct,
                "internal_score": self.internal_score, "risk_grade": self.risk_grade}
        return {k: v for k, v in data.items() if v is not None}


class Scorer(Protocol):
    def score(self, request: DecisionRequest) -> Scores | None:
        """Return the scores, or None when scoring is unavailable or a required feature is missing."""


class StubScorer:
    """Reads ready-made scores from the snapshot. For development and tests only."""

    def score(self, request: DecisionRequest) -> Scores | None:
        f = request.features
        if f.get("pd_pct") is None or f.get("lgd_pct") is None:
            return None
        return Scores(pd_pct=f["pd_pct"], lgd_pct=f["lgd_pct"], fraud_pct=f.get("fraud_pct"),
                      internal_score=f.get("internal_score"), risk_grade=f.get("risk_grade"),
                      model_version=str(f.get("model_version", "stub-0")))
