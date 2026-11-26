from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.clientpack.loader import ClientPackError
from app.domain.models import DecidedBy, DecisionRecord, DecisionResponse, Outcome, ReasonOut, ScoresOut
from app.main import create_app

PACK = Path(__file__).parent.parent / "client_packs" / "demo" / "decision.yaml"


@pytest.fixture()
def client():
    with TestClient(create_app(PACK)) as c:
        yield c


def a_decision(application_id):
    return DecisionResponse(
        decision_id=uuid4(), application_id=application_id, outcome=Outcome.REFER,
        decided_by=DecidedBy.SYSTEM, reasons=[ReasonOut(code="DSR_ABOVE_LIMIT", text="Commitments are high.")],
        max_approvable_amount_cents=180000000,
        scores=ScoresOut(pd_pct=7.0, lgd_pct=35.0, fraud_pct=2.0, model_version="baseline-1.0"),
        rule_set_version="policy-rules-2.0.0", decided_at=datetime(2026, 11, 16, 9, 21, 30, tzinfo=timezone.utc))


def test_health_shows_the_pack_version(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["client_pack_version"] == "policy-2.0.0"
    assert client.get("/api/v1/health").status_code == 200


def test_ready(client):
    assert client.get("/ready").json() == {"status": "ready"}


def test_get_decision_returns_the_contract_fields(client):
    app_id = uuid4()
    client.app.state.store.save(DecisionRecord(decision=a_decision(app_id), features_hash="x", rule_results=[]))
    res = client.get(f"/api/v1/decisions/{app_id}")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"decision_id", "application_id", "outcome", "decided_by", "reasons",
                         "max_approvable_amount_cents", "approved_amount_cents", "scores",
                         "rule_set_version", "explanation", "decided_at"}
    assert body["outcome"] == "REFER" and body["approved_amount_cents"] is None
    assert body["decided_at"] == "2026-11-16T09:21:30Z"
    assert body["reasons"] == [{"code": "DSR_ABOVE_LIMIT", "text": "Commitments are high."}]


def test_unknown_application_gives_the_contract_error_shape(client):
    app_id = uuid4()
    res = client.get(f"/api/v1/decisions/{app_id}")
    assert res.status_code == 404
    assert res.json() == {"code": "NOT_FOUND", "message": "No decision for this application.",
                          "details": [], "correlation_id": str(app_id)}   # equals the application ID
    assert res.headers["X-Correlation-Id"] == str(app_id)


def test_a_given_correlation_id_is_echoed(client):
    res = client.get(f"/api/v1/decisions/{uuid4()}", headers={"X-Correlation-Id": "abc-123"})
    assert res.json()["correlation_id"] == "abc-123"


def test_bad_id_gives_validation_failed(client):
    res = client.get("/api/v1/decisions/not-a-uuid")
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "VALIDATION_FAILED" and body["details"][0]["field"] == "application_id"


def test_unknown_path_uses_the_same_error_shape(client):
    res = client.get("/api/v1/nothing-here")
    assert res.status_code == 404 and res.json()["code"] == "NOT_FOUND"


def test_service_refuses_to_start_with_a_broken_pack(tmp_path):
    bad = tmp_path / "decision.yaml"
    bad.write_text(PACK.read_text(encoding="utf-8").replace("op: lte", "op: lte2", 1), encoding="utf-8")
    with pytest.raises(ClientPackError):
        with TestClient(create_app(bad)):
            pass


@pytest.fixture()
def dev_client():
    with TestClient(create_app(PACK, dev_endpoints=True)) as c:
        yield c


def test_dev_endpoints_are_off_by_default(client):
    assert client.get("/dev/sample/personal_loan").status_code == 404


def test_dev_sample_then_evaluate_gives_next_stage(dev_client):
    sample = dev_client.get("/dev/sample/personal_loan").json()
    res = dev_client.post("/dev/evaluate", json=sample).json()
    assert res["final_result"] == "NEXT_STAGE" and res["outcome"] is None


def test_dev_evaluate_declines_a_broken_rule(dev_client):
    sample = dev_client.get("/dev/sample/personal_loan").json()
    sample["features"]["identity_verified"] = False
    res = dev_client.post("/dev/evaluate", json=sample).json()
    assert res["final_result"] == "DECLINE"
    assert res["reasons"][0]["code"] == "KYC_IDENTITY_FAILURE"


def test_dev_evaluate_refers_missing_data(dev_client):
    res = dev_client.post("/dev/evaluate", json={"product": "personal_loan", "features": {}}).json()
    assert res["final_result"] == "REFER"


def test_dev_evaluate_rejects_an_unknown_product(dev_client):
    res = dev_client.post("/dev/evaluate", json={"product": "pawn", "features": {}})
    assert res.status_code == 422 and res.json()["code"] == "VALIDATION_FAILED"


def test_dev_decide_then_get_the_decision_through_the_contract_endpoint(dev_client):
    sample = dev_client.get("/dev/sample/personal_loan").json()
    record = dev_client.post("/dev/decide", json=sample).json()
    app_id = record["decision"]["application_id"]
    res = dev_client.get(f"/api/v1/decisions/{app_id}")
    assert res.status_code == 200
    assert res.json()["outcome"] == "APPROVE" and res.json()["decided_by"] == "SYSTEM"
    assert len(record["features_hash"]) == 64