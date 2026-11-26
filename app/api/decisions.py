"""Decision endpoints (API contract, section 8.4)."""
from uuid import UUID

from fastapi import APIRouter, Request

from app.api.errors import ApiError
from app.domain.models import DecisionResponse

router = APIRouter(prefix="/api/v1", tags=["decisions"])

# Reserved words such as "mine" must be registered BEFORE this route (contract, section 5),
# so GET /decisions/mine is never read as GET /decisions/{application_id}.


@router.get("/decisions/{application_id}", response_model=DecisionResponse)
def get_decision(application_id: UUID, request: Request):
    request.state.correlation_id = request.headers.get("X-Correlation-Id") or str(application_id)
    decision = request.app.state.store.latest(application_id)
    if decision is None:
        raise ApiError(404, "NOT_FOUND", "No decision for this application.")
    return decision
