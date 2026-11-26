"""/health and /ready: no login needed (API contract, section 6)."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["platform"])


@router.get("/health")
def health(request: Request):
    pack = request.app.state.pack
    return {"status": "ok", "service": "decision-engine",
            "client_pack_version": pack.pack_version, "rule_set_version": pack.rule_set_version}


@router.get("/ready")
def ready(request: Request):
    if getattr(request.app.state, "engine", None) is None:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready"}
