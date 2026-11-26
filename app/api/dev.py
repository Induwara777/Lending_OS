"""DEV-ONLY endpoints for trying the engine with real-looking data.

Not part of the API contract. They exist only when DEV_ENDPOINTS=1 and must stay OFF in production.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.errors import ApiError
from app.domain.models import DecisionRequest
from tests.test_sample import sample_customer 

router = APIRouter(prefix="/dev", tags=["dev only (not in the contract)"])


class EvaluateIn(BaseModel):
    product: str
    features: dict[str, Any]          # the applicant's snapshot: field name -> value
    stage: Literal["before_scoring", "after_scoring"] = "before_scoring"


@router.get("/sample/{product}")
def sample(product: str, request: Request):
    """A clean sample applicant. Copy it, change some values, and send it to POST /dev/evaluate."""
    if product not in request.app.state.engine.products:
        raise ApiError(404, "NOT_FOUND", f"Unknown product '{product}'.")     # only needed while testing
    return {"product": product, "features": sample_customer(product)}


@router.post("/evaluate")
def evaluate(body: EvaluateIn, request: Request):
    """Run the rules on the data you send. Nothing is saved and no event is published."""
    engine = request.app.state.engine
    if body.product not in engine.products:
        raise ApiError(422, "VALIDATION_FAILED", "Unknown product.",
                       [{"field": "product", "issue": f"must be one of {engine.products}"}])
    result = engine.evaluate(DecisionRequest(application_id=uuid4(), product=body.product,
                                             features=body.features), stage=body.stage)
    final = result.outcome.value if result.outcome else "NEXT_STAGE"
    return {"final_result": final, **result.model_dump(mode="json")}


class DecideIn(BaseModel):
    product: str
    features: dict[str, Any]


@router.post("/decide")
def decide(body: DecideIn, request: Request):
    """Run the WHOLE decision (rules, scoring, rules after scoring) and keep it, so that
    GET /api/v1/decisions/{application_id} returns it. No event is published."""
    state = request.app.state
    if body.product not in state.engine.products:
        raise ApiError(422, "VALIDATION_FAILED", "Unknown product.",
                       [{"field": "product", "issue": f"must be one of {state.engine.products}"}])
    record = state.decisions.decide(DecisionRequest(application_id=uuid4(), product=body.product,
                                                    features=body.features))
    state.store.save(record)
    return record.model_dump(mode="json")
