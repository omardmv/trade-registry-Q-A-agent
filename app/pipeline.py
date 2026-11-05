"""End-to-end question answering with a hard deadline."""
import logging
import time
from dataclasses import dataclass, field

from app import config
from app.agent.writer import Writer, WriterResult
from app.answer import templates
from app.agent.llm import call_with_retry, deadline_scope, safe_text, track_providers
from app.answer.composer import compose
from app.answer.verifier import verify_answer
from app.compiler.explain import explain
from app.compiler.sql import MEASURE_SQL
from app.db import to_jsonable
from app.plan.dictionary import load_dictionary
from app.plan.explain import explain_plan
from app.plan.model import Plan
from app.text import detect_lang

log = logging.getLogger(__name__)
MAX_RETURNED_ROWS = 200


@dataclass
class AskResult:
    answer: str
    lang: str
    answered: bool
    source: str
    sql: str | None = None
    path: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    splits: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    explanation: str = ""
    verified: bool = True
    unsupported: list[str] = field(default_factory=list)
    reason: str = ""
    timings: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)  # writer tool calls (no rows); eval only
    providers_used: list[str] = field(default_factory=list)  # LLM provider of each successful reply, in order


_MEASURE_COLUMNS = {alias for _, alias in MEASURE_SQL.values()}
_MEASURE_COLUMNS |= {f"{a}{suffix}" for a in _MEASURE_COLUMNS for suffix in ("_all", "_valid")}


def _is_no_data(result, compiled: bool, measure_columns=None) -> bool:
    """No rows, all cells NULL, or (compiled path) every measure cell NULL. Zero counts are answers."""
    if not result.rows:
        return True
    if all(c is None for row in result.rows for c in row):
        return True
    if not compiled:
        return False
    names = set(measure_columns) if measure_columns else _MEASURE_COLUMNS
    idx = [i for i, c in enumerate(result.columns) if c in names]
    return bool(idx) and all(row[i] is None for row in result.rows for i in idx)


class Pipeline:
    def __init__(self, writer, llm, deadline_s: float = 75.0, llm_timeout_s: float = config.LLM_TIMEOUT_S,
                 known_names: frozenset[str] | set[str] | None = None, alias_groups=None):
        self.writer, self.llm = writer, llm
        self.known_names, self.alias_groups = known_names, alias_groups
        self.deadline_s, self.llm_timeout_s = deadline_s, llm_timeout_s

    def ask(self, question: str, previous: dict | None = None) -> AskResult:
        with track_providers() as used:
            res = self._ask(question, previous)
        res.providers_used = list(used)
        return res

    def _ask(self, question: str, previous: dict | None) -> AskResult:
        t0 = time.monotonic()
        deadline = t0 + self.deadline_s
        lang = detect_lang(question)
        try:
            wr = self.writer.run(question, lang, deadline - self.llm_timeout_s, previous)
        except Exception as e:  # never surface a stack trace as an answer
            log.exception("writer crashed")
            wr = WriterResult("failed", reason=f"error: {type(e).__name__}")
        timings = {"writer_s": round(time.monotonic() - t0, 2)}
        try:
            res = self._answer(question, lang, wr, deadline)
        except Exception:
            log.exception("answer stage crashed")
            res = AskResult(templates.failed(lang), lang, False, "failed", reason="error: answer stage")
        timings["total_s"] = round(time.monotonic() - t0, 2)
        res.timings = timings
        res.trace = wr.trace
        return res

    def _supported_assumptions(self, assumptions, question, shown, extra) -> list[str]:
        """Assumptions are model text: keep only those whose numbers/ids/dates/names the data supports."""
        kept = []
        for a in assumptions:
            try:
                check = verify_answer(a, question, shown, extra_text=extra, known_names=self.known_names, alias_groups=self.alias_groups)
            except Exception:
                log.exception("assumption check crashed")
                check = None
            if check is not None and check.ok:
                kept.append(a)
            else:
                log.info("dropping unsupported assumption %r: %s", a, check.unsupported if check else "error")
        return kept

    def _answer(self, question: str, lang: str, wr: WriterResult, deadline: float) -> AskResult:
        if wr.status == "unanswerable":
            return AskResult(templates.no_data(lang), lang, False, "no_data", reason=wr.reason)
        if wr.status == "failed":
            if wr.reason == "timeout":
                return AskResult(templates.timeout(lang), lang, False, "timeout", reason=wr.reason)
            return AskResult(templates.failed(lang), lang, False, "failed", reason=wr.reason)

        result = wr.result
        if isinstance(wr.spec, Plan):
            explanation = explain_plan(wr.spec, load_dictionary(), wr.notes, lang)
        else:
            explanation = explain(wr.spec, wr.notes, lang) if wr.spec is not None else ""
        base = dict(lang=lang, sql=wr.sql, path=wr.path, columns=result.columns,
                    rows=[[to_jsonable(c) for c in r] for r in result.rows[:MAX_RETURNED_ROWS]],
                    row_count=len(result.rows), truncated=result.truncated, splits=wr.splits,
                    assumptions=wr.assumptions, explanation=explanation)
        if _is_no_data(result, wr.spec is not None, wr.measure_columns):
            base["assumptions"] = []
            return AskResult(templates.no_data(lang), answered=False, source="no_data", **base)

        shown = result.rows[:MAX_RETURNED_ROWS]  # exactly what the composer sees
        resolved = " ".join(wr.resolved_names)
        assumptions = self._supported_assumptions(wr.assumptions, question, shown, explanation + " " + resolved)
        base["assumptions"] = assumptions
        context = " ".join(assumptions) + " " + explanation + " " + " ".join(result.columns) + " " + resolved
        count = None if result.truncated or len(result.rows) > MAX_RETURNED_ROWS else len(result.rows)
        tail = templates.partial_note(lang) if result.truncated else ""
        feedback = None
        unsupported: list[str] = []
        for _ in range(2):
            if deadline - time.monotonic() < self.llm_timeout_s:
                break
            try:
                with deadline_scope(deadline):  # a provider fallback must fit before the deadline too
                    text = call_with_retry(
                        lambda: compose(self.llm, question=question, lang=lang, sql=wr.sql, columns=result.columns,
                                        rows=result.rows, row_count=len(result.rows), truncated=result.truncated,
                                        splits=wr.splits, assumptions=assumptions, feedback=feedback),
                        deadline, self.llm_timeout_s)
            except Exception as e:  # no traceback: provider messages may contain key material
                log.error("composer failed: %s: %s", type(e).__name__, safe_text(e))
                break
            check = verify_answer(text, question, shown, extra_text=context, row_count=count,
                                  known_names=self.known_names, alias_groups=self.alias_groups)
            if check.ok and text:
                answer = "\n\n".join(p for p in (text, explanation, tail) if p)
                return AskResult(answer, answered=True, source="llm", verified=True, **base)
            unsupported = check.unsupported
            feedback = unsupported
        answer = templates.table_answer(lang, result.columns, result.rows,
                                        "\n".join(p for p in (explanation, tail) if p))
        return AskResult(answer, answered=True, source="template", verified=True, unsupported=unsupported, **base)


def build_pipeline() -> Pipeline:
    from app.agent.llm_fallback import build_llm
    from app.db import Database
    from app.examples import ExampleStore
    from app.resolve import Resolver
    from app.semantic import load_semantic, render_schema_prompt

    db = Database(config.DB_PATH)
    llm = build_llm(config.LLM_PROVIDERS, {"gemini": config.GEMINI_API_KEY, "openai": config.OPENAI_API_KEY},
                    {"gemini": config.GEMINI_MODEL, "openai": config.OPENAI_MODEL}, config.LLM_TIMEOUT_S)
    resolver = Resolver(db)
    if config.PLANNER == "grammar":
        from app.agent.grammar_writer import GrammarWriter
        writer = GrammarWriter(llm, db, resolver, config.MAX_TOOL_CALLS, config.MAX_FIX_RETRIES, config.LLM_TIMEOUT_S)
    else:
        writer = Writer(llm, db, resolver, ExampleStore.from_yaml(config.EXAMPLES_PATH),
                        render_schema_prompt(load_semantic()), config.MAX_TOOL_CALLS, config.MAX_FIX_RETRIES,
                        config.LLM_TIMEOUT_S)
    return Pipeline(writer, llm, config.DEADLINE_S, config.LLM_TIMEOUT_S, resolver.known_names(), resolver.alias_groups())
