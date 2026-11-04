"""Writer agent: Gemini turns a question into a compiled spec (preferred) or validated SQL."""
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

from app.agent.llm import LLM, ToolSpec
from app.agent.validators import is_grounded, validate_sql
from app.compiler.spec import (CONTRACT_FORMS, CONTRACT_TYPES, DIMENSIONS, MEASURES, REGISTRY_ENTITIES, ROLES,
                               STATUS_SCOPES, STATUSES, TRADE_TYPES, Spec, SpecError, entity_values, parse_spec)
from app.compiler.sql import compile_spec
from app.db import Database, QueryError, QueryResult, to_jsonable
from app.examples import ExampleStore
from app.resolve import Resolver

SPLIT_AXES = {"status_scope", "measure", "currency", "product_scope", "role"}
S, I, B = {"type": "STRING"}, {"type": "INTEGER"}, {"type": "BOOLEAN"}


def _arr(item):
    return {"type": "ARRAY", "items": item}


def _enum(values):
    return {"type": "STRING", "enum": list(values)}


FILTERS_SCHEMA = {"type": "OBJECT", "properties": {
    "year": I, "month_from": I, "month_to": I,
    "date_from": {"type": "STRING", "description": "YYYY-MM-DD"},
    "date_to": {"type": "STRING", "description": "YYYY-MM-DD"},
    "products": _arr(S), "product_family": S, "regions": _arr(S),
    "buyer_inns": _arr(S), "seller_inns": _arr(S), "participant_inns": _arr(S),
    "seller_brokers": _arr(S), "buyer_brokers": _arr(S), "brokers": _arr(S),
    "contract_id": I, "contract_type": _enum(CONTRACT_TYPES), "contract_form": _enum(CONTRACT_FORMS),
    "trade_type": _enum(TRADE_TYPES), "statuses": _arr(_enum(STATUSES)),
}}

SPEC_SCHEMA = {"type": "OBJECT", "properties": {
    "kind": _enum(["deals_aggregate", "deal_lookup", "contract_lookup", "usd_rate", "registry_count"]),
    "measures": _arr(_enum(MEASURES)), "filters": FILTERS_SCHEMA, "group_by": _enum(DIMENSIONS), "top": I,
    "status_scope": _enum(STATUS_SCOPES),
    "deal_id": I, "contract_id": I, "date": {"type": "STRING", "description": "YYYY-MM-DD"},
    "year": I, "month": I,
    "entity": _enum(REGISTRY_ENTITIES), "role": _enum(ROLES), "vat_payer": B, "region": S, "registered_year": I,
}, "required": ["kind"]}

TOOLS = [
    ToolSpec("resolve", "Find exact IDs for a company, broker, product, region or contract named in the question.",
             {"type": "OBJECT", "properties": {
                 "kind": _enum(["company", "broker", "product", "region", "contract"]), "text": S},
              "required": ["kind", "text"]}),
    ToolSpec("get_examples", "Return verified example questions with their spec or SQL.",
             {"type": "OBJECT", "properties": {"question": S}, "required": ["question"]}),
    ToolSpec("compile_query", "PREFERRED. Run a structured query spec compiled by verified templates.",
             {"type": "OBJECT", "properties": {"spec": SPEC_SCHEMA, "assumptions": _arr(S)},
              "required": ["spec", "assumptions"]}),
    ToolSpec("submit_sql", "FALLBACK only when no spec can express the question. One DuckDB SELECT.",
             {"type": "OBJECT", "properties": {
                 "sql": S, "splits": _arr(_enum(["status_scope", "measure", "currency", "product_scope", "role"])),
                 "assumptions": _arr(S)}, "required": ["sql", "splits", "assumptions"]}),
    ToolSpec("explore_sql", "Read-only exploration query (max 50 rows), e.g. distinct units of a product.",
             {"type": "OBJECT", "properties": {"sql": S}, "required": ["sql"]}),
    ToolSpec("declare_unanswerable", "The database cannot answer this question.",
             {"type": "OBJECT", "properties": {"reason": S}, "required": ["reason"]}),
]

SYSTEM = """You are the query planner of a Q&A service over a commodity-exchange deal registry.
Questions come in Russian or Uzbek. You never answer from memory: you only produce a query.

Steps:
1. If the question needs data the database does not have (see Data coverage), or a period outside it,
   call declare_unanswerable. If compile_query returns an error telling you to clamp a period to the data range,
   do so and add the assumption — do not declare the question unanswerable.
2. For every company, broker, product, region or contract mentioned, call resolve(). Use only the IDs/values
   it returns. If resolve returns a product and its product_family with the same score, use the specific
   product unless the question is about the whole product group. For follow-up questions, re-run resolve()
   for entities from the previous question; IDs in the previous SQL are not pre-approved. If nothing relevant comes back, call declare_unanswerable. If several candidates are equally
   close, include all of them (max 5) and group by that entity so each is shown separately.
3. Call get_examples() once.
4. Prefer compile_query(spec). Use submit_sql only if no spec kind/measure/dimension can express the question.
5. Ambiguity: follow the Ambiguity registry. Compute the variants in ONE query: status_scope=split for counts
   and totals; two measures for "most/largest" without a measure; group_by=participant when the role is not
   stated. At most 2 split axes. Write the defaults you applied as short assumptions in the question's language.
6. Free SQL rules: DuckDB dialect; money via amount_uzs/amount_usd; never filter by names; derived numbers
   (percent, averages, differences) as SQL columns; include participant/broker names next to IDs.

{schema}
"""


_KIND = {"company": "inn"}


def _json(value):
    """Recursively make a value JSON-safe (dates -> ISO strings, Decimal -> float)."""
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return to_jsonable(value)


@dataclass
class WriterResult:
    status: Literal["ok", "unanswerable", "failed"]
    path: Literal["compiled", "sql"] | None = None
    sql: str | None = None
    result: QueryResult | None = None
    spec: Spec | None = None
    splits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    reason: str = ""
    trace: list[dict] = field(default_factory=list)


class Writer:
    def __init__(self, llm: LLM, db: Database, resolver: Resolver, examples: ExampleStore, schema_prompt: str,
                 max_tool_calls: int = 8, max_fixes: int = 2):
        self.llm, self.db, self.resolver, self.examples = llm, db, resolver, examples
        self.system = SYSTEM.format(schema=schema_prompt)
        self.max_tool_calls, self.max_fixes = max_tool_calls, max_fixes

    def run(self, question: str, lang: str, deadline: float, previous: dict | None = None) -> WriterResult:
        allowed: set[tuple[str, str]] = set()
        trace: list[dict] = []
        calls = fixes = 0
        nudged = False

        def failed(reason):
            return WriterResult("failed", reason=reason, trace=trace)

        def send(fn, *args):
            """-> (reply, None) or (None, failed-result); checks the deadline before every send."""
            if time.monotonic() > deadline:
                return None, failed("timeout")
            try:
                return fn(*args), None
            except Exception as e:  # LLMError or transport failure from the chat
                return None, failed(f"llm error: {e}")

        if time.monotonic() > deadline:
            return failed("timeout")
        try:
            chat = self.llm.start_chat(self.system, TOOLS)
        except Exception as e:
            return failed(f"llm error: {e}")
        reply, err = send(chat.send_text, self._prompt(question, lang, previous))
        while True:
            if err:
                return err
            if not reply.tool_calls:
                if nudged:
                    return failed("model did not call a tool")
                nudged = True
                reply, err = send(chat.send_text,
                                  "Do not answer in text. Call compile_query, submit_sql or declare_unanswerable.")
                continue
            results = []
            for tc in reply.tool_calls:
                calls += 1
                trace.append({"tool": tc.name, "args": tc.args})
                if tc.name == "declare_unanswerable":
                    return WriterResult("unanswerable", reason=str(tc.args.get("reason", "")), trace=trace)
                if calls > self.max_tool_calls:
                    return failed("tool budget exceeded")
                if tc.name in ("compile_query", "submit_sql"):
                    outcome = (self._compile(tc.args, allowed, question, deadline) if tc.name == "compile_query"
                               else self._sql(tc.args, allowed, question, deadline))
                    if isinstance(outcome, WriterResult):
                        outcome.trace = trace
                        return outcome
                    fixes += 1
                    trace[-1]["error"] = outcome
                    if fixes > self.max_fixes:
                        return failed(f"fix budget exceeded: {outcome}")
                    results.append((tc.name, {"error": outcome}))
                    continue
                try:
                    payload = self._tool(tc.name, tc.args, allowed, question, deadline)
                except (KeyError, ValueError, TypeError) as e:
                    payload = {"error": f"bad arguments: {e}"}
                results.append((tc.name, payload))
            reply, err = send(chat.send_tool_results, results)

    @staticmethod
    def _prompt(question, lang, previous):
        text = f"Question: {question}\nQuestion language: {lang}\n"
        if previous:
            text += f"Previous question: {previous.get('question', '')}\nPrevious SQL: {previous.get('sql', '')}\n"
        return text

    @staticmethod
    def _timeout(deadline, cap):
        return max(1.0, min(cap, deadline - time.monotonic()))

    def _compile(self, args, allowed, question, deadline) -> WriterResult | str:
        try:
            spec = parse_spec(args.get("spec") or {})
        except (SpecError, KeyError, ValueError, TypeError) as e:
            return str(e)
        missing = sorted(v for k, v in entity_values(spec) if not is_grounded(v, k, allowed, question))
        if missing:
            return f"values {missing} were not returned by resolve() in this request; call resolve() first"
        try:
            compiled = compile_spec(spec)
            result = self.db.query(compiled.sql, timeout_s=self._timeout(deadline, 10.0))
        except (SpecError, KeyError, ValueError, TypeError) as e:
            return f"could not compile spec: {e}"
        except QueryError as e:
            return f"compiled query failed: {e}"
        return WriterResult("ok", "compiled", compiled.sql, result, spec, compiled.splits, compiled.notes,
                            [str(a) for a in args.get("assumptions") or []])

    def _sql(self, args, allowed, question, deadline) -> WriterResult | str:
        v = validate_sql(str(args.get("sql", "")), allowed, question, self.db)
        if not v.ok:
            return "; ".join(v.errors)
        try:
            result = self.db.query(v.sql, timeout_s=self._timeout(deadline, 10.0))
        except QueryError as e:
            return f"query failed: {e}"
        splits = [str(x) for x in args.get("splits") or [] if str(x) in SPLIT_AXES][:2]
        return WriterResult("ok", "sql", v.sql, result, None, splits, [],
                            [str(a) for a in args.get("assumptions") or []])

    def _tool(self, name, args, allowed, question, deadline) -> dict:
        if name == "resolve":
            cands = self.resolver.resolve(args["kind"], str(args["text"]))
            allowed.update((_KIND.get(c.kind, c.kind), c.id) for c in cands)
            return {"candidates": [_json(asdict(c)) for c in cands]}
        if name == "get_examples":
            exs = self.examples.search(str(args.get("question") or question))
            return {"examples": [{"question": e.question, "spec": json.dumps(e.spec, ensure_ascii=False) if e.spec
                                  else None, "sql": e.sql, "action": e.action, "note": e.note} for e in exs]}
        if name == "explore_sql":
            v = validate_sql(str(args["sql"]), allowed, question, self.db, check_ids=False, row_limit=50)
            if not v.ok:
                return {"error": "; ".join(v.errors)}
            try:
                res = self.db.query(v.sql, timeout_s=self._timeout(deadline, 5.0), max_rows=50)
            except QueryError as e:
                return {"error": str(e)}
            return {"columns": res.columns, "rows": [[_json(c) for c in r] for r in res.rows]}
        return {"error": f"unknown tool {name}"}
