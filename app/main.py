"""Starts the decision-engine service.

Run:  uvicorn app.main:app --reload      then open http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api import decisions, dev, errors, health
from app.clientpack.loader import ClientPackError, load_client_pack
from app.config import get_settings
from app.services.decision_service import DecisionService
from app.services.decision_store import InMemoryDecisionStore
from app.services.rules_engine import RulesEngine
from app.services.scorer import StubScorer

log = logging.getLogger("decision_engine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def create_app(pack_path: str | Path | None = None, dev_endpoints: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        path = pack_path or get_settings().client_pack_path
        try:
            pack = load_client_pack(path)           # a broken pack stops the service here (PLT-02)
        except ClientPackError as exc:
            log.error("client pack is invalid, the service will not start:\n%s", exc)
            raise
        for warning in pack.warnings:
            log.warning("client pack: %s", warning)
        app.state.pack = pack
        app.state.engine = RulesEngine(pack)
        app.state.store = InMemoryDecisionStore()
        app.state.decisions = DecisionService(app.state.engine, StubScorer())   # StubScorer until the baseline scorer exists
        log.info("started with client pack %s (rules %s)", pack.pack_version, pack.rule_set_version)
        yield

    app = FastAPI(title="LendOS 360 decision-engine", version="1.0.0", lifespan=lifespan)
    errors.install(app)
    app.include_router(health.router)                       # /health, /ready
    app.include_router(health.router, prefix="/api/v1")     # same, behind the gateway path
    app.include_router(decisions.router)
    if get_settings().enable_dev_endpoints if dev_endpoints is None else dev_endpoints:
        log.warning("DEV endpoints are ON (/dev/*). Never enable them in production.")
        app.include_router(dev.router)
    return app


app = create_app()