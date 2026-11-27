from __future__ import annotations


# ------------------------------------------------------------------ basics
def ratio_pct(numerator: float, denominator: float) -> float:
    """numerator / denominator as a percentage."""
    return numerator / denominator * 100


def ltv_pct(loan_cents: float, collateral_value_cents: float) -> float:
    """Loan-to-value: loan amount against the collateral value."""
    return ratio_pct(loan_cents, collateral_value_cents)


def dsr_pct(monthly_obligations_cents: float, monthly_income_cents: float) -> float:
    """Debt service ratio: monthly debt payments against monthly income."""
    return ratio_pct(monthly_obligations_cents, monthly_income_cents)


def expected_loss(pd_pct: float, lgd_pct: float, ead_cents: float) -> float:
    """PD x LGD x EAD (scores are percent)."""
    return pd_pct / 100 * lgd_pct / 100 * ead_cents


def instalment(principal_cents: float, annual_rate_pct: float, months: int) -> float:
    """Monthly instalment of a reducing-balance loan (used later for the maximum approvable amount)."""
    if months <= 0:
        raise ValueError("months must be positive")
    monthly = annual_rate_pct / 100 / 12
    if monthly == 0:
        return principal_cents / months
    return principal_cents * monthly / (1 - (1 + monthly) ** -months)


# ---------------------------------------------------------- recovery and LGD
def present_value(amount: float, discount_rate_pct: float = 0.0, years: float = 0.0) -> float:
    """Amount received `years` after default, discounted to the default date: amount / (1 + r)^t."""
    return amount / (1 + discount_rate_pct / 100) ** years


def lgd_pct(ead: float, pv_net_recovery: float) -> float:
    """max(0, (EAD - net recovery) / EAD), as a percentage. Capped at 100."""
    return min(100.0, max(0.0, (ead - pv_net_recovery) / ead * 100))


def _net_recovery_pv(proceeds: float, costs: float, other_recoveries: float,
                     discount_rate_pct: float, years: float) -> float:
    """PV(proceeds) - PV(costs) + PV(other recoveries). One timing for all three amounts."""
    return (present_value(proceeds, discount_rate_pct, years)
            - present_value(costs, discount_rate_pct, years)
            + present_value(other_recoveries, discount_rate_pct, years))


# ---- vehicle loan -------------------------------------------------------------
def ead_vehicle_loan(outstanding_principal: float, accrued_interest: float, default_fees: float) -> float:
    return outstanding_principal + accrued_interest + default_fees


def lgd_vehicle_loan(ead: float, vehicle_sale_proceeds: float, repossession_and_sale_costs: float,
                     other_recoveries: float = 0.0, discount_rate_pct: float = 0.0, years: float = 0.0) -> float:
    net = _net_recovery_pv(vehicle_sale_proceeds, repossession_and_sale_costs, other_recoveries,
                           discount_rate_pct, years)
    return lgd_pct(ead, net)


# ---- vehicle leasing ----------------------------------------------------------
def ead_vehicle_leasing(unpaid_future_rentals: float, residual_value_obligation: float,
                        termination_charges: float) -> float:
    """Do not count the residual value twice if it is already inside the rentals."""
    return unpaid_future_rentals + residual_value_obligation + termination_charges


def lgd_vehicle_leasing(ead_lease: float, vehicle_remarketing_proceeds: float,
                        repossession_refurbishment_sale_costs: float, other_recoveries: float = 0.0,
                        discount_rate_pct: float = 0.0, years: float = 0.0) -> float:
    """Use only the part of the sale proceeds the lease contract gives the lessor."""
    net = _net_recovery_pv(vehicle_remarketing_proceeds, repossession_refurbishment_sale_costs,
                           other_recoveries, discount_rate_pct, years)
    return lgd_pct(ead_lease, net)


# ---- gold loan ----------------------------------------------------------------
def ead_gold_loan(outstanding_principal: float, accrued_interest: float, charges: float) -> float:
    return outstanding_principal + accrued_interest + charges


def gold_sale_proceeds(net_gold_weight_grams: float, expected_sale_price_per_gram: float) -> float:
    return net_gold_weight_grams * expected_sale_price_per_gram


def lgd_gold_loan(ead: float, gold_sale_proceeds_amount: float, assay_storage_auction_sale_costs: float,
                  other_recoveries: float = 0.0, discount_rate_pct: float = 0.0, years: float = 0.0) -> float:
    net = _net_recovery_pv(gold_sale_proceeds_amount, assay_storage_auction_sale_costs, other_recoveries,
                           discount_rate_pct, years)
    return lgd_pct(ead, net)


# ---- personal loan (unsecured) ------------------------------------------------
def ead_personal_loan(outstanding_principal: float, accrued_interest: float, late_fees: float,
                      expected_additional_drawdowns: float = 0.0) -> float:
    """Drawdowns are normally zero for a term loan; they matter for revolving credit."""
    return outstanding_principal + accrued_interest + late_fees + expected_additional_drawdowns


def _unsecured_net_recovery_pv(cash_collections: float, legal_recoveries: float, guarantee_recoveries: float,
                               collection_and_legal_costs: float, discount_rate_pct: float, years: float) -> float:
    return (present_value(cash_collections, discount_rate_pct, years)
            + present_value(legal_recoveries, discount_rate_pct, years)
            + present_value(guarantee_recoveries, discount_rate_pct, years)
            - present_value(collection_and_legal_costs, discount_rate_pct, years))


def lgd_personal_loan(ead: float, cash_collections: float, legal_recoveries: float,
                      guarantee_recoveries: float, collection_and_legal_costs: float,
                      discount_rate_pct: float = 0.0, years: float = 0.0) -> float:
    net = _unsecured_net_recovery_pv(cash_collections, legal_recoveries, guarantee_recoveries,
                                     collection_and_legal_costs, discount_rate_pct, years)
    return lgd_pct(ead, net)


# ---- SME loan (secured part + unsecured part) ---------------------------------
def ead_sme_loan(drawn_principal: float, accrued_interest: float, fees: float,
                 expected_additional_drawdown: float = 0.0) -> float:
    """Expected drawdown applies to committed facilities."""
    return drawn_principal + accrued_interest + fees + expected_additional_drawdown


def lgd_sme_secured(ead_secured: float, collateral_sale_proceeds: float, enforcement_costs: float,
                    guarantee_recoveries: float = 0.0, discount_rate_pct: float = 0.0, years: float = 0.0) -> float:
    net = _net_recovery_pv(collateral_sale_proceeds, enforcement_costs, guarantee_recoveries,
                           discount_rate_pct, years)
    return lgd_pct(ead_secured, net)


def lgd_sme_unsecured(ead_unsecured: float, cash_collections: float, legal_recoveries: float,
                      guarantee_recoveries: float, collection_and_legal_costs: float,
                      discount_rate_pct: float = 0.0, years: float = 0.0) -> float:
    net = _unsecured_net_recovery_pv(cash_collections, legal_recoveries, guarantee_recoveries,
                                     collection_and_legal_costs, discount_rate_pct, years)
    return lgd_pct(ead_unsecured, net)


def lgd_sme_loan(ead_secured: float, lgd_secured_pct: float, ead_unsecured: float, lgd_unsecured_pct: float) -> float:
    """Exposure-weighted average of the secured and unsecured LGD."""
    total = ead_secured + ead_unsecured
    return (lgd_secured_pct * ead_secured + lgd_unsecured_pct * ead_unsecured) / total


# ---- baseline (what the demo scorecard uses today) ----------------------------
def lgd_baseline_secured(loan_cents: float, collateral_value_cents: float, haircut_pct: float,
                         floor_pct: float) -> float:
    """100 x (1 - collateral x (1 - haircut) / loan), kept between the floor and 100.
    The haircut stands in for sale costs and price drop, so no cost or timing inputs are needed."""
    recoverable = collateral_value_cents * (1 - haircut_pct / 100)
    return max(float(floor_pct), min(100.0, 100 * (1 - recoverable / loan_cents)))