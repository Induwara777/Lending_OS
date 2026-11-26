from pathlib import Path
from uuid import uuid4

import yaml

from app.clientpack.loader import load_client_pack
from app.domain.models import DecidedBy, DecisionRequest, Outcome
from app.services.decision_service import DecisionService, hash_features
from app.services.rules_engine import RulesEngine
from app.services.scorer import StubScorer
from tests.test_sample import sample_customer

PACK = Path(__file__).parent.parent / "client_packs" / "demo" / "decision.yaml"


def service():
    return DecisionService(RulesEngine(load_client_pack(PACK)), StubScorer())


def decide(features, product="personal_loan", **kw):
    return service().decide(DecisionRequest(application_id=uuid4(), product=product, features=features), **kw)


def codes(record):
    return [r.code for r in record.decision.reasons]


def test_clean_applicant_is_approved_for_every_product():
    for product in service().engine.products:
        d = decide(sample_customer(product), product).decision
        assert d.outcome == Outcome.APPROVE and d.reasons == [], product
        assert d.decided_by == DecidedBy.SYSTEM
        assert d.scores.pd_pct == 2.0 and d.scores.model_version == "stub-0"
        assert d.rule_set_version == "policy-rules-2.0.0"
        assert d.decided_at.tzinfo is not None


def test_broken_knock_out_declines_and_skips_scoring():
    d = decide({**sample_customer("personal_loan"), "identity_verified": False}).decision
    assert d.outcome == Outcome.DECLINE
    assert codes(decide({**sample_customer("personal_loan"), "identity_verified": False})) == ["KYC_IDENTITY_FAILURE"]
    assert d.scores is None                                   # scoring never ran
    assert d.reasons[0].text == "The applicant's identity could not be verified."


def test_decline_reasons_come_before_refer_reasons():
    data = {**sample_customer("personal_loan"), "identity_verified": False}
    del data["bank_statements"]                               # missing data -> a REFER reason
    result = codes(decide(data))
    assert result[0] == "KYC_IDENTITY_FAILURE" and "DATA_MISSING" in result


def test_missing_data_is_refer_with_a_readable_text():
    d = decide({k: v for k, v in sample_customer("personal_loan").items() if k != "monthly_gross_income_cents"}).decision
    assert d.outcome == Outcome.REFER
    assert d.reasons[0].code == "DATA_MISSING" and "officer" in d.reasons[0].text


def test_scoring_unavailable_is_refer_never_approve():
    data = {k: v for k, v in sample_customer("personal_loan").items() if k not in ("pd_pct", "lgd_pct")}
    record = decide(data)
    assert record.decision.outcome == Outcome.REFER
    assert codes(record) == ["SCORING_UNAVAILABLE"]
    assert record.decision.scores is None


def test_pd_above_limit_is_refer_after_scoring():
    record = decide({**sample_customer("personal_loan"), "pd_pct": 9.0})      # limit for personal loans is 6
    assert record.decision.outcome == Outcome.REFER
    assert "PD_ABOVE_LIMIT" in codes(record)


def test_risk_grade_outside_the_authority_matrix_is_refer():
    record = decide({**sample_customer("sme_loan"), "risk_grade": "E"}, "sme_loan")     # no approver level covers grade E
    assert record.decision.outcome == Outcome.REFER and "AUTHORITY_LIMIT_EXCEEDED" in codes(record)


def test_amount_above_the_product_maximum_is_declined_before_scoring():
    record = decide({**sample_customer("sme_loan"), "requested_amount_cents": 6000000000}, "sme_loan")
    assert record.decision.outcome == Outcome.DECLINE and codes(record) == ["LOAN_AMOUNT_ABOVE_MAXIMUM"]
    assert record.decision.scores is None


def test_the_record_keeps_every_rule_result():
    record = decide(sample_customer("personal_loan"))
    ids = [r.rule_id for r in record.rule_results]
    assert "CP-ELG-008" in ids and "CP-RISK-002" in ids      # before and after scoring


def test_features_hash_is_stable_and_can_be_supplied():
    data = sample_customer("personal_loan")
    assert hash_features(data) == hash_features(dict(reversed(list(data.items()))))
    assert decide(data).features_hash == hash_features(data)
    assert decide(data, snapshot_hash="abc123").features_hash == "abc123"
    assert hash_features({**data, "pd_pct": 3.0}) != hash_features(data)


def test_every_decision_gets_its_own_id():
    data = sample_customer("personal_loan")
    assert decide(data).decision.decision_id != decide(data).decision.decision_id


def test_every_reason_code_in_the_pack_has_a_text():
    pack = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    needed = {r["reason_code"] for r in pack["credit_policy"]} | {"DATA_MISSING", "SCORING_UNAVAILABLE"}
    assert needed <= set(pack["reason_texts"])
