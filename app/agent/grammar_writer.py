"""Grammar planner mode of the writer (PLANNER=grammar): one generic plan instead of the structured form.

Same loop, grounding, deadline and free-SQL fallback as the legacy Writer; the compile tool is run_plan(plan).
"""
from app import config
from app.agent.validators import grounding_status, missing_message
from app.agent.writer import Writer, WriterResult
from app.db import QueryError
from app.plan.build import build_sql
from app.plan.checks import apply_defaults, consistency_error
from app.plan.dictionary import Dictionary, load_dictionary
from app.plan.model import PlanError, parse_plan
from app.plan.prompt import TOOLS, build_system, load_examples
from app.plan.validate import grounding_pairs, validate_plan


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
        plan = apply_defaults(plan, d, question)
        if (err := consistency_error(plan, d, question)) is not None:
            return err
        statuses = {(k, v): grounding_status(v, k, allowed, question, self._exists) for k, v in grounding_pairs(plan, d)}
        absent = sorted(v for (k, v), st in statuses.items() if st == "missing")
        if absent:
            return "; ".join(missing_message(v) for v in absent)
        missing = sorted(v for (k, v), st in statuses.items() if st != "ok")
        if missing:
            return f"values {missing} were not returned by resolve() in this request; call resolve() first"
        try:
            built = build_sql(plan, d, variants=self.resolver.variant_names)
            result = self.db.query(built.sql, timeout_s=self._timeout(deadline, 10.0))
        except (PlanError, KeyError, ValueError, TypeError) as e:
            return f"could not build the plan: {e}"
        except QueryError as e:
            return f"query failed: {e}"
        return WriterResult("ok", "compiled", built.sql, result, plan, built.splits, built.notes,
                            [str(a) for a in args.get("assumptions") or []], measure_columns=built.measure_columns)

    def _resolve_variant(self, text, allowed, names) -> dict:
        out = super()._resolve_variant(text, allowed, names)
        if "note" in out:
            out["note"] = out["note"].replace("filters.product_variant_query",
                                              "a filter {field: product_variant, op: =, value: <this text>}")
            out["note"] = out["note"].replace("the compiler", "the builder")
        return out
