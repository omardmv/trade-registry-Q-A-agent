"""Grammar planner mode of the writer (PLANNER=grammar): one generic plan instead of the structured form.

Same loop, grounding, deadline and free-SQL fallback as the legacy Writer; the compile tool is run_plan(plan).
"""
from app import config
from app.agent.guard import _tok_match
from app.agent.validators import grounding_status, missing_message
from app.agent.writer import Writer, WriterResult
from app.db import QueryError
from app.plan.build import build_sql
from app.plan.checks import apply_defaults, consistency_error, plan_missing_condition_error
from app.plan.dictionary import Dictionary, load_dictionary
from app.plan.mentions import Resolution, mention_error
from app.plan.model import PlanError, parse_plan
from app.plan.prompt import TOOLS, build_system, load_examples
from app.plan.validate import grounding_pairs, validate_plan
from app.text import normalize_text


class GrammarWriter(Writer):
    tools = TOOLS
    compile_tool = "run_plan"
    nudge = "Do not answer in text. Call run_plan, submit_sql or declare_unanswerable."

    def __init__(self, llm, db, resolver, max_tool_calls: int = 8, max_fixes: int = 2,
                 llm_timeout_s: float = config.LLM_TIMEOUT_S, catalog: dict[str, set[str]] | None = None,
                 dictionary: Dictionary | None = None, examples: list[dict] | None = None):
        super().__init__(llm, db, resolver, None, "", max_tool_calls, max_fixes, llm_timeout_s, catalog)
        self.dictionary = dictionary or load_dictionary()
        self.system = build_system(self.dictionary, load_examples() if examples is None else examples)

    def _compile(self, args, allowed, question, deadline, names=(), state=None) -> WriterResult | str:
        d = self.dictionary
        try:
            plan = validate_plan(parse_plan(args.get("plan") or {}), d, self.catalog)
        except (PlanError, KeyError, TypeError) as e:
            return str(e)
        raw_filters = frozenset(f.field for f in plan.filters)
        plan = apply_defaults(plan, d, question)
        if (err := consistency_error(plan, d, question)) is not None:
            return err
        if (err := plan_missing_condition_error(question, plan, d, self._guard_index, names,
                                                self._product_attrs)) is not None:
            if state is not None:
                state["guard"] = state.get("guard") or err      # submit_sql may not bypass it
            return err
        statuses = {(k, v): grounding_status(v, k, allowed, question, self._exists) for k, v in grounding_pairs(plan, d)}
        absent = sorted(v for (k, v), st in statuses.items() if st == "missing")
        if absent:
            return "; ".join(missing_message(v) for v in absent)
        missing = sorted(v for (k, v), st in statuses.items() if st != "ok")
        if missing:
            return f"values {missing} were not returned by resolve() in this request; call resolve() first"
        resolutions = (state or {}).get("resolved", ())
        if (err := mention_error(plan, d, question, self._guard_index, resolutions, names, raw_filters)) is not None:
            return err
        try:
            built = build_sql(plan, d, variants=self.resolver.variant_names)
            result = self.db.query(built.sql, timeout_s=self._timeout(deadline, 10.0))
        except (PlanError, KeyError, ValueError, TypeError) as e:
            return f"could not build the plan: {e}"
        except QueryError as e:
            return f"query failed: {e}"
        return WriterResult("ok", "compiled", built.sql, result, plan, built.splits, built.notes,
                            [str(a) for a in args.get("assumptions") or []], measure_columns=built.measure_columns)

    def _tool(self, name, args, allowed, question, deadline, names=None, state=None) -> dict:
        if name == "resolve" and str(args.get("kind")) == "catalog":
            return self._resolve_catalog(str(args.get("text", "")))
        out = super()._tool(name, args, allowed, question, deadline, names, state)
        if name == "resolve" and state is not None:
            text = str(args.get("text", ""))
            values = {str(c.get("id")) for c in out.get("candidates", ())} | {str(c.get("name"))
                                                                             for c in out.get("candidates", ())}
            if out.get("matched_spellings"):
                values.add(text.strip())
            state.setdefault("resolved", []).append(Resolution(str(args.get("kind", "")), text, frozenset(values)))
        return out

    def _resolve_catalog(self, text: str) -> dict:
        """Catalog / status values matching the text (inflection-tolerant), with the plan fields that take them.
        A field or catalog name (category, activity, status, ...) lists all its values."""
        key = text.strip().lower()
        fields_of = {}
        for s in self.dictionary.subjects.values():
            for f in s.fields.values():
                cat = f.catalog or (f.name if f.ground == "enum" else "")
                if cat:
                    fields_of.setdefault(cat, set()).add(f.name)
        cat_of = {n: c for c, names in fields_of.items() for n in names}
        if key in cat_of or key in fields_of:
            cat = cat_of.get(key, key)
            values = sorted(self.catalog.get(cat, ())) or sorted({v for s in self.dictionary.subjects.values()
                                                                  for f in s.fields.values()
                                                                  if f.name == key for v in f.values})
            return {"catalog": cat, "fields": sorted(fields_of.get(cat, ())), "values": values[:60]}
        toks = normalize_text(text).split()
        scored = {}
        for v in self._guard_index:
            hit = sum(1 for g in v.tokens if any(_tok_match(g, w, False) for w in toks))
            if hit:
                score = hit / len(v.tokens)
                if score > scored.get((v.field, v.value), 0):
                    scored[(v.field, v.value)] = score
        best = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        if not best:
            return {"candidates": [], "note": "no catalog value matches; resolve(kind=catalog, text=<field name>) "
                                              "lists the values of a field"}
        return {"candidates": [{"catalog": c, "value": v, "fields": sorted(fields_of.get(c, {c}))}
                               for (c, v), _ in best]}

    def _resolve_variant(self, text, allowed, names) -> dict:
        out = super()._resolve_variant(text, allowed, names)
        if "note" in out:
            out["note"] = out["note"].replace("filters.product_variant_query",
                                              "a filter {field: product_variant, op: =, value: <this text>}")
            out["note"] = out["note"].replace("the compiler", "the builder")
        return out
