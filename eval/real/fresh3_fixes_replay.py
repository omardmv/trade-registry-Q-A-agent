"""Offline replay of fresh3 plans (rebuilt from the SQL in eval/results/real-fresh3_intents-*.json) through the
current checks, defaults, SQL builder and templates; prints the gold rows next to the new answer. No LLM calls.

    uv run python -m eval.real.fresh3_fixes_replay
"""
import yaml
from app import config
from app.db import Database
from app.resolve import Resolver
from app.answer import plan_templates
from app.plan.build import build_sql
from app.plan.checks import consistency_error
from app.plan.explain import explain_plan
from tests.plan_helpers import D, prepare

db = Database(config.DB_PATH); res = Resolver(db)
Q = {i["id"]: i for i in yaml.safe_load(open("eval/real/fresh3_intents.yaml"))}
def F(f, v, op="="): return {"field": f, "op": op, "value": v}
LANG = {"ru": "ru", "uz_latn": "uz", "uz_cyrl": "uz-Cyrl"}
CASES = [
 ("h041", {"subject": "usd_rates", "measures": [{"agg": "min", "of": "rate"}], "filters": [F("year", 2025)], "group_by": ["day"], "limit": 1, "order": "desc"}),
 ("h002", {"subject": "deals", "filters": [F("deal", "237654")], "group_by": ["deal"]}),
 ("h006", {"subject": "deals", "measures": [{"agg": "share"}], "filters": [F("year", 2025), F("cancelled", True), {**F("payment_late", True), "numerator": True}]}),
 ("h010", {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024)], "group_by": ["contract_form"]}),
 ("h011", {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024)], "group_by": ["day"], "limit": 1}),
 ("h028", {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("seller", "995233269"), {"field": "year", "op": "between", "values": [2022, 2026]}], "group_by": ["year"]}),
 ("h029", {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("seller", "915688255"), {"field": "year", "op": "in", "values": [2024, 2025]}], "group_by": ["year"]}),
]
for ident, plan in CASES:
    item = Q[ident]
    print(f"=== {ident}: {item['intent']}")
    print("gold:", db.query(item["readings"][0]["sql"]).rows[:6])
    for lang, q in item["questions"].items():
        p = prepare(plan, q)
        err = consistency_error(p, D, q)
        if err:
            print(f"  {lang}: FIX MESSAGE: {err[:160]}")
            continue
        built = build_sql(p, D, res.variant_names)
        r = db.query(built.sql)
        text = plan_templates.render(p, D, r.columns, r.rows, LANG[lang])
        print(f"  {lang}: order={p.order} scope={p.scope}\n    " + (text or "").replace("\n", "\n    ")[:700])
        print("    " + explain_plan(p, D, built.notes, LANG[lang])[:300])
