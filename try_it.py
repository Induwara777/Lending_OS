import sys
from pathlib import Path
from uuid import uuid4

from app.clientpack.loader import load_client_pack
from app.domain.models import DecisionRequest, Outcome, RuleStatus, OUTCOME_SEVERITY
from app.services.rules_engine import RulesEngine
from tests.test_sample import sample_customer

PACK_PATH = Path("client_packs/demo/decision.yaml")
HELP = """Commands:
  Enter / run          run the chosen rules on the current data
  set field=value      change a field (example: set identity_verified=false)
  del field            remove a field (the engine sees it as missing)
  show                 print the current data
  reset                back to the sample good customer
  all                  run and also list the rules that passed
  rules                choose other rules (same product)
  product              change product (shows the rule list again)
  help                 this text        q = quit"""


def parse(text):
    low = text.strip().lower()
    if low in ("true", "t", "yes", "y"):
        return True
    if low in ("false", "f", "no", "n"):
        return False
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text.strip()


def choose_product(products):
    print("\nProducts:")
    for i, p in enumerate(products, 1):
        print(f"  {i}. {p}")
    while True:
        answer = input("Select product (number or name, q = quit): ").strip()
        if answer.lower() in ("q", "quit", "exit"):
            raise SystemExit
        if answer.isdigit() and 1 <= int(answer) <= len(products):
            return products[int(answer) - 1]
        if answer in products:
            return answer
        print("  Not a valid product.")


def number_rules(engine, product):
    """Only the knock-out rules of the product, each with ONE unique number."""
    rules = [r for r in engine.select_rules(product, "before_scoring") if r["knock_out"]]
    return list(enumerate(rules, 1))


def choose_rules(numbered):
    print(f"\nKNOCK-OUT RULES (a failure gives DECLINE): {len(numbered)} rules")
    print("-" * 72)
    for n, r in numbered:
        print(f"  {n:>2}. {r['id']:<11} {r['name']}")
    by_number = {n: r["id"] for n, r in numbered}
    print("\nPress Enter to check ALL these rules, or type numbers to check only some (example: 3,7,12).")
    while True:
        answer = input("Rule numbers: ").strip()
        if answer.lower() in ("q", "quit", "exit"):
            raise SystemExit
        if answer == "" or answer.lower() == "all":
            return set(by_number.values())
        try:
            numbers = [int(x) for x in answer.split(",") if x.strip()]
        except ValueError:
            print("  Type numbers only, like 1,4,10")
            continue
        unknown = [n for n in numbers if n not in by_number]
        if unknown or not numbers:
            print(f"  No such rule number: {', '.join(map(str, unknown)) or '(none given)'}")
            continue
        return {by_number[n] for n in numbers}


def run(engine, product, data, chosen_ids, show_passed=False):
    res = engine.evaluate(DecisionRequest(application_id=uuid4(), product=product, features=data),
                          stage="before_scoring", rule_ids=chosen_ids)
    results = [("before_scoring", r) for r in res.rule_results]
    outcome, reasons = res.outcome, res.reasons

    def line(r):
        if r.status == RuleStatus.FAILED:
            print(f"  FAIL    {r.rule_id:<11} {r.name}")
            print(f"          -> {r.effect.value}, {r.reason_code}  ({r.cause.value}: {r.detail})")
        elif r.status == RuleStatus.SKIPPED:
            print(f"  SKIPPED {r.rule_id:<11} {r.name}  ({r.detail})")
        else:
            print(f"  PASS    {r.rule_id:<11} {r.name}")

    def count(group):
        return {s: sum(1 for r in group if r.status == s) for s in RuleStatus}

    print("\n" + "=" * 72)
    group = [r for _, r in results]
    c = count(group)
    print(f"KNOCK-OUT RULES CHECKED: {len(group)}  ({c[RuleStatus.PASSED]} passed, {c[RuleStatus.FAILED]} failed, {c[RuleStatus.SKIPPED]} skipped)")
    for r in group:
        if show_passed or r.status != RuleStatus.PASSED:
            line(r)
    print("-" * 72)
    if outcome:
        print(f"FINAL RESULT: {outcome.value}")
        print("Reason codes: " + ", ".join(f"{r.code} ({r.source})" for r in reasons))
    else:
        print("FINAL RESULT: NEXT STAGE (no tested rule failed)")
    print("=" * 72)


def start(engine, pack, product=None):
    """Product -> numbered rule list -> choose rules -> returns (product, data, numbered, chosen)."""
    product = product or choose_product(list(pack.products))
    numbered = number_rules(engine, product)
    chosen = choose_rules(numbered)
    return product, sample_customer(product), numbered, chosen


def main():
    pack = load_client_pack(PACK_PATH)              # stops with a clear message if the YAML is wrong
    engine = RulesEngine(pack)
    for w in pack.warnings:
        print("pack warning:", w)
    first = sys.argv[1] if len(sys.argv) > 1 else None
    if first and first not in pack.products:
        raise SystemExit(f"unknown product '{first}'. Choose from: {', '.join(pack.products)}")
    try:
        product, data, numbered, chosen = start(engine, pack, first)
        print(f"\nChecking {len(chosen)} rule(s) for {product}, using the sample good customer.")
        run(engine, product, data, chosen)
        while True:
            line = input("\n> ").strip()
            cmd = line.split(None, 1)[0].lower() if line else "run"
            arg = line.split(None, 1)[1] if " " in line else ""
            if cmd in ("q", "quit", "exit"):
                break
            elif cmd in ("run", "r"):
                run(engine, product, data, chosen)
            elif cmd == "all":
                run(engine, product, data, chosen, show_passed=True)
            elif cmd == "set" and "=" in arg:
                key, value = arg.split("=", 1)
                data[key.strip()] = parse(value)
                run(engine, product, data, chosen)
            elif cmd == "del" and arg:
                data.pop(arg.strip(), None)
                run(engine, product, data, chosen)
            elif cmd == "show":
                for k, v in data.items():
                    print(f"  {k} = {v}")
            elif cmd == "reset":
                data = sample_customer(product)
                run(engine, product, data, chosen)
            elif cmd == "rules":                     # pick other rules, same product
                chosen = choose_rules(numbered)
                run(engine, product, data, chosen)
            elif cmd == "product":                   # new product -> new list -> new check
                product, data, numbered, chosen = start(engine, pack)
                run(engine, product, data, chosen)
            else:
                print(HELP)
    except (EOFError, KeyboardInterrupt, SystemExit):
        pass
    print("\nBye.")


if __name__ == "__main__":
    main()