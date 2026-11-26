"""One error format for every endpoint (API contract, section 6.1)."""
from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("decision_engine")
CODES = {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
         409: "INVALID_STATE", 422: "VALIDATION_FAILED", 429: "RATE_LIMITED",
         502: "UPSTREAM_UNAVAILABLE", 503: "UPSTREAM_UNAVAILABLE"}


class ApiError(Exception):
    """Raise this in an endpoint. code is one of the contract's error codes."""

    def __init__(self, status: int, code: str, message: str, details: list[dict] | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or []


def correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", None) or str(uuid4())


def _response(request: Request, status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"code": code, "message": message, "details": details or [],
            "correlation_id": correlation_id(request)}
    return JSONResponse(status_code=status, content=body)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_correlation_id(request: Request, call_next):
        # X-Correlation-Id equals the application ID when one exists; it is echoed back
        request.state.correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = request.state.correlation_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return _response(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = CODES.get(exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "BAD_REQUEST")
        return _response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"] if p not in ("path", "query", "body", "header")),
                    "issue": e["type"]} for e in exc.errors()]
        return _response(request, 422, "VALIDATION_FAILED", "The request is not valid.", details)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unexpected error", extra={"correlation_id": correlation_id(request)})
        return _response(request, 500, "INTERNAL_ERROR", "Something went wrong.")
