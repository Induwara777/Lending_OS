"""Derived fields: values the engine calculates from the snapshot.

One place for every formula (DSR, FOIR, LTV, age, expected loss ...). A value that
Dev 3 already supplies in the snapshot is used as it is; the formula runs only when
the snapshot does not have it. To add a formula, add one entry to DERIVED.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any


class NotConfigured(Exception):
    """A limit the rule needs is still null in the client pack (rule is skipped)."""


class MissingData(Exception):
    """A field needed by a check is missing or unusable."""


# ------------------------------------------------------------- date helpers
def to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise MissingData(f"not a date: {value!r}")


def add_months(d: date, months: int) -> date:
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def whole_years(start: date, end: date) -> int:
    return end.year - start.year - ((end.month, end.day) < (start.month, start.day))


def whole_months(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day)


# ------------------------------------------------- derived fields (formulas)
# Each function gets `get(name)`, which returns a feature or raises MissingData.
def _ratio(num: str, den: str, scale: int = 1):
    def calc(get, ctx):
        d = get(den)
        if d == 0:
            raise MissingData(f"{den} is zero")
        return get(num) / d * scale
    return calc


# ---- fields that need the rule's own settings (ctx["parameters"]) --------------
def _param(ctx, name):
    value = (ctx.get("parameters") or {}).get(name)
    if value is None:
        raise NotConfigured(f"'{name}' is not filled in for this rule")
    return value


def _by_product(ctx, name):
    """A setting that may be written per product: {vehicle_loan: ..., gold_loan: ...}."""
    value = _param(ctx, name)
    if isinstance(value, dict):
        value = value.get(ctx.get("product"), value.get("default"))
        if value is None:
            raise NotConfigured(f"'{name}' has no value for product '{ctx.get('product')}'")
    return value


def _documents_present(get, ctx):
    """True when every document the product requires is present (and verified)."""
    return all(get(doc) is True for doc in _by_product(ctx, "required_documents_by_product"))


def _security_perfected(get, ctx):
    """The pack says which registration each product needs, e.g. vehicle_loan: lien_registered."""
    return get(_by_product(ctx, "security_field_by_product")) is True


def _score_band(get, ctx):
    """Bands are minimum scores, e.g. {A: 750, B: 650, C: 550, D: 0}. Best matching band wins."""
    bands = _param(ctx, "score_bands")
    if any(v is None for v in bands.values()):
        raise NotConfigured("score_bands has an empty value")
    score = get("internal_score")
    for band, minimum in sorted(bands.items(), key=lambda kv: -kv[1]):
        if score >= minimum:
            return band
    raise MissingData("internal_score is below every score band")


def _within_authority(get, ctx):
    """True when one approver level covers the amount and the risk grade (A best ... D worst).
    A case flagged as an exception needs a level with allows_exceptions: true."""
    matrix = _param(ctx, "authority_matrix")
    amount, grade, exception = get("requested_amount_cents"), get("risk_grade"), get("exception_flag")
    return any(
        amount <= level["max_amount_cents"] and grade <= level["max_risk_grade"]
        and (not exception or level.get("allows_exceptions", False))
        for level in matrix)


def _residency_category(get, ctx):
    """First matching line of the pack's residency_mapping wins; otherwise residency_default."""
    status, citizenship = str(get("residency_status")).lower(), str(get("citizenship")).lower()
    for line in _param(ctx, "residency_mapping"):
        if (str(line["residency_status"]).lower() == status
                and str(line["citizenship"]).lower() == citizenship):
            return line["category"]
    return (ctx.get("parameters") or {}).get("residency_default", "other")


DERIVED = {
    "age_years": lambda g, c: whole_years(to_date(g("date_of_birth")), to_date(g("application_date"))),
    "age_at_maturity": lambda g, c: whole_years(
        to_date(g("date_of_birth")),
        add_months(to_date(g("application_date")), int(g("loan_tenure_months")))),
    "months_in_employment_or_business": lambda g, c: whole_months(
        to_date(_first(g, "employment_start_date", "business_registration_date")),
        to_date(g("application_date"))),
    "debt_service_ratio_pct": _ratio("total_monthly_debt_obligations_cents", "monthly_gross_income_cents", 100),
    "foir_pct": _ratio("total_fixed_monthly_obligations_cents", "monthly_net_income_cents", 100),
    "net_disposable_income_cents": lambda g, c: (
        g("monthly_net_income_cents") - g("essential_living_expenses_cents") - g("existing_debt_obligations_cents")),
    "residual_income_after_instalment_cents": lambda g, c: (
        g("monthly_net_income_cents") - g("existing_obligations_cents")
        - g("proposed_instalment_cents") - g("living_expenses_cents")),
    # ASSUMPTION (confirm with product owner): balances + card limits + new facility;
    # existing_instalment_cents is monthly so it is not added to a balance.
    "aggregate_exposure_cents": lambda g, c: (
        g("existing_loan_balance_cents") + g("credit_card_limit_cents") + g("credit_facility_amount_cents")),
    "ltv_pct": _ratio("credit_facility_amount_cents", "collateral_value_cents", 100),
    "valuation_age_days": lambda g, c: (to_date(g("application_date")) - to_date(g("valuation_date"))).days,
    "insurance_expired": lambda g, c: to_date(g("insurance_expiry_date")) < to_date(g("application_date")),
    "expected_loss_cents": lambda g, c: g("pd_pct") / 100 * g("lgd_pct") / 100 * g("ead_cents"),   # scores are percent
    "within_single_borrower_limit": lambda g, c: _within_single_borrower(g, c),
    "income_documents_complete": _documents_present,
    "collateral_documents_complete": _documents_present,
    "income_verified": _documents_present,        # a document flag is true only when it is present and verified
    "security_perfected": _security_perfected,
    "score_band": _score_band,
    "within_authority": _within_authority,
    "residency_category": _residency_category,
}


def _first(get, *names):
    for name in names:
        try:
            return get(name)
        except MissingData:
            continue
    raise MissingData(f"none of {', '.join(names)} supplied")


def _within_single_borrower(get, ctx):
    p = ctx["regulatory"]["single_borrower_exposure"]
    if p.get("capital_funds_cents") is None:
        raise NotConfigured("capital_funds_cents is not filled in")
    return get("aggregate_exposure_cents") <= p["limit_pct_of_capital_funds"] / 100 * p["capital_funds_cents"]


def has_formula(name: str) -> bool:
    return name in DERIVED


def calculate(name: str, get, ctx: dict) -> Any:
    """Calculate one derived field. get(field) returns a feature or raises MissingData."""
    return DERIVED[name](get, ctx)