"""Load and validate the client pack (decision.yaml).

A broken pack must stop the service at startup with a clear message,
never fail in the middle of a decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in"}
VALID_FAILURE_OUTCOMES = {"DECLINE", "REFER"}
VALID_STAGES = {"before_scoring", "after_scoring"}
REQUIRED_TOP_LEVEL = (
    "pack_version", "rule_set_version", "outcomes", "products",
    "on_rule_failure", "credit_policy",
)
REQUIRED_RULE_KEYS = (
    "id", "name", "products", "inputs", "check", "parameters",
    "knock_out", "reason_code", "enabled",
)


class ClientPackError(ValueError):
    """The client pack is invalid. The message lists every problem found."""


@dataclass(frozen=True)
class ClientPack:
    pack_version: str
    rule_set_version: str
    products: tuple[str, ...]
    on_rule_failure: dict[str, str]
    rules: tuple[dict[str, Any], ...]
    derived_fields: dict[str, Any]
    regulatory_parameters: dict[str, Any]
    warnings: tuple[str, ...]
    reason_texts: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- checks
def _validate_comparison(rid, cmp_, where, parameters, regulatory, errors):
    """One question: {field, op, and exactly one of value / param / ref}.
    Returns the field name, or None if the comparison is unusable."""
    if not isinstance(cmp_, dict):
        errors.append(f"{rid}: {where} must be a mapping")
        return None
    field = cmp_.get("field")
    if not isinstance(field, str) or not field:
        errors.append(f"{rid}: {where} needs a 'field'")
        return None
    if cmp_.get("op") not in VALID_OPS:
        errors.append(f"{rid}: {where} op '{cmp_.get('op')}' is not one of {sorted(VALID_OPS)}")
    targets = [k for k in ("value", "param", "ref") if k in cmp_]
    if len(targets) != 1:
        errors.append(f"{rid}: {where} needs exactly one of value / param / ref (found {targets})")
    elif "param" in cmp_ and cmp_["param"] not in parameters:
        errors.append(f"{rid}: {where} param '{cmp_['param']}' is not defined in parameters")
    elif "ref" in cmp_:
        parts = str(cmp_["ref"]).split(".")
        if len(parts) < 2 or parts[0] != "regulatory_parameters" or parts[1] not in regulatory:
            errors.append(f"{rid}: {where} ref '{cmp_['ref']}' does not exist in regulatory_parameters")
    elif cmp_.get("op") == "in" and "value" in cmp_ and not isinstance(cmp_["value"], list):
        errors.append(f"{rid}: {where} op 'in' needs a list as value")
    return field


def _validate_check(rid, check, where, parameters, regulatory, errors):
    """A single question, or all: [...] / any: [...]. Returns the fields used."""
    if not isinstance(check, dict):
        errors.append(f"{rid}: {where} must be a mapping")
        return []
    groups = [k for k in ("all", "any") if k in check]
    if groups:
        if len(groups) > 1 or "field" in check:
            errors.append(f"{rid}: {where} must use either all, or any, or a single question")
            return []
        items = check[groups[0]]
        if not isinstance(items, list) or not items:
            errors.append(f"{rid}: {where}.{groups[0]} must be a non-empty list")
            return []
        fields = [
            _validate_comparison(rid, item, f"{where}.{groups[0]}[{i}]", parameters, regulatory, errors)
            for i, item in enumerate(items, 1)
        ]
        return [f for f in fields if f]
    field = _validate_comparison(rid, check, where, parameters, regulatory, errors)
    return [field] if field else []


def _param_names(check):
    if not isinstance(check, dict):
        return []
    items = check.get("all") or check.get("any") or [check]
    return [i["param"] for i in items if isinstance(i, dict) and "param" in i]


# ------------------------------------------------------------ whole pack
def _validate_rules(raw, products, derived, regulatory, errors, warnings):
    rules = raw["credit_policy"]
    if not isinstance(rules, list) or not rules:
        errors.append("credit_policy must be a non-empty list")
        return
    seen = set()
    for index, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            errors.append(f"credit_policy item {index} must be a mapping")
            continue
        rid = rule.get("id", f"<item {index}>")
        missing = [k for k in REQUIRED_RULE_KEYS if k not in rule]
        if missing:
            errors.append(f"{rid}: missing {', '.join(missing)}")
            continue
        if rid in seen:
            errors.append(f"{rid}: duplicate rule id")
        seen.add(rid)

        for flag in ("knock_out", "enabled"):
            if not isinstance(rule[flag], bool):
                errors.append(f"{rid}: {flag} must be true or false")
        if not isinstance(rule["reason_code"], str) or not rule["reason_code"].strip():
            errors.append(f"{rid}: reason_code must be a non-empty text")
        if rule.get("stage", "before_scoring") not in VALID_STAGES:
            errors.append(f"{rid}: stage must be one of {sorted(VALID_STAGES)}")

        scope = rule["products"]
        if scope != ["all"]:
            if not isinstance(scope, list) or not scope or "all" in scope:
                errors.append(f"{rid}: products must be [all] or a list of products")
            else:
                for p in scope:
                    if p not in products:
                        errors.append(f"{rid}: product '{p}' is not in the products list")

        inputs = rule["inputs"]
        if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
            errors.append(f"{rid}: inputs must be a list of field names")
            inputs = []
        parameters = rule["parameters"]
        if not isinstance(parameters, dict):
            errors.append(f"{rid}: parameters must be a mapping (use {{}} when empty)")
            parameters = {}

        fields = _validate_check(rid, rule["check"], "check", parameters, regulatory, errors)
        if "applies_when" in rule:
            fields += _validate_check(rid, rule["applies_when"], "applies_when", parameters, regulatory, errors)

        for field in dict.fromkeys(fields):
            if field in derived:
                for source in derived[field].get("from", []):
                    if "." not in source and source != "product" and source not in inputs:
                        warnings.append(f"{rid}: derived field '{field}' needs '{source}' but it is not in inputs")
            elif field not in inputs:
                errors.append(f"{rid}: uses field '{field}' but it is not in inputs and is not a derived field")

        if rule["enabled"]:
            for name in _param_names(rule["check"]):
                value = parameters.get(name)
                if value is None or (isinstance(value, dict) and any(v is None for v in value.values())):
                    warnings.append(f"{rid}: enabled but limit '{name}' is not filled in yet (null)")
                if isinstance(value, dict) and isinstance(scope, list):
                    wanted = products if scope == ["all"] else scope
                    for prod in wanted:
                        if prod not in value and "default" not in value:
                            warnings.append(f"{rid}: limit '{name}' has no value for product '{prod}'")
                    for prod in value:
                        if prod != "default" and prod not in products:
                            warnings.append(f"{rid}: limit '{name}' lists unknown product '{prod}'")


def load_client_pack(path: str | Path) -> ClientPack:
    path = Path(path)
    if not path.is_file():
        raise ClientPackError(f"client pack not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ClientPackError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ClientPackError(f"{path.name} must contain a mapping at the top level")
    missing = [k for k in REQUIRED_TOP_LEVEL if k not in raw]
    if missing:
        raise ClientPackError(f"{path.name} is missing top-level keys: {', '.join(missing)}")

    errors: list[str] = []
    warnings: list[str] = []

    products = raw["products"]
    if (not isinstance(products, list) or not products
            or not all(isinstance(p, str) and p for p in products)
            or len(set(products)) != len(products) or "all" in products):
        errors.append("products must be a list of unique product names (do not list 'all' here)")
        products = []

    failure = raw["on_rule_failure"]
    if not isinstance(failure, dict):
        errors.append("on_rule_failure must be a mapping")
        failure = {}
    else:
        for key in ("knock_out_true", "knock_out_false"):
            if failure.get(key) not in VALID_FAILURE_OUTCOMES:
                errors.append(f"on_rule_failure.{key} must be DECLINE or REFER")
        if "data_missing" in failure and failure["data_missing"] not in VALID_FAILURE_OUTCOMES:
            errors.append("on_rule_failure.data_missing must be DECLINE or REFER")
        code = failure.get("data_missing_reason_code", "DATA_MISSING")
        if not isinstance(code, str) or not code.strip():
            errors.append("on_rule_failure.data_missing_reason_code must be a non-empty text")

    derived = raw.get("derived_fields") or {}
    for name, spec in derived.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("from"), list):
            errors.append(f"derived_fields.{name} needs a 'from' list")
            derived[name] = {"from": []}
    regulatory = raw.get("regulatory_parameters") or {}

    texts = raw.get("reason_texts") or {}
    if not isinstance(texts, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip()
                                              for k, v in texts.items()):
        errors.append("reason_texts must map each reason code to a non-empty text")
        texts = {}

    _validate_rules(raw, products, derived, regulatory, errors, warnings)

    needed = {r["reason_code"] for r in raw["credit_policy"] if isinstance(r, dict) and "reason_code" in r}
    needed |= {failure.get("data_missing_reason_code", "DATA_MISSING"), "SCORING_UNAVAILABLE"}
    lacking = sorted(c for c in needed if isinstance(c, str) and c not in texts)
    if lacking:
        warnings.append("reason_texts has no text for: " + ", ".join(lacking))

    if errors:
        listing = "\n".join(f"  - {e}" for e in errors)
        raise ClientPackError(f"{path.name} has {len(errors)} problem(s):\n{listing}")

    return ClientPack(
        pack_version=str(raw["pack_version"]),
        rule_set_version=str(raw["rule_set_version"]),
        products=tuple(products),
        on_rule_failure=dict(failure),
        rules=tuple(raw["credit_policy"]),
        derived_fields=derived,
        regulatory_parameters=regulatory,
        warnings=tuple(warnings),
        reason_texts=dict(texts),
    )