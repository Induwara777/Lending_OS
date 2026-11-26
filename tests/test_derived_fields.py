from app.services.derived_fields import MissingData, NotConfigured, calculate, has_formula
import pytest


def calc(name, features, regulatory=None, parameters=None, product=None):
    def get(n):
        if features.get(n) is None:
            raise MissingData(n)
        return features[n]
    return calculate(name, get, {"regulatory": regulatory or {}, "parameters": parameters or {}, "product": product})


def test_debt_service_ratio():
    assert calc("debt_service_ratio_pct", {"total_monthly_debt_obligations_cents": 40, "monthly_gross_income_cents": 100}) == 40


def test_ltv_ratio():
    assert calc("ltv_pct", {"credit_facility_amount_cents": 300, "collateral_value_cents": 600}) == 50


def test_age_whole_years():
    f = {"date_of_birth": "1990-12-01", "application_date": "2026-11-25"}
    assert calc("age_years", f) == 35                       # birthday not reached yet


def test_age_at_maturity():
    f = {"date_of_birth": "1990-05-10", "application_date": "2026-11-25", "loan_tenure_months": 60}
    assert calc("age_at_maturity", f) == 41                 # final instalment 2031-11-25


def test_expected_loss():
    assert calc("expected_loss_cents", {"pd_pct": 2, "lgd_pct": 40, "ead_cents": 1000000}) == pytest.approx(8000)   # 2% x 40% x 1,000,000


def test_zero_income_is_missing_data_not_a_crash():
    with pytest.raises(MissingData):
        calc("debt_service_ratio_pct", {"total_monthly_debt_obligations_cents": 40, "monthly_gross_income_cents": 0})


def test_single_borrower_limit_needs_capital_funds():
    reg = {"single_borrower_exposure": {"limit_pct_of_capital_funds": 15, "capital_funds_cents": None}}
    with pytest.raises(NotConfigured):
        calc("within_single_borrower_limit", {"aggregate_exposure_cents": 1}, reg)


def test_has_formula():
    assert has_formula("ltv_pct") and not has_formula("identity_verified")


DOCS = {"required_documents_by_product": {"personal_loan": ["salary_slips", "bank_statements"], "gold_loan": []}}


def test_documents_complete_when_all_required_are_present():
    f = {"salary_slips": True, "bank_statements": True}
    assert calc("income_documents_complete", f, parameters=DOCS, product="personal_loan") is True


def test_documents_incomplete_when_one_is_false():
    f = {"salary_slips": True, "bank_statements": False}
    assert calc("income_verified", f, parameters=DOCS, product="personal_loan") is False


def test_documents_missing_flag_is_missing_data():
    with pytest.raises(MissingData):
        calc("income_documents_complete", {"salary_slips": True}, parameters=DOCS, product="personal_loan")


def test_product_with_no_required_documents_is_complete():
    assert calc("income_documents_complete", {}, parameters=DOCS, product="gold_loan") is True


def test_documents_not_configured_for_product():
    with pytest.raises(NotConfigured):
        calc("collateral_documents_complete", {}, parameters=DOCS, product="sme_loan")


SEC = {"security_field_by_product": {"vehicle_loan": "lien_registered", "vehicle_leasing": "lease_registered"}}


def test_security_uses_the_registration_the_product_needs():
    f = {"lien_registered": True, "lease_registered": False}
    assert calc("security_perfected", f, parameters=SEC, product="vehicle_loan") is True
    assert calc("security_perfected", f, parameters=SEC, product="vehicle_leasing") is False


BANDS = {"score_bands": {"A": 750, "B": 650, "C": 550, "D": 0}}


def test_score_band():
    band = lambda score: calc("score_band", {"internal_score": score}, parameters=BANDS)
    assert (band(800), band(750), band(700), band(560), band(10)) == ("A", "A", "B", "C", "D")


def test_score_band_not_configured():
    with pytest.raises(NotConfigured):
        calc("score_band", {"internal_score": 700}, parameters={"score_bands": {"A": None, "B": 650}})


MATRIX = {"authority_matrix": [
    {"level": "officer", "max_amount_cents": 1000000, "max_risk_grade": "B", "allows_exceptions": False},
    {"level": "manager", "max_amount_cents": 15000000, "max_risk_grade": "C", "allows_exceptions": True}]}


def test_within_authority():
    f = {"requested_amount_cents": 500000, "risk_grade": "A", "exception_flag": False}
    assert calc("within_authority", f, parameters=MATRIX) is True
    assert calc("within_authority", {**f, "requested_amount_cents": 5000000}, parameters=MATRIX) is True   # manager
    assert calc("within_authority", {**f, "requested_amount_cents": 20000000}, parameters=MATRIX) is False  # too big
    assert calc("within_authority", {**f, "risk_grade": "D"}, parameters=MATRIX) is False            # grade too weak
    assert calc("within_authority", {**f, "exception_flag": True}, parameters=MATRIX) is True        # manager allows
    only_officer = {"authority_matrix": MATRIX["authority_matrix"][:1]}
    assert calc("within_authority", {**f, "exception_flag": True}, parameters=only_officer) is False


RES = {"residency_mapping": [
    {"citizenship": "LK", "residency_status": "resident", "category": "sri_lankan_resident"},
    {"citizenship": "LK", "residency_status": "non_resident", "category": "permitted_non_resident"}],
    "residency_default": "other"}


def test_residency_category():
    cat = lambda status, cit: calc("residency_category", {"residency_status": status, "citizenship": cit}, parameters=RES)
    assert cat("resident", "LK") == "sri_lankan_resident"
    assert cat("non_resident", "lk") == "permitted_non_resident"
    assert cat("resident", "IN") == "other"