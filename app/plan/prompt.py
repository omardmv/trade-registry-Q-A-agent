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
    "compare": _arr({"type": "OBJECT", "properties": {"year": I, "quarter": I, "month_from": I, "month_to": I,
                                                       "day": I}}),
    "mentions": _arr({"type": "OBJECT", "properties": {"text": S, "used_as": S}, "required": ["text", "used_as"]}),
}, "required": ["subject", "mentions"]}
RESOLVE_KINDS = ["company", "broker", "product", "product_variant", "region", "contract", "catalog"]

TOOLS = [
    ToolSpec("resolve", "Exact ids/values of a named company, broker, product, grade/mark (product_variant), region, "
                        "contract, status or catalog value (category, activity, unit, ...).",
             {"type": "OBJECT", "properties": {"kind": {"type": "STRING", "enum": RESOLVE_KINDS}, "text": S},
              "required": ["kind", "text"]}),
    ToolSpec("run_plan", "PREFERRED. Run a plan.",
             {**PLAN_SCHEMA, "properties": {**PLAN_SCHEMA["properties"], "assumptions": _arr(S)}}),
    ToolSpec("submit_sql", "FALLBACK when no plan fits. One DuckDB SELECT.",
             {"type": "OBJECT", "properties": {"sql": S, "splits": _arr(S), "assumptions": _arr(S)},
              "required": ["sql", "splits", "assumptions"]}),
    ToolSpec("explore_sql", "Read-only query (50 rows).",
             {"type": "OBJECT", "properties": {"sql": S}, "required": ["sql"]}),
    ToolSpec("declare_unanswerable", "The data cannot answer this.",
             {"type": "OBJECT", "properties": {"reason": S}, "required": ["reason"]}),
]

SYSTEM = """You plan queries over a commodity-exchange registry (Russian/Uzbek questions). Never answer from memory.
1) resolve() each company, broker, product, region, contract, grade/mark, status or catalog value named (catalog
values are not listed here); use only returned values. 2) run_plan with the plan fields and assumptions; on an
error fix the plan as told. 3) declare_unanswerable if the data lacks it (profit, staff, forecasts, other exchanges) or a
condition cannot be expressed: never drop a condition. Assumptions: short, in the question's language.

# Plan
{{subject, measures:[{{agg, of}}], filters:[{{field, op, value}}], group_by:[≤2], order, limit, compare, mentions}}
- agg: count | sum avg median min max (number) | distinct (number of different X) | a named measure (no of) |
  share {{agg:share, of?:number to sum (omit = count)}}; mark the part's filters numerator:true.
- op: = in != between >= <=; in/between take values:[..]. year, month (1-12), quarter (1-4) are fields.
- group_by: dimensions, time grains, or a record (deal, contract, company, broker) to list records ("largest
  deal": group_by [deal], limit 1). Two fields + limit = top-N per first field.
- order desc | asc (least); limit = top-N. compare: two periods {{year, quarter | month_from, month_to}}
  or two days {{year, month_from, day}}, no time filters then; gives change %.
- mentions: each phrase of the question you used, copied exactly, and what it sets: filter:<field> |
  group_by:<field> | measure:<i from 0> | order | limit | compare. Every filter and group_by needs one; every
  number, date, id or catalog word of the question must be in one.
- * = value from resolve().

# Fields
{dictionary}

# Rules
- Deals: counts/totals without group_by show all and without annulled automatically; rankings exclude annulled.
  Only if the question names a status: cancelled=false (without annulled), cancelled=true, status.
- Money in so'm: amount_uzs; "в долларах/dollarda" amount_usd. Volume: tonnes; volume = in units (by unit).
  "Most/largest" without a measure: measures tonnes and amount_uzs.
- Price: price_per_kg (sum/volume); avg/median/min/max of one deal's price: deal_price_per_kg; "средняя цена"
  unspecified: price_per_kg and avg deal_price_per_kg.
- "торговал больше всего", no role: group_by participant. Broker on any side: broker; seller vs buyer side of one
  broker: filter broker, group_by broker_side.
- product_family = a whole group (Хлопок = сырец, масло, шрот, пряжа); plain "хлопок/paxta" = Хлопок-сырец.
  "(спот)/(форвард)" in a name = contract_form. Grade/mark ("АИ-92", "1 сорт"): resolve product_variant, filter
  product_variant = <same text>.
- buyer_region/seller_region = company's region.

# Examples
{examples}
"""

# hints shown next to field names whose meaning is not obvious
_HINTS = {"deals.participant": "buyer or seller", "deals.product_variant": "grade/mark",
          "deals.region": "warehouse", "contracts.region": "warehouse", "deals.lots": "lots bought",
          "deals.volume": "in units", "deals.deal_price_per_kg": "one deal", "deals.unit_price_uzs": "per unit",
          "deals.price_change_pct": "vs start price", "contracts.validity_days": "days valid",
          "contracts.active_on": "valid on date", "contracts.base_price": "contract currency"}


# registry counters (all states, annulled included): a deals plan counts deals correctly, so they stay out of the prompt
_HIDDEN = {"companies.deals_as_buyer", "companies.deals_as_seller", "brokers.deals_as_seller_broker",
           "brokers.deals_as_buyer_broker"}


_RECORD_COLS_SHOWN = 3
# record columns named first in the prompt (what lookups ask for); the rest is "…"
_RECORD_COLS_FIRST = ("broker_code", "inn", "region", "name", "broker_name", "seller_inn", "buyer_inn", "amount_uzs",
                      "status", "product", "deal_date", "registered_date", "base_price")


def _record_cols(f) -> list[str]:
    """Columns a record listing shows, the ones lookups ask for first (names next to ids, counters left out)."""
    cols = [c.rsplit(" AS ", 1)[-1].strip() for c in f.columns]
    cols = [c for c in cols if not (c.endswith(("_name", "_valid")) or c.startswith("deals_")) or c == "broker_name"]
    first = [c for c in _RECORD_COLS_FIRST if c in cols]
    return first + [c for c in cols if c not in first]


def _subject_lines(s: Subject, seen: dict) -> list[str]:
    by: dict[str, list[str]] = {}
    for f in s.fields.values():
        if f"{s.name}.{f.name}" in _HIDDEN:
            continue
        name = f.name + ("*" if f.ground in ("company", "broker", "product", "product_family", "product_variant",
                                             "region", "contract", "deal") else "")
        hint = _HINTS.get(f"{s.name}.{f.name}", "")
        if f.ground == "enum" and len(f.values) <= 3 and seen.get(f.name) != f.values:
            hint = "|".join(f.values)       # longer value lists: resolve(kind=catalog) or the validator's error
            seen.setdefault(f.name, f.values)
        if f.type == "record" and f.ground in ("company", "broker"):     # «ИНН / STIR, регион» of a party
            cols = _record_cols(f)
            hint = "lists " + " ".join(cols[:_RECORD_COLS_SHOWN]) + (" …" if len(cols) > _RECORD_COLS_SHOWN else "")
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


def example_plan(ex: dict) -> dict:
    """The run_plan argument of an example: its plan with the mentions as {text, used_as} objects."""
    plan = dict(ex["plan"])
    if ex.get("mentions"):
        plan["mentions"] = [{"text": str(t), "used_as": u} for t, u in ex["mentions"]]
    return plan


def render_examples(examples) -> str:
    out = []
    for ex in examples:
        line = f"- {ex['q']}" + (f" [resolve {ex['resolve']}]" if ex.get("resolve") else "")
        out.append(line + "\n  " + json.dumps(example_plan(ex), ensure_ascii=False, separators=(",", ":")))
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
