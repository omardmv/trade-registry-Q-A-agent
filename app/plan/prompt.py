"""System prompt and tool schema of the grammar planner (PLANNER=grammar)."""
import json
from pathlib import Path

import yaml

from app.agent.llm import ToolSpec
from app.plan.dictionary import DEFAULT_AGGS, Dictionary, Subject
from app.plan.model import OPS, SUBJECTS

S, I = {"type": "STRING"}, {"type": "INTEGER"}


def _arr(item):
    return {"type": "ARRAY", "items": item}


_FILTER = {"type": "OBJECT", "properties": {
    "field": S, "op": {"type": "STRING", "enum": list(OPS)}, "value": S, "values": _arr(S),
    "numerator": {"type": "BOOLEAN"}}, "required": ["field"]}
PLAN_SCHEMA = {"type": "OBJECT", "properties": {
    "subject": {"type": "STRING", "enum": list(SUBJECTS)},
    "measures": _arr({"type": "OBJECT", "properties": {"agg": S, "of": S}, "required": ["agg"]}),
    "filters": _arr(_FILTER),
    "group_by": _arr(S),
    "order": {"type": "STRING", "enum": ["desc", "asc"]},
    "limit": I,
    "compare": _arr({"type": "OBJECT", "properties": {"year": I, "quarter": I, "month_from": I, "month_to": I}}),
}, "required": ["subject"]}

TOOLS = [
    ToolSpec("resolve", "Exact ids/values of a named company, broker, product, grade/mark (product_variant), region "
                        "or contract.",
             {"type": "OBJECT", "properties": {
                 "kind": {"type": "STRING", "enum": ["company", "broker", "product", "product_variant", "region",
                                                     "contract"]}, "text": S}, "required": ["kind", "text"]}),
    ToolSpec("run_plan", "PREFERRED. Run a plan.",
             {"type": "OBJECT", "properties": {"plan": PLAN_SCHEMA, "assumptions": _arr(S)},
              "required": ["plan", "assumptions"]}),
    ToolSpec("submit_sql", "FALLBACK when no plan fits. One DuckDB SELECT.",
             {"type": "OBJECT", "properties": {"sql": S, "splits": _arr(S), "assumptions": _arr(S)},
              "required": ["sql", "splits", "assumptions"]}),
    ToolSpec("explore_sql", "Read-only exploration query (50 rows).",
             {"type": "OBJECT", "properties": {"sql": S}, "required": ["sql"]}),
    ToolSpec("declare_unanswerable", "The data cannot answer this.",
             {"type": "OBJECT", "properties": {"reason": S}, "required": ["reason"]}),
]

SYSTEM = """You plan queries for a Q&A service over a commodity-exchange registry. Questions are Russian or Uzbek.
Never answer from memory; call tools.

Steps: 1) resolve() every company, broker, product, region, contract or grade/mark named; use only returned
values. 2) run_plan(plan, assumptions); on an error fix the plan as told. 3) declare_unanswerable if the data lacks
it (profit, staff, forecasts, other exchanges) or a condition cannot be expressed: never drop a condition.
Assumptions: short, in the question's language.

# Plan
{{subject, measures:[{{agg, of}}], filters:[{{field, op, value}}], group_by:[≤2], order, limit, compare}}
- agg: count | sum avg median min max (number) | distinct (number of different X) | a named measure (no of) |
  share: {{agg:share, of?:number to sum (omit = by count)}}; mark the part's filters numerator:true.
- op: = in != between >= <=; in/between take values:[..]. Years, months (1-12), quarters (1-4) are fields.
- group_by: dimensions, time grains, or a record (deal, contract, company, broker) to list single records
  ("largest deal": group_by [deal], limit 1). Two fields + limit = top-N per first field.
- order desc (default) | asc (least, cheapest); limit for top-N. compare: two periods {{year, quarter | month_from,
  month_to}} (no time filters then); gives change %.
- * = value from resolve(); (a|b) = allowed values.

# Fields
{dictionary}

# Rules
- Deals: counts/totals without group_by show all deals and without annulled automatically; rankings and
  breakdowns exclude annulled. Only when the question names a status: filter cancelled=false (without annulled),
  cancelled=true, status, or cancelled in [true,false] (all deals in a ranking).
- Money in so'm: amount_uzs (USD deals at the deal-date rate); "в долларах/dollarda" amount_usd. Volume: tonnes;
  volume only with units (grouped by unit). "Most/largest" without a measure: two measures tonnes and amount_uzs.
- Price: price_per_kg (weighted: sum/volume); median/avg/min/max of one deal's price: deal_price_per_kg;
  "средняя цена" unspecified: price_per_kg and avg deal_price_per_kg.
- "торговал больше всего" with no role: group_by participant. Broker on any side: broker.
- product = a product; product_family = a whole group (Хлопок = сырец, масло, шрот, пряжа). Plain "хлопок/paxta" =
  Хлопок-сырец. "(спот)/(форвард)" in a name = contract_form. Grade/mark ("АИ-92", "М400", "1 сорт"): resolve
  product_variant, filter {{field:product_variant, value:<same text>}}.
- region = warehouse region; buyer_region/seller_region = company's region. category/industry only if named so.
- 2026 ends 30.10: compare it with another year over months 1-10 of both.

# Examples
{examples}
"""

# hints shown next to field names whose meaning is not obvious
_HINTS = {"deals.participant": "buyer or seller", "deals.broker": "either side", "deals.product_variant": "grade/mark",
          "deals.region": "warehouse", "contracts.region": "warehouse", "deals.lots": "lots bought",
          "deals.volume": "in units", "deals.deal_price_per_kg": "one deal", "deals.unit_price_uzs": "per unit",
          "deals.price_change_pct": "vs start price", "contracts.validity_days": "days valid",
          "contracts.active_on": "valid on date", "contracts.base_price": "contract currency"}


def _subject_lines(s: Subject, seen: dict) -> list[str]:
    by: dict[str, list[str]] = {}
    for f in s.fields.values():
        name = f.name + ("*" if f.ground in ("company", "broker", "product", "product_family", "product_variant",
                                             "region", "contract", "deal") else "")
        hint = _HINTS.get(f"{s.name}.{f.name}", "")
        if f.ground == "enum":
            hint = "|".join(f.values)
            if seen.get(f.name) == f.values:
                hint = "as deals"
            seen.setdefault(f.name, f.values)
        kind = "number (no sum)" if f.type == "number" and f.aggs != DEFAULT_AGGS["number"] else f.type
        by.setdefault(kind, []).append(f"{name}({hint})" if hint else name)
    rng = f"; {s.data_range[0]:%d.%m.%Y}–{s.data_range[1]:%d.%m.%Y}" if s.data_range else ""
    out = [f"## {s.name} (count={s.count_col}{rng})"]
    for t in ("record", "dimension", "number", "number (no sum)", "date", "bool", "grain"):
        if by.get(t):
            out.append(f"{t}: {', '.join(by[t])}")
    if s.measures:
        out.append("named: " + ", ".join(s.measures))
    return out


def render_dictionary(d: Dictionary) -> str:
    seen: dict = {}
    return "\n".join(line for s in d.subjects.values() for line in _subject_lines(s, seen))


def render_examples(examples) -> str:
    out = []
    for ex in examples:
        line = f"- {ex['q']}" + (f" [resolve {ex['resolve']}]" if ex.get("resolve") else "")
        out.append(line + "\n  " + json.dumps(ex["plan"], ensure_ascii=False, separators=(",", ":")))
    return "\n".join(out)


EXAMPLES_PATH = Path(__file__).resolve().parent / "examples.yaml"


def load_examples(path: Path = EXAMPLES_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_system(d: Dictionary, examples) -> str:
    return SYSTEM.format(dictionary=render_dictionary(d), examples=render_examples(examples))


def prompt_size(system: str) -> dict:
    tools = json.dumps([{"name": t.name, "description": t.description, "parameters": t.parameters} for t in TOOLS],
                       ensure_ascii=False, separators=(",", ":"))
    return {"system": len(system), "tools": len(tools), "total": len(system) + len(tools)}
