"""Data shapes used by the decision-engine."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class Outcome(str, Enum):
    APPROVE = "APPROVE"
    REFER = "REFER"
    DECLINE = "DECLINE"


# A bigger number is a stronger outcome: DECLINE beats REFER beats APPROVE.
OUTCOME_SEVERITY = {Outcome.APPROVE: 0, Outcome.REFER: 1, Outcome.DECLINE: 2}


class RuleStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"          # the rule did not run (the reason is in `detail`)


class FailureCause(str, Enum):
    VIOLATED = "VIOLATED"            # the data was there and the rule was broken
    DATA_MISSING = "DATA_MISSING"    # a field the rule needs was missing


class DecisionRequest(BaseModel):
    """What the decision-engine receives: one application's feature snapshot."""

    application_id: UUID
    product: str
    features: dict[str, Any]         # the snapshot: field name -> value


class Reason(BaseModel):
    code: str                        # reason code from the client pack
    source: str                      # ID of the rule that produced it


class RuleResult(BaseModel):
    """What happened to one rule for one application."""

    rule_id: str
    name: str
    knock_out: bool
    status: RuleStatus
    cause: FailureCause | None = None    # only when status is FAILED
    effect: Outcome | None = None        # DECLINE or REFER, only when FAILED
    reason_code: str | None = None       # only when FAILED
    detail: str = ""                     # readable explanation


class EvaluationResult(BaseModel):
    """The result of running the rules for one application."""

    application_id: UUID
    product: str
    outcome: Outcome | None              # None = no rule failed, application continues
    reasons: list[Reason]
    rule_results: list[RuleResult]
    rule_set_version: str

# ---------------------------------------------------------------------------
# API shapes from the API contract (sections 6.1 and 9.3). Field names are exact.
# ---------------------------------------------------------------------------
class DecidedBy(str, Enum):
    SYSTEM = "SYSTEM"
    OFFICER = "OFFICER"


class ReasonOut(BaseModel):
    code: str
    text: str


class ScoresOut(BaseModel):
    pd_pct: float | None = None
    lgd_pct: float | None = None
    fraud_pct: float | None = None
    model_version: str | None = None


class DecisionResponse(BaseModel):
    """GET /decisions/{application_id}"""

    decision_id: UUID
    application_id: UUID
    outcome: Outcome
    decided_by: DecidedBy
    reasons: list[ReasonOut]
    max_approvable_amount_cents: int | None = None
    approved_amount_cents: int | None = None   # null for a plain APPROVE, REFER and DECLINE
    scores: ScoresOut | None = None            # officer roles and services only (later: removed for branch_officer)
    rule_set_version: str
    explanation: str | None = None             # null before Phase 3
    decided_at: datetime


class ErrorDetail(BaseModel):
    field: str | None = None
    issue: str
    limit: Any | None = None


class ErrorResponse(BaseModel):
    """Every error of every endpoint uses this one shape."""

    code: str
    message: str
    details: list[ErrorDetail] = []
    correlation_id: str


class DecisionRecord(BaseModel):
    """What is stored for one decision. Written once and never changed (DEC-04)."""

    decision: DecisionResponse
    features_hash: str                 # fingerprint of the snapshot the decision used
    rule_results: list[RuleResult]     # every rule, for audit and for the officer's evidence screen