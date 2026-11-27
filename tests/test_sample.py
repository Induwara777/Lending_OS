"""One sample 'good customer' per product. Used by try_it.py and by a test.

With these values no rule fails, so the engine returns outcome None.
Change one field to see a DECLINE or REFER.
"""
from __future__ import annotations

BASE = {
    "application_date": "2026-11-25", "date_of_birth": "1990-05-10",
    "loan_tenure_months": 36, "repayment_frequency": "monthly",
    "residency_status": "resident", "citizenship": "LK",
    "employment_type": "salaried", "employment_start_date": "2020-01-15",
    "months_of_continuous_income": 24,
    "monthly_gross_income_cents": 25000000, "monthly_net_income_cents": 20000000,
    "total_monthly_debt_obligations_cents": 4000000, "total_fixed_monthly_obligations_cents": 4000000,
    "essential_living_expenses_cents": 5000000, "living_expenses_cents": 5000000,
    "existing_debt_obligations_cents": 4000000, "existing_obligations_cents": 4000000,
    "proposed_instalment_cents": 3000000,
    "existing_loan_balance_cents": 20000000, "credit_card_limit_cents": 10000000,
    "identity_verified": True, "refinances_existing_npl": False, "legal_restriction_flag": False,
    "crib_consent": True, "crib_consent_flag": True, "crib_report_id": "CRIB-1",
    "crib_report_available": True, "crib_status": "clear", "crib_arrears": 0,
    "worst_arrears_days": 0, "npl_flag": False, "facility_status": "performing",
    "has_guarantor": False, "exception_flag": False,
    "nic_or_passport": True, "address_proof": True, "photos": True,
    "sanctions_screening_result": "no_match", "fraud_indicator_verified": False,
    "salary_slips": True, "bank_statements": True, "employer_confirmation": True,
    "financial_statements": True,
    # collateral (ignored by products without collateral)
    "collateral_owner": "borrower", "ownership_documents": True,
    "valuation_date": "2026-11-01", "valuation_amount_cents": 600000000, "valuer_id": "VAL-001",
    "insurance_status": "comprehensive", "insurance_expiry_date": "2027-06-30",
    "vehicle_class": "motor_car_suv_van", "vehicle_age_group": "unregistered_or_under_1_year",
    "vehicle_registration": True, "title_deed": True, "valuation_report": True,
    "lien_registered": True, "lease_registered": True, "mortgage_registered": False,
    "pledge_recorded": True,
}

# Ready-made scores, only for tests that use the StubScorer. The real scorer calculates its own.
STUB_SCORES = {"pd_pct": 2.0, "lgd_pct": 40.0, "internal_score": 800, "risk_grade": "A", "model_version": "stub-0"}

PRODUCT_DATA = {
    "vehicle_loan": {"requested_amount_cents": 200000000, "credit_facility_amount_cents": 200000000,
                     "collateral_value_cents": 600000000, "loan_purpose": "vehicle_purchase",
                     "loan_tenure_months": 60},
    "vehicle_leasing": {"requested_amount_cents": 300000000, "credit_facility_amount_cents": 300000000,
                        "collateral_value_cents": 900000000, "loan_purpose": "vehicle_purchase",
                        "loan_tenure_months": 60},
    "gold_loan": {"requested_amount_cents": 30000000, "credit_facility_amount_cents": 30000000,
                  "collateral_value_cents": 60000000, "loan_purpose": "personal_use",
                  "loan_tenure_months": 6, "valuation_date": "2026-11-20"},
    "personal_loan": {"requested_amount_cents": 100000000, "credit_facility_amount_cents": 100000000,
                      "loan_purpose": "education"},
    "sme_loan": {"requested_amount_cents": 500000000, "credit_facility_amount_cents": 500000000,
                 "collateral_value_cents": 1200000000, "loan_purpose": "working_capital",
                 "mortgage_registered": True, "employment_type": "business_owner", "business_registration_date": "2018-03-01",
                 "loan_tenure_months": 60, "valuation_date": "2026-10-01"},
}


def sample_customer(product: str) -> dict:
    return {**BASE, **PRODUCT_DATA[product]}