"""Writer agent: Gemini turns a question into a compiled spec (preferred) or validated SQL."""
import json
import re
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Literal

from app import config
from app.agent.guard import build_index, missing_condition_error, sql_missing_condition_error
from app.agent.llm import LLM, EmptyReplyError, ToolSpec, call_with_retry, deadline_scope, safe_text
from app.agent.validators import grounding_status, missing_message, validate_sql
from app.catalog import load_catalog, load_product_attrs, render_catalog
from app.compiler.spec import (CONTRACT_FORMS, CONTRACT_TYPES, DIMENSIONS, FILTER_KEYS, KINDS, MEASURES, ORDERS,
                               PERIOD_KEYS, REGISTRY_ENTITIES, ROLES, SHARE_BASES, SHARE_FIELDS, STATUS_SCOPES, STATUSES,
                               TRADE_TYPES, USD_EXTREMES, DealsAggregate, Spec, SpecError, UsdRate, check_catalog,
                               entity_values, parse_spec)
from app.compiler.sql import SCOPE_FREE, compile_spec
from app.db import Database, QueryError, QueryResult, to_jsonable
from app.examples import ExampleStore
from app.resolve import Resolver

VARIANT_SAMPLES = 15
SPLIT_AXES = {"status_scope", "measure", "currency", "product_scope", "role", "price_average"}
S, I, B = {"type": "STRING"}, {"type": "INTEGER"}, {"type": "BOOLEAN"}


def _arr(item):
    return {"type": "ARRAY", "items": item}


def _enum(values):
    return {"type": "STRING", "enum": list(values)}


_DATE = {"type": "STRING", "description": "YYYY-MM-DD"}
_FILTER_TYPES = {"year": I, "month_from": I, "month_to": I, "date_from": _DATE, "date_to": _DATE,
                 "product_family": S, "contract_id": I, "contract_type": _enum(CONTRACT_TYPES),
                 "contract_form": _enum(CONTRACT_FORMS), "trade_type": _enum(TRADE_TYPES),
                 "statuses": _arr(_enum(STATUSES)),
                 "product_variant_query": {"type": "STRING", "description":
                                           "the exact text passed to resolve(kind=product_variant); expanded to "
                                           "every matching registry spelling"}}
# every other filter is a list of strings
FILTERS_SCHEMA = {"type": "OBJECT", "properties": {k: _FILTER_TYPES.get(k, _arr(S)) for k in FILTER_KEYS}}

SPEC_SCHEMA = {"type": "OBJECT", "properties": {
    "kind": _enum(KINDS),
    "measures": _arr(_enum(MEASURES)), "filters": FILTERS_SCHEMA, "group_by": _enum(DIMENSIONS), "top": I,
    "status_scope": _enum(STATUS_SCOPES),
    "order": {**_enum(ORDERS), "description": "ranking direction; asc = least / cheapest / fewest"},
    "share_of": {"type": "OBJECT", "description": "condition for share_pct (numerator)", "properties": {
        "field": _enum(SHARE_FIELDS), "value": S, "values": _arr(S),
        "basis": {**_enum(SHARE_BASES), "description": "deals (default) | amount_uzs | volume_t"}}},
    "compare_periods": {**_arr({"type": "OBJECT", "properties": {k: I for k in PERIOD_KEYS}}),
                        "description": "exactly two periods to compare (ungrouped deals_aggregate)"},
    "deal_id": I, "contract_id": I, "inn": S, "broker_code": S, "date": _DATE,
    "year": I, "month": I, "extreme": _enum(USD_EXTREMES),
    "entity": _enum(REGISTRY_ENTITIES), "role": _enum(ROLES), "vat_payer": B, "region": S, "registered_year": I,
    "activity": S, "products": _arr(S), "contract_form": _enum(CONTRACT_FORMS), "seller_inn": S, "active_on": _DATE,
}, "required": ["kind"]}

TOOLS = [
    ToolSpec("resolve", "Find exact IDs for a company, broker, product, region or contract named in the question. "
                        "product_variant (a specific grade/mark/spelling) returns how many raw registry spellings "
                        "match plus samples; then put the same text into filters.product_variant_query.",
             {"type": "OBJECT", "properties": {
                 "kind": _enum(["company", "broker", "product", "product_variant", "region", "contract"]),
                 "text": S},
              "required": ["kind", "text"]}),
    ToolSpec("get_examples", "Optional: all verified examples are already listed in your instructions.",
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
   For a specific spelling, grade or mark of a product ("сахар белый", "АИ-92", "М00К") call
   resolve(kind=product_variant, text=<phrase>) and put the same phrase into filters.product_variant_query (the
   compiler counts every matching spelling; never list spellings, never filter product names in SQL);
   "(спот)/(форвард)/(фьючерс)" in a product name is contract_form, not a variant. Enum-like values (category, activity, unit, ...) come from the Value catalog.
3. Find the closest of the verified Examples below (they cover Russian and Uzbek phrasings) and follow its
   spec shape; adapt values, periods and filters to the question.
4. Prefer compile_query(spec). Use submit_sql only if no spec kind/measure/dimension can express the question.
5. Ambiguity: follow the Ambiguity registry. Compute the variants in ONE query: status_scope=split for counts
   and totals; two measures for "most/largest" without a measure; group_by=participant when the role is not
   stated. At most 2 split axes. Write the defaults you applied as short assumptions in the question's language.
6. If the question has a condition the form cannot express, call declare_unanswerable — never drop a condition.
   Answering a broader question with real numbers is worse than no answer.
7. Free SQL rules: Put text labels only in the top-level SELECT; for buyer/seller splits use compile_query
   with group_by=participant. DuckDB dialect; money via amount_uzs/amount_usd; never filter by names; derived numbers
   (percent, averages, differences) as SQL columns; include participant/broker names next to IDs.

{schema}

# Examples (verified question -> query)
{examples}
"""


_KIND = {"company": "inn"}

_UZ_SUFFIX = r"(?:да|дан|га|нинг|гача|da|dan|ga|ning|gacha)?"
_MONTH_RE = re.compile(
    r"(?<!\w)(?:январ|феврал|апрел|июн|июл|август|сентябр|октябр|ноябр|декабр"
    r"|yanvar|fevral|aprel|iyun|iyul|avgust|sentyabr|oktyabr|noyabr|dekabr"
    r"|chorak|чорак|квартал"
    rf"|март(?:а|е|у|ом|ы)?{_UZ_SUFFIX}(?!\w)|ма[йяе]{_UZ_SUFFIX}(?!\w)"
    rf"|mart{_UZ_SUFFIX}(?!\w)|may{_UZ_SUFFIX}(?!\w)|май{_UZ_SUFFIX}(?!\w))", re.IGNORECASE)
_DATE_RE = re.compile(r"(?<![\d.])(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})(?![\d])")


_STATUS_WORDS = ("аннулир", "отмен", "состоявш", "исполнен", "оплач", "подписан", "действительн", "валидн",
                 "все сделки", "всех сделок", "bekor", "amalga osh", "to‘lan", "to'lan", "to’lan", "imzolan",
                 "barcha bitim")


def enforce_status_default(question: str, spec: Spec) -> Spec:
    """Ungrouped deal counts/totals default to the split view unless the question names a status."""
    if not isinstance(spec, DealsAggregate) or spec.group_by or all(m in SCOPE_FREE for m in spec.measures):
        return spec
    q = question.lower()
    if any(w in q for w in _STATUS_WORDS):
        return spec
    return replace(spec, status_scope="split")


def period_error(question: str, spec: Spec) -> str | None:
    """Fix message when the question names a month/quarter/date that the spec does not filter on."""
    if isinstance(spec, DealsAggregate):
        f = spec.filters
        has_month, has_date = f.month_from is not None or f.date_from is not None, \
            f.date_from is not None or f.date_to is not None
        has_month = has_month or any(p.month_from is not None for p in spec.compare_periods)
    elif isinstance(spec, UsdRate):
        has_month, has_date = spec.month is not None or spec.date is not None, spec.date is not None
    else:
        return None
    m = _DATE_RE.search(question)
    if m and not has_date:
        return (f"the question mentions the date {m.group(1)}; use filters.date_from/date_to "
                "(or date for usd_rate)")
    if _MONTH_RE.search(question) and not has_month:
        return ("the question mentions a month/quarter; add it (filters.month_from/month_to with year, "
                "or month for usd_rate)")
    return None


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
    resolved_names: list[str] = field(default_factory=list)  # display names/values returned by resolve()
    measure_columns: list[str] = field(default_factory=list)  # grammar planner: output columns holding measures


def _with_note(results: list[tuple[str, dict]], note: str) -> list[tuple[str, dict]]:
    """Tool results with a note added to the first payload (copies; the originals are in the trace)."""
    out = [(name, dict(payload)) for name, payload in results]
    if out:
        name, payload = out[0]
        payload["note"] = f"{payload['note']} {note}" if payload.get("note") else note
    return out


class Writer:
    tools = TOOLS
    compile_tool = "compile_query"
    nudge = "Do not answer in text. Call compile_query, submit_sql or declare_unanswerable."
    empty_nudge = "Your last reply was empty. Reply with one tool call."

    def __init__(self, llm: LLM, db: Database, resolver: Resolver, examples: ExampleStore, schema_prompt: str,
                 max_tool_calls: int = 8, max_fixes: int = 2,
                 llm_timeout_s: float = config.LLM_TIMEOUT_S, catalog: dict[str, set[str]] | None = None):
        self.llm, self.db, self.resolver, self.examples = llm, db, resolver, examples
        if catalog is None:
            catalog = load_catalog(db) if db is not None else {}
        self.catalog = catalog
        self._guard_index = build_index(catalog)
        self._product_attrs = load_product_attrs(db) if db is not None else {}
        self.system = SYSTEM.format(schema=schema_prompt + ("\n\n" + render_catalog(catalog) if catalog else ""),
                                     examples=examples.render_all() if examples is not None else "")
        self.max_tool_calls, self.max_fixes = max_tool_calls, max_fixes
        self.llm_timeout_s = llm_timeout_s
        self._exists_cache: dict[tuple[str, str], bool] = {}

    def _exists(self, kind: str, value: str) -> bool:
        if kind in ("inn", "broker"):  # in-memory dicts, nothing to cache
            return self.resolver.exists(kind, str(value))
        key = (kind, str(value))
        if key not in self._exists_cache:
            if len(self._exists_cache) >= 1024:
                self._exists_cache.pop(next(iter(self._exists_cache)))
            self._exists_cache[key] = self.resolver.exists(kind, str(value))
        return self._exists_cache[key]

    def run(self, question: str, lang: str, deadline: float, previous: dict | None = None) -> WriterResult:
        with deadline_scope(deadline):  # a provider fallback inside a send must fit before this deadline
            return self._run(question, lang, deadline, previous)

    def _run(self, question: str, lang: str, deadline: float, previous: dict | None) -> WriterResult:
        allowed: set[tuple[str, str]] = set()
        names: list[str] = []
        # per-question state: the first guard error, if any
        state: dict = {"guard": None}
        trace: list[dict] = []
        calls = fixes = 0
        nudged = False

        def failed(reason):
            return WriterResult("failed", reason=reason, trace=trace)

        def send_once(fn, arg):
            """-> (reply, None) or (None, failed-result); checks the deadline before every send.
            reply is None with no failure when the model replied with neither text nor tool calls."""
            if deadline - time.monotonic() < self.llm_timeout_s:
                return None, failed("timeout")
            try:
                reply = call_with_retry(lambda: fn(arg), deadline, self.llm_timeout_s)
            except EmptyReplyError:
                return "raised", None
            except Exception as e:  # LLMError or transport failure from the chat
                return None, failed(f"llm error: {safe_text(e)}")
            if not (reply.text or "").strip() and not reply.tool_calls:
                return "returned", None
            return reply, None

        def send(fn, arg):
            """An empty reply on any turn is retried once with a short nudge (within the deadline). A raised empty
            reply was not committed: the same turn is sent again with the nudge; a returned one stays in the history,
            so the nudge follows it as a new user message."""
            reply, err = send_once(fn, arg)
            if err or not isinstance(reply, str):
                return reply, err
            if reply == "returned":
                reply, err = send_once(chat.send_text, self.empty_nudge)
            elif fn == chat.send_text:
                reply, err = send_once(fn, f"{arg}\n\n{self.empty_nudge}")
            else:
                reply, err = send_once(fn, _with_note(arg, self.empty_nudge))
            if isinstance(reply, str):
                return None, failed("llm error: empty reply twice")
            return reply, err

        if deadline - time.monotonic() < self.llm_timeout_s:
            return failed("timeout")
        try:
            chat = self.llm.start_chat(self.system, self.tools)
        except Exception as e:
            return failed(f"llm error: {safe_text(e)}")
        reply, err = send(chat.send_text, self._prompt(question, lang, previous))
        while True:
            if err:
                return err
            if not reply.tool_calls:
                if nudged:
                    return failed("model did not call a tool")
                nudged = True
                reply, err = send(chat.send_text, self.nudge)
                continue
            results = []
            for tc in reply.tool_calls:
                calls += 1
                trace.append({"tool": tc.name, "args": tc.args})
                if tc.name == "declare_unanswerable":
                    return WriterResult("unanswerable", reason=str(tc.args.get("reason", "")), trace=trace)
                if calls > self.max_tool_calls:
                    return failed("tool budget exceeded")
                if tc.name in (self.compile_tool, "submit_sql"):
                    outcome = (self._compile(tc.args, allowed, question, deadline, names, state)
                               if tc.name == self.compile_tool
                               else self._sql(tc.args, allowed, question, deadline, names, state))
                    if isinstance(outcome, WriterResult):
                        outcome.trace, outcome.resolved_names = trace, names
                        return outcome
                    fixes += 1
                    trace[-1]["error"] = outcome
                    if fixes > self.max_fixes:
                        return failed(f"fix budget exceeded: {outcome}")
                    results.append((tc.name, {"error": outcome}))
                    continue
                try:
                    payload = self._tool(tc.name, tc.args, allowed, question, deadline, names, state)
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

    def _compile(self, args, allowed, question, deadline, names=(), state=None) -> WriterResult | str:
        state = state if state is not None else {"guard": None}
        try:
            spec = parse_spec(args.get("spec") or {})
            check_catalog(spec, self.catalog)
        except (SpecError, KeyError, ValueError, TypeError) as e:
            return str(e)
        spec = enforce_status_default(question, spec)
        if (err := period_error(question, spec)) is not None:
            return err
        if (err := missing_condition_error(question, spec, self._guard_index, names,
                                           product_attrs=self._product_attrs)) is not None:
            state["guard"] = state["guard"] or err
            return err
        statuses = {(k, v): grounding_status(v, k, allowed, question, self._exists)
                    for k, v in entity_values(spec)}
        absent = sorted(v for (k, v), st in statuses.items() if st == "missing")
        if absent:
            return "; ".join(missing_message(v) for v in absent)
        missing = sorted(v for (k, v), st in statuses.items() if st != "ok")
        if missing:
            return f"values {missing} were not returned by resolve() in this request; call resolve() first"
        try:
            compiled = compile_spec(spec, variants=self.resolver.variant_names)
            result = self.db.query(compiled.sql, timeout_s=self._timeout(deadline, 10.0))
        except (SpecError, KeyError, ValueError, TypeError) as e:
            return f"could not compile spec: {e}"
        except QueryError as e:
            return f"compiled query failed: {e}"
        return WriterResult("ok", "compiled", compiled.sql, result, spec, compiled.splits, compiled.notes,
                            [str(a) for a in args.get("assumptions") or []])

    def _sql(self, args, allowed, question, deadline, names=(), state=None) -> WriterResult | str:
        if state is not None and state.get("guard"):
            return ("submit_sql is not allowed after compile_query reported a missing condition "
                    f"({state['guard'][:160]}...): fix the spec, or call declare_unanswerable")
        sql = str(args.get("sql", ""))
        if (err := sql_missing_condition_error(question, sql, self._guard_index, names)) is not None:
            return err
        v = validate_sql(sql, allowed, question, self.db, exists=self._exists, catalog=self.catalog)
        if not v.ok:
            return "; ".join(v.errors)
        try:
            result = self.db.query(v.sql, timeout_s=self._timeout(deadline, 10.0))
        except QueryError as e:
            return f"query failed: {e}"
        splits = [str(x) for x in args.get("splits") or [] if str(x) in SPLIT_AXES][:2]
        return WriterResult("ok", "sql", v.sql, result, None, splits, [],
                            [str(a) for a in args.get("assumptions") or []])

    def _resolve_variant(self, text: str, allowed, names) -> dict:
        """Compact product_variant result: counts and samples, never the full list of spellings. The phrase itself
        is what gets grounded: the compiler expands filters.product_variant_query with the same matcher."""
        text = text.strip()
        cands = self.resolver.resolve("product_variant", text)
        if not cands:
            return {"matched_spellings": 0, "note": "no registry spelling matches this text; use products (resolve "
                                                    "kind=product) or call declare_unanswerable"}
        allowed.add(("product_variant_query", text))
        products = list(dict.fromkeys(c.extra["product_canonical"] for c in cands))
        samples = [c.id for c in cands[:VARIANT_SAMPLES]]
        if names is not None:
            for value in [text, *products, *samples]:
                if value not in names:
                    names.append(value)
        out = {"text": text, "matched_spellings": len(cands), "deals": sum(c.extra["deals"] for c in cands),
               "products": products[:10], "sample_spellings": samples,
               "note": f"put exactly this text into filters.product_variant_query: {text!r}; the compiler counts all "
                       f"{len(cands)} spellings. If the matches are wrong, resolve a more specific text"}
        missed = self.resolver.same_grade_spellings(text)
        if missed:
            out["same_grade_missed_spellings"] = len(missed)
            out["missed_samples"] = missed[:5]
            out["note"] += (f". {len(missed)} more spellings of the same product with the same grade / mark name the "
                            "product differently and are NOT matched; if the question means the grade whatever the "
                            "product wording, resolve only the grade / mark part of the phrase (e.g. «ЦЕМ I 42,5 Н» "
                            "instead of «Портландцемент ЦЕМ I 42,5 Н») and use that text")
        return out

    def _tool(self, name, args, allowed, question, deadline, names=None, state=None) -> dict:
        if name == "resolve":
            if args["kind"] == "product_variant":
                return self._resolve_variant(str(args["text"]), allowed, names)
            cands = self.resolver.resolve(args["kind"], str(args["text"]))
            allowed.update((_KIND.get(c.kind, c.kind), c.id) for c in cands)
            if names is not None:
                for c in cands:
                    for text in (c.id, c.name, *c.extra.values()):
                        if isinstance(text, str) and text and not text.isdigit() and text not in names:
                            names.append(text)
            return {"candidates": [_json(asdict(c)) for c in cands]}
        if name == "get_examples":
            exs = self.examples.examples
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
