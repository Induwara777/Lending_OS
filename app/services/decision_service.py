"""Builds one decision for one application (DEC-04).

snapshot -> rules before scoring -> scoring -> rules after scoring -> DecisionResponse

A broken knock-out rule ends the decision at once (DECLINE, no scoring). If scoring is unavailable the
outcome is REFER, never APPROVE (DEC-02). APPROVE only happens when nothing failed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.domain.models import (
    OUTCOME_SEVERITY, DecidedBy, DecisionRecord, DecisionRequest, DecisionResponse, Outcome,
    ReasonOut, RuleStatus, ScoresOut,
)
from app.services.rules_engine import RulesEngine
from app.services.scorer import Scorer

SCORING_UNAVAILABLE = "SCORING_UNAVAILABLE"


def hash_features(features: dict) -> str:
    """Fingerprint of a snapshot. The same data always gives the same hash, whatever the key order."""
    canonical = json.dumps(features, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DecisionService:
    def __init__(self, engine: RulesEngine, scorer: Scorer):
        self.engine, self.scorer = engine, scorer

    def decide(self, request: DecisionRequest, snapshot_hash: str | None = None) -> DecisionRecord:
        before = self.engine.evaluate(request, "before_scoring")
        rule_results = list(before.rule_results)
        outcomes = [before.outcome]
        scores, scoring_unavailable = None, False

        if before.outcome != Outcome.DECLINE:            # a knock-out stops everything: no scoring needed
            scores = self.scorer.score(request)
            if scores is None:
                scoring_unavailable = True
                outcomes.append(Outcome.REFER)
            else:
                features = {**request.features, **scores.as_features()}
                if "ead_cents" not in features and "credit_facility_amount_cents" in features:
                    features["ead_cents"] = features["credit_facility_amount_cents"]   # exposure at default = the loan
                after = self.engine.evaluate(request.model_copy(update={"features": features}), "after_scoring")
                rule_results += after.rule_results
                outcomes.append(after.outcome)

        outcome = max((o for o in outcomes if o), key=OUTCOME_SEVERITY.get, default=Outcome.APPROVE)
        failed = [r for r in rule_results if r.status == RuleStatus.FAILED]
        codes = []                                        # DECLINE reasons first, then REFER, no repeats
        for effect in (Outcome.DECLINE, Outcome.REFER):
            for r in failed:
                if r.effect == effect and r.reason_code not in codes:
                    codes.append(r.reason_code)
        if scoring_unavailable:
            codes.append(SCORING_UNAVAILABLE)
        texts = self.engine.reason_texts

        decision = DecisionResponse(
            decision_id=uuid4(), application_id=request.application_id, outcome=outcome,
            decided_by=DecidedBy.SYSTEM,
            reasons=[ReasonOut(code=c, text=texts.get(c, c)) for c in codes],
            scores=ScoresOut(pd_pct=scores.pd_pct, lgd_pct=scores.lgd_pct, fraud_pct=scores.fraud_pct,
                             model_version=scores.model_version) if scores else None,
            rule_set_version=self.engine.rule_set_version,
            decided_at=datetime.now(timezone.utc))
        return DecisionRecord(decision=decision, rule_results=rule_results,
                              features_hash=snapshot_hash or hash_features(request.features))
