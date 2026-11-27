
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol
from pydantic import BaseModel
from app.domain.models import DecisionRequest
from app.services import maths_fun
from app.services.derived_fields import MissingData, NotConfigured

if TYPE_CHECKING:
    from app.services.rules_engine import RulesEngine

# OUTPUT OF BASELINESCORER.score
class Scores(BaseModel):
    pd_pct: float                     
    lgd_pct: float                      
    fraud_pct: float | None = None
    internal_score: float | None = None
    risk_grade: str | None = None      
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

# FLOW : HOW TO CALCULATE THE SCORE OF SCORECARD
class BaselineScorer:
    def __init__(self, engine: "RulesEngine"):
        self.engine = engine
        self.card = engine.scorecard

    # MAIN SCORING FUNCTION
    def score(self, request: DecisionRequest) -> Scores | None:
        """None when a needed field is missing or the scorecard has no setting for the product."""
        try:
            points = self.card["base_points"]
            for factor in self.card["factors"]:
                if "products" in factor and request.product not in factor["products"]:
                    continue
                points += self._points(factor, self.engine.value(factor["field"], request.features, request.product))
            score = max(0.0, min(1000.0, float(points)))
            grade = next(g for g in self.card["grades"] if score >= g["min_score"])
            lgd = self._lgd(request)
        except (MissingData, NotConfigured, KeyError, TypeError, ZeroDivisionError, StopIteration):
            return None
        return Scores(pd_pct=grade["pd_pct"], lgd_pct=round(lgd, 2), fraud_pct=self._fraud(request.features),
                      internal_score=score, risk_grade=grade["grade"], model_version=self.card["version"])

    @staticmethod  #NO ACCESS FROM BaselineScorer class.
    def _points(factor: dict, value) -> float:
        if "map" in factor:
            return factor["map"].get(value, factor.get("default", 0))
        for band in factor["bands"]:                       # first band the value fits; the last one has no limit
            if "up_to" not in band or value <= band["up_to"]:
                return band["points"]
        raise MissingData(factor["field"])

    # CALCULATE LGD (LOSS GIVEN DEFAULT)
    def _lgd(self, request: DecisionRequest) -> float:
        spec = self.card["lgd_by_product"][request.product]
        if "fixed_pct" in spec:
            return float(spec["fixed_pct"])
        loan = self.engine.value("credit_facility_amount_cents", request.features, request.product)
        collateral = self.engine.value("collateral_value_cents", request.features, request.product)
        return maths_fun.lgd_baseline_secured(loan, collateral, spec["haircut_pct"], spec["floor_pct"])

    def _fraud(self, features: dict) -> float:
        raised = sum(f["points"] for f in (self.card.get("fraud") or {}).get("flags", [])
                     if features.get(f["field"]) is True)   # a flag that is absent counts as not raised
        return float(min(100, raised))