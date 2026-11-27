"""Tests for app/services/maths_fun.py (pure formulas)."""
import pytest

from app.services import maths_fun as m


def test_vehicle_loan_example_from_the_notes():
    # EAD 20,000; sold for 14,000; costs 1,500 -> net 12,500 -> LGD 37.5%
    assert m.lgd_vehicle_loan(20_000, 14_000, 1_500) == pytest.approx(37.5)


def test_gold_loan_example_from_the_notes():
    # EAD 10,000; sold for 9,500; costs 500 -> LGD 10%
    assert m.lgd_gold_loan(10_000, 9_500, 500) == pytest.approx(10.0)


def test_lgd_is_never_below_zero_or_above_100():
    assert m.lgd_vehicle_loan(10_000, 50_000, 0) == 0
    assert m.lgd_vehicle_loan(10_000, 0, 5_000) == 100


def test_discounting_raises_lgd():
    now = m.lgd_vehicle_loan(20_000, 14_000, 1_500)
    later = m.lgd_vehicle_loan(20_000, 14_000, 1_500, discount_rate_pct=10, years=1)
    assert later > now
    assert m.present_value(110, 10, 1) == pytest.approx(100)


def test_eads():
    assert m.ead_vehicle_loan(100, 5, 2) == 107
    assert m.ead_vehicle_leasing(100, 20, 3) == 123
    assert m.ead_gold_loan(100, 5, 1) == 106
    assert m.ead_personal_loan(100, 5, 2) == 107
    assert m.ead_personal_loan(100, 5, 2, 10) == 117
    assert m.ead_sme_loan(100, 5, 2, 8) == 115
    assert m.gold_sale_proceeds(50, 20) == 1_000


def test_personal_loan_unsecured():
    # EAD 1,000; collections 300 + legal 100 + guarantee 100 - costs 50 = 450 -> LGD 55%
    assert m.lgd_personal_loan(1_000, 300, 100, 100, 50) == pytest.approx(55.0)


def test_sme_split():
    secured = m.lgd_sme_secured(600, 400, 40)            # (600 - 360) / 600 = 40%
    unsecured = m.lgd_sme_unsecured(400, 100, 50, 0, 50)  # (400 - 100) / 400 = 75%
    assert secured == pytest.approx(40.0)
    assert unsecured == pytest.approx(75.0)
    assert m.lgd_sme_loan(600, secured, 400, unsecured) == pytest.approx(54.0)


def test_baseline_secured_matches_the_scorecard():
    # collateral covers the loan after the haircut -> floor applies
    assert m.lgd_baseline_secured(1_000_000, 1_500_000, 30, 10) == 10
    # collateral 800k, haircut 30% -> 560k recoverable -> LGD 44%
    assert m.lgd_baseline_secured(1_000_000, 800_000, 30, 10) == pytest.approx(44.0)


def test_basics():
    assert m.ltv_pct(80, 100) == 80
    assert m.dsr_pct(40, 100) == 40
    assert m.expected_loss(3.5, 10, 1_000_000) == pytest.approx(3_500)
    assert m.instalment(1_200, 0, 12) == 100
    assert m.instalment(1_000_000, 12, 12) == pytest.approx(88_848.79, abs=0.01)
    with pytest.raises(ZeroDivisionError):
        m.ltv_pct(1, 0)