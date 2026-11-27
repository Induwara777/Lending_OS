from pathlib import Path
from uuid import uuid4

import yaml
from app.domain.models import DecisionRequest, FailureCause, Outcome, RuleStatus
from app.services.rules_engine import RulesEngine
from tests.test_sample import sample_customer, STUB_SCORES

PACK_PATH = Path(__file__).parent.parent / "client_packs" / "demo" / "decision.yaml"


def make_engine():
    pack = yaml.safe_load(PACK_PATH.read_text(encoding="utf-8"))
    return RulesEngine(pack)


def test_personal_loan_gets_33_rules_before_scoring():
    rules = make_engine().select_rules("personal_loan")
    assert len(rules) == 33


def test_risk_rules_only_run_after_scoring():
    engine = make_engine()
    after = engine.select_rules("personal_loan", stage="after_scoring")
    assert len(after) == 5
    assert all(r["id"].startswith("CP-RISK") for r in after)


def test_gold_loan_gets_gold_cap_but_not_minimum_income():
    ids = [r["id"] for r in make_engine().select_rules("gold_loan")]
    assert "CP-REG-002" in ids
    assert "CP-ELG-006" not in ids

def test_fraud_rule_fails_when_fraud_is_true():
    engine = make_engine()
    check = engine.get_rule("CP-DOC-006")["check"]
    assert engine.evaluate_check(check, {"fraud_indicator_verified": True}) is False


def test_fraud_rule_passes_when_fraud_is_false():
    engine = make_engine()
    check = engine.get_rule("CP-DOC-006")["check"]
    assert engine.evaluate_check(check, {"fraud_indicator_verified": False}) is True


def test_missing_data_fails_the_check():
    engine = make_engine()
    check = engine.get_rule("CP-DOC-006")["check"]
    assert engine.evaluate_check(check, {}) is False


def test_identity_rule():
    engine = make_engine()
    check = engine.get_rule("CP-ELG-008")["check"]
    assert engine.evaluate_check(check, {"identity_verified": True}) is True
    assert engine.evaluate_check(check, {"identity_verified": False}) is False


def test_operators():
    engine = make_engine()
    assert engine.evaluate_check({"field": "x", "op": "gte", "value": 18}, {"x": 18}) is True
    assert engine.evaluate_check({"field": "x", "op": "gte", "value": 18}, {"x": 17}) is False
    assert engine.evaluate_check({"field": "x", "op": "in", "value": ["a", "b"]}, {"x": "a"}) is True
    assert engine.evaluate_check({"field": "x", "op": "in", "value": ["a", "b"]}, {"x": "c"}) is False


def test_param_check_uses_parameters():
    engine = make_engine()
    rule = engine.get_rule("CP-ELG-001")
    assert engine.evaluate_check(rule["check"], {"age_years": 20}, rule["parameters"]) is True
    assert engine.evaluate_check(rule["check"], {"age_years": 17}, rule["parameters"]) is False


def test_age_is_derived_from_date_of_birth():
    engine = make_engine()
    rule = engine.get_rule("CP-ELG-001")
    f = {"date_of_birth": "2008-12-01", "application_date": "2026-11-25"}   # 17 years old
    assert engine.evaluate_check(rule["check"], f, rule["parameters"]) is False


def test_dated_vehicle_ltv_cap():
    engine = make_engine()
    chk = engine.get_rule("CP-REG-001")["check"]
    f = {"ltv_pct": 45, "vehicle_class": "motor_car_suv_van",
         "vehicle_age_group": "unregistered_or_under_1_year"}
    assert engine.evaluate_check(chk, {**f, "application_date": "2025-09-01"}) is True    # cap 60
    assert engine.evaluate_check(chk, {**f, "application_date": "2026-06-01"}) is False   # cap 40


def request(features, product="personal_loan"):
    return DecisionRequest(application_id=uuid4(), product=product, features=features)


def test_knock_out_failure_gives_decline_with_reason_code():
    res = make_engine().evaluate(request({"identity_verified": False}))
    assert res.outcome == Outcome.DECLINE
    assert "KYC_IDENTITY_FAILURE" in [r.code for r in res.reasons]


def test_general_rule_failure_gives_refer():
    res = make_engine().evaluate(request({"pd_pct": 0.5}), stage="after_scoring")
    # RISK rules are not knock-outs; null limits are skipped, missing data is REFER
    assert res.outcome == Outcome.REFER


def test_missing_data_gives_refer_never_decline():
    res = make_engine().evaluate(request({}))
    assert res.outcome == Outcome.REFER
    assert [r.code for r in res.reasons] == ["DATA_MISSING"]
    assert all(r.effect == Outcome.REFER for r in res.rule_results if r.status == RuleStatus.FAILED)


def test_missing_data_detail_names_the_missing_fields():
    res = make_engine().evaluate(request({}))
    row = next(r for r in res.rule_results if r.rule_id == "CP-ELG-008")
    assert row.cause == FailureCause.DATA_MISSING
    assert "identity_verified" in row.detail


def test_decline_beats_refer_and_decline_reasons_come_first():
    res = make_engine().evaluate(request({"identity_verified": False}))      # one real breach, the rest missing
    assert res.outcome == Outcome.DECLINE
    assert res.reasons[0].code == "KYC_IDENTITY_FAILURE"
    assert "DATA_MISSING" in [r.code for r in res.reasons]


def test_pd_is_a_percent():
    engine = make_engine()
    chk, params = engine.get_rule("CP-RISK-002")["check"], engine.get_rule("CP-RISK-002")["parameters"]
    assert engine.evaluate_check(chk, {"pd_pct": 5.0}, params, product="personal_loan") is True    # limit 6 %
    assert engine.evaluate_check(chk, {"pd_pct": 7.0}, params, product="personal_loan") is False


def test_null_limit_is_skipped_not_failed():
    pack = yaml.safe_load(PACK_PATH.read_text(encoding="utf-8"))
    next(r for r in pack["credit_policy"] if r["id"] == "CP-ELG-006")["parameters"]["min_monthly_income_cents_by_product"]["personal_loan"] = None
    res = RulesEngine(pack).evaluate(request({"monthly_gross_income_cents": 1000}))
    row = next(r for r in res.rule_results if r.rule_id == "CP-ELG-006")
    assert row.status == RuleStatus.SKIPPED


def test_same_ratio_gives_different_result_per_product():
    engine = make_engine()
    f = {"total_monthly_debt_obligations_cents": 50, "monthly_gross_income_cents": 100}     # DSR 50 %
    def dsr(product):
        res = engine.evaluate(request(f, product=product))
        return next(r for r in res.rule_results if r.rule_id == "CP-AFF-001").status
    assert dsr("personal_loan") == RuleStatus.FAILED      # limit 40
    assert dsr("gold_loan") == RuleStatus.PASSED          # limit 60


def test_demo_pack_loads_without_warnings():
    from app.clientpack.loader import load_client_pack
    assert load_client_pack(PACK_PATH).warnings == ()


def test_missing_data_is_marked_data_missing():
    res = make_engine().evaluate(request({}))
    row = next(r for r in res.rule_results if r.rule_id == "CP-ELG-008")
    assert row.cause == FailureCause.DATA_MISSING


def test_guarantor_rule_only_applies_when_guarantor_present():
    res = make_engine().evaluate(request({"has_guarantor": False}))
    row = next(r for r in res.rule_results if r.rule_id == "CP-ELG-007")
    assert row.status == RuleStatus.SKIPPED


def test_unknown_product_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        make_engine().evaluate(request({}, product="pawn"))


def test_sample_customers_pass_every_rule_before_scoring():
    
    engine = make_engine()
    for product in engine.products:
        res = engine.evaluate(request(sample_customer(product), product=product), stage="before_scoring")
        assert res.outcome is None, (product, [r.code for r in res.reasons])      # scoring is tested in test_scorer.py


def test_unverified_income_documents_decline_through_the_engine():
    data = {**sample_customer("personal_loan"), "bank_statements": False}
    res = make_engine().evaluate(request(data))
    assert res.outcome == Outcome.DECLINE
    assert "INCOME_NOT_VERIFIED" in [r.code for r in res.reasons]


def test_amount_above_every_authority_level_is_referred():
    data = {**sample_customer("sme_loan"), **STUB_SCORES, "requested_amount_cents": 6000000000}
    res = make_engine().evaluate(request(data, product="sme_loan"), stage="after_scoring")
    assert res.outcome == Outcome.REFER
    assert "AUTHORITY_LIMIT_EXCEEDED" in [r.code for r in res.reasons]


def test_loader_rejects_a_bad_data_missing_setting(tmp_path):
    import pytest
    from app.clientpack.loader import ClientPackError, load_client_pack
    text = PACK_PATH.read_text(encoding="utf-8").replace("data_missing: REFER", "data_missing: APPROVE")
    bad = tmp_path / "decision.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ClientPackError, match="data_missing must be DECLINE or REFER"):
        load_client_pack(bad)