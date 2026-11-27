from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from app.clientpack.loader import ClientPackError, load_client_pack
from app.domain.models import DecisionRequest
from app.services.rules_engine import RulesEngine
from app.services.scorer import BaselineScorer
from tests.test_sample import sample_customer

PACK = Path(__file__).parent.parent / "client_packs" / "demo" / "decision.yaml"


def scorer():
    return BaselineScorer(RulesEngine(load_client_pack(PACK)))


def score(features, product="personal_loan"):
    return scorer().score(DecisionRequest(application_id=uuid4(), product=product, features=features))


def test_sample_customers_get_the_hand_calculated_scores():
    expected = {"vehicle_loan": 745, "vehicle_leasing": 745, "gold_loan": 665, "personal_loan": 720, "sme_loan": 690}
    for product, points in expected.items():
        s = score(sample_customer(product), product)
        assert s.internal_score == points, product
        assert s.risk_grade == "B" and s.pd_pct == 3.5
        assert s.model_version == "baseline-1.0-demo"


def test_scores_in_the_snapshot_are_ignored():
    s = score({**sample_customer("personal_loan"), "pd_pct": 99.0, "risk_grade": "D"})
    assert s.pd_pct == 3.5 and s.risk_grade == "B"


def test_worse_arrears_lower_the_score():
    clean = score(sample_customer("personal_loan")).internal_score
    late = score({**sample_customer("personal_loan"), "worst_arrears_days": 45}).internal_score
    assert clean - late == 120                                   # +80 becomes -40


def test_a_missing_factor_field_means_scoring_is_unavailable():
    data = sample_customer("personal_loan")
    del data["worst_arrears_days"]
    assert score(data) is None


def test_unsecured_loan_uses_the_fixed_lgd():
    assert score(sample_customer("personal_loan")).lgd_pct == 70.0


def test_secured_loan_lgd_comes_from_the_collateral():
    # loan 200,000,000; collateral 250,000,000; vehicle haircut 30 %: recoverable 175,000,000 -> LGD 12.5 %
    data = {**sample_customer("vehicle_loan"), "collateral_value_cents": 250000000}
    assert score(data, "vehicle_loan").lgd_pct == 12.5


def test_well_covered_secured_loan_gets_the_lgd_floor():
    assert score(sample_customer("vehicle_loan"), "vehicle_loan").lgd_pct == 10.0


def test_secured_loan_without_collateral_value_cannot_be_scored():
    data = sample_customer("vehicle_loan")
    del data["collateral_value_cents"]
    assert score(data, "vehicle_loan") is None


def test_fraud_score_adds_up_the_raised_flags():
    data = sample_customer("personal_loan")
    assert score(data).fraud_pct == 0.0
    assert score({**data, "income_mismatch_flag": True, "mobile_mismatch_flag": True}).fraud_pct == 65.0


def test_grade_thresholds_match_the_score_bands_of_the_risk_rule():
    pack = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    bands = next(r for r in pack["credit_policy"] if r["id"] == "CP-RISK-001")["parameters"]["score_bands"]
    grades = {g["grade"]: g["min_score"] for g in pack["scorecard"]["grades"]}
    assert grades == bands


def test_loader_rejects_a_scorecard_whose_lowest_grade_does_not_start_at_zero(tmp_path):
    text = PACK.read_text(encoding="utf-8").replace("{min_score: 0,   grade: D", "{min_score: 100, grade: D")
    bad = tmp_path / "decision.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ClientPackError, match="min_score 0"):
        load_client_pack(bad)