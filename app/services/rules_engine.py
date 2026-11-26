"""Rules engine: runs the credit-policy rules of the client pack on one snapshot.

No limit or rule is written here. Everything comes from decision.yaml.
Outcome of a run: DECLINE (a knock-out rule failed), REFER (only general rules
failed) or None (nothing failed, the application continues).
"""
from __future__ import annotations

from typing import Any

from app.services.derived_fields import MissingData, NotConfigured, calculate, has_formula, to_date
from app.domain.models import (
    OUTCOME_SEVERITY, DecisionRequest, EvaluationResult, FailureCause,
    Outcome, Reason, RuleResult, RuleStatus,
)

OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "in": lambda a, b: a in b,
}


# ------------------------------------------------------------------ engine
class RulesEngine:
    def __init__(self, pack):
        # accepts the raw YAML dict, or the ClientPack returned by loader.py
        if not isinstance(pack, dict):
            pack = {
                "rule_set_version": pack.rule_set_version, "products": list(pack.products),
                "on_rule_failure": pack.on_rule_failure, "credit_policy": list(pack.rules),
                "derived_fields": pack.derived_fields,
                "regulatory_parameters": pack.regulatory_parameters,
                "reason_texts": pack.reason_texts,
            }
        self.rule_set_version = str(pack["rule_set_version"])
        self.reason_texts = dict(pack.get("reason_texts") or {})
        self.products = list(pack["products"])
        self.on_failure = pack["on_rule_failure"]
        self.rules = [r for r in pack["credit_policy"] if r["enabled"]]
        self.regulatory = pack.get("regulatory_parameters") or {}
        self._by_id = {r["id"]: r for r in pack["credit_policy"]}

    # ---- selecting rules
    def get_rule(self, rule_id: str) -> dict:
        return self._by_id[rule_id]

    def select_rules(self, product: str, stage: str = "before_scoring") -> list[dict]:
        return [r for r in self.rules
                if r.get("stage", "before_scoring") == stage
                and ("all" in r["products"] or product in r["products"])]

    # ---- one question
    def _field(self, name, features, ctx):
        """A feature value; a supplied value wins, otherwise a derived formula."""
        if features.get(name) is not None:
            return features[name]
        if has_formula(name):
            return calculate(name, lambda n: self._field(n, features, ctx), ctx)
        raise MissingData(name)

    def _target(self, cmp_, ctx, parameters, features):
        if "value" in cmp_:
            return cmp_["value"]
        if "param" in cmp_:
            value = parameters.get(cmp_["param"])
            if isinstance(value, dict):                      # limit set per product
                value = value.get(ctx.get("product"), value.get("default"))
            if value is None:
                raise NotConfigured(f"limit '{cmp_['param']}' is not filled in")
            return value
        return self._resolve_ref(cmp_["ref"], features, ctx)

    def _resolve_ref(self, ref, features, ctx):
        name = ref.split(".")[1]
        get = lambda n: self._field(n, features, ctx)
        versions = self.regulatory[name]["versions"]
        on = to_date(get("application_date"))
        live = [v for v in versions if to_date(v["effective_from"]) <= on]
        if not live:
            raise MissingData(f"no {name} in force on {on}")
        version = max(live, key=lambda v: v["effective_from"])
        if name == "gold_ltv_cap":
            return version["cap_pct"]
        if name == "vehicle_ltv_cap":
            if get("vehicle_age_group") == "registered_over_1_year":
                cap = version.get("registered_over_1_year_cap_pct")
            else:
                cap = version["caps_pct"].get(get("vehicle_class"))
            if cap is None:
                raise MissingData("no vehicle LTV cap for this class / age group")
            return cap
        raise NotConfigured(f"no resolver for {ref}")

    def _compare(self, cmp_, features, parameters, ctx):
        """True / False, or raises MissingData / NotConfigured."""
        actual = self._field(cmp_["field"], features, ctx)
        target = self._target(cmp_, ctx, parameters, features)
        try:
            return bool(OPS[cmp_["op"]](actual, target))
        except TypeError:
            raise MissingData(f"{cmp_['field']} has an unusable value: {actual!r}")

    def _run_check(self, check, features, parameters, ctx):
        """Returns (passed, cause, missing field names). NotConfigured is allowed to escape."""
        group = "all" if "all" in check else "any" if "any" in check else None
        items = check[group] if group else [check]
        results, missing = [], []
        for item in items:
            try:
                results.append((self._compare(item, features, parameters, ctx), None))
            except MissingData as exc:
                results.append((False, FailureCause.DATA_MISSING))
                missing.append(str(exc))
        passed = all(r[0] for r in results) if group != "any" else any(r[0] for r in results)
        if passed:
            return True, None, []
        violated = any(r[1] is None for r in results)
        return False, FailureCause.VIOLATED if violated else FailureCause.DATA_MISSING, missing

    def evaluate_check(self, check, features, parameters=None, product=None) -> bool:
        """True when the check passes. Missing data counts as failed."""
        ctx = {"product": product, "regulatory": self.regulatory, "parameters": parameters or {}}
        return self._run_check(check, features, parameters or {}, ctx)[0]

    # ---- one rule / one application
    def evaluate_rule(self, rule, features, product) -> RuleResult:
        base = dict(rule_id=rule["id"], name=rule["name"], knock_out=rule["knock_out"])
        params = rule.get("parameters") or {}
        ctx = {"product": product, "regulatory": self.regulatory, "parameters": params}
        try:
            if "applies_when" in rule:
                try:
                    applies, _, _ = self._run_check(rule["applies_when"], features, params, ctx)
                except MissingData:
                    applies = False
                if not applies:
                    return RuleResult(**base, status=RuleStatus.SKIPPED, detail="does not apply")
            passed, cause, missing = self._run_check(rule["check"], features, params, ctx)
        except NotConfigured as exc:
            return RuleResult(**base, status=RuleStatus.SKIPPED, detail=f"not configured: {exc}")
        if passed:
            return RuleResult(**base, status=RuleStatus.PASSED)
        if cause == FailureCause.DATA_MISSING:
            # Missing data is never a policy breach: the case goes to an officer (REFER).
            effect = Outcome(self.on_failure.get("data_missing", "REFER"))
            code = self.on_failure.get("data_missing_reason_code", "DATA_MISSING")
            detail = "required data is missing: " + ", ".join(dict.fromkeys(missing))
        else:
            effect = Outcome(self.on_failure["knock_out_true" if rule["knock_out"] else "knock_out_false"])
            code, detail = rule["reason_code"], "rule was broken"
        return RuleResult(**base, status=RuleStatus.FAILED, cause=cause, effect=effect,
                          reason_code=code, detail=detail)

    def evaluate(self, request: DecisionRequest, stage: str = "before_scoring",
                 rule_ids: set[str] | None = None) -> EvaluationResult:
        """Run the rules of one stage. rule_ids limits the run to chosen rules (for testing)."""
        if request.product not in self.products:
            raise ValueError(f"unknown product '{request.product}'")
        results = [self.evaluate_rule(r, request.features, request.product)
                   for r in self.select_rules(request.product, stage)
                   if rule_ids is None or r["id"] in rule_ids]
        failed = [r for r in results if r.status == RuleStatus.FAILED]
        outcome = max((r.effect for r in failed), key=OUTCOME_SEVERITY.get, default=None)
        reasons, seen = [], set()
        for effect in (Outcome.DECLINE, Outcome.REFER):          # strongest reasons first
            for r in failed:
                if r.effect == effect and r.reason_code not in seen:
                    seen.add(r.reason_code)
                    reasons.append(Reason(code=r.reason_code, source=r.rule_id))
        return EvaluationResult(
            application_id=request.application_id, product=request.product,
            outcome=outcome, reasons=reasons, rule_results=results,
            rule_set_version=self.rule_set_version)