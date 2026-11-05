"""End-to-end question answering with a hard deadline."""
import logging
import time
from dataclasses import dataclass, field

from app import config
from app.agent.writer import Writer, WriterResult
from app.answer import plan_templates, templates
from app.agent.llm import call_with_retry, deadline_scope, safe_text, track_providers
from app.answer.composer import compose
from app.answer.verifier import verify_answer
from app.compiler.explain import explain
from app.compiler.sql import MEASURE_SQL
from app.db import to_jsonable
from app.plan.dictionary import load_dictionary
from app.plan.explain import explain_plan
from app.plan.model import Plan
from app.plan.vocab import log_refusal
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
    column_labels: list[str] = field(default_factory=list)  # readable column names in the question's language
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
                 known_names: frozenset[str] | set[str] | None = None, alias_groups=None, vocab=None):
        self.writer, self.llm = writer, llm
        self.known_names, self.alias_groups = known_names, alias_groups
        self.deadline_s, self.llm_timeout_s = deadline_s, llm_timeout_s
        # grammar planner: answers are rendered from the plan by code (app/answer/plan_templates.py)
        self.grammar = getattr(writer, "compile_tool", None) == "run_plan"
        # closed vocabulary, stage 1 (grammar planner only): the writer's lexicon unless one is given
        self.vocab = vocab if vocab is not None else (getattr(writer, "vocab", None) if self.grammar else None)

    def ask(self, question: str, previous: dict | None = None) -> AskResult:
        with track_providers() as used:
            res = self._ask(question, previous)
        res.providers_used = list(used)
        return res

    def _ask(self, question: str, previous: dict | None) -> AskResult:
        t0 = time.monotonic()
        deadline = t0 + self.deadline_s
        lang = detect_lang(question)
        if (refused := self._vocab_refusal(question, lang)) is not None:
            refused.timings = {"writer_s": 0.0, "total_s": round(time.monotonic() - t0, 2)}
            return refused
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

    def _vocab_refusal(self, question: str, lang: str) -> AskResult | None:
        """Stage 1 of the closed vocabulary, before any LLM call, a narrow pre-filter: refuse only when the
        question names no subject the data has, or an unknown word is the subject of a rate («курс юаня»). Other
        unknown words go on: the writer judges the ones its plan does not use (stage 2)."""
        if self.vocab is None:
            return None
        if config.SAFETY == "max":
            # fail-closed: any word outside the lexicon and outside every name in the database refuses
            try:
                unknown = self.vocab.unknown(question)
            except Exception:
                log.exception("vocabulary check crashed")
                return AskResult(templates.failed(lang), lang, False, "failed", reason="vocabulary check crashed")
            if not unknown:
                return None
            log_refusal("stage 1, max safety", question, unknown)
            return AskResult(templates.no_data(lang, unknown[0].word), lang, False, "no_data",
                             reason="not in the data (max safety): " + ", ".join(u.word for u in unknown))
        try:
            pre = self.vocab.prefilter(question)
        except Exception:
            log.exception("vocabulary check crashed")
            return None
        if pre.refuse is None:
            if pre.unknown:
                log.info("closed vocabulary (stage 1): passing unknown words %s to the planner",
                         [u.word for u in pre.unknown])
            return None
        log_refusal(f"stage 1, {pre.why}", question, [pre.refuse])
        return AskResult(templates.no_data(lang, pre.refuse.word), lang, False, "no_data",
                         reason=f"not in the data ({pre.why}): " + ", ".join(u.word for u in pre.unknown))

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
            return AskResult(templates.no_data(lang, wr.term or None), lang, False, "no_data", reason=wr.reason)
        if wr.status == "failed":
            if wr.reason == "timeout":
                return AskResult(templates.timeout(lang), lang, False, "timeout", reason=wr.reason)
            return AskResult(templates.failed(lang), lang, False, "failed", reason=wr.reason)

        result = wr.result
        labels = list(result.columns)
        if isinstance(wr.spec, Plan):
            explanation = explain_plan(wr.spec, load_dictionary(), wr.notes, lang)
            labels = plan_templates.column_labels(wr.spec, load_dictionary(), result.columns, lang)
        else:
            explanation = explain(wr.spec, wr.notes, lang) if wr.spec is not None else ""
        base = dict(lang=lang, sql=wr.sql, path=wr.path, columns=result.columns,
                    rows=[[to_jsonable(c) for c in r] for r in result.rows[:MAX_RETURNED_ROWS]],
                    row_count=len(result.rows), truncated=result.truncated, splits=wr.splits,
                    assumptions=wr.assumptions, explanation=explanation, column_labels=labels)
        if _is_no_data(result, wr.spec is not None, wr.measure_columns):
            base["assumptions"] = []
            return AskResult(templates.no_data(lang), answered=False, source="no_data", **base)

        shown = result.rows[:MAX_RETURNED_ROWS]  # exactly what the composer sees
        resolved = " ".join(wr.resolved_names)
        assumptions = self._supported_assumptions(wr.assumptions, question, shown, explanation + " " + resolved)
        base["assumptions"] = assumptions
        context = " ".join(assumptions) + " " + explanation + " " + " ".join(result.columns) + " " + resolved
        if isinstance(wr.spec, Plan):     # numbers the plan templates derive from cells (price per kg -> per tonne)
            context += " " + plan_templates.derived_numbers(wr.spec, load_dictionary(), result.columns, shown)
        count = None if result.truncated or len(result.rows) > MAX_RETURNED_ROWS else len(result.rows)
        tail = templates.partial_note(lang) if result.truncated else ""
        feedback = None
        unsupported: list[str] = []
        if self.grammar:
            # grammar mode: the answer text is rendered by code from the plan and the rows; no composer call
            text = (plan_templates.render(wr.spec, load_dictionary(), result.columns, shown, lang)
                    if isinstance(wr.spec, Plan) else None)
            if text:
                answer = "\n\n".join(p for p in (text, explanation, tail) if p)
                check = verify_answer(answer, question, shown, extra_text=context, row_count=count,
                                      known_names=self.known_names, alias_groups=self.alias_groups)
                if check.ok:
                    return AskResult(answer, answered=True, source="plan_template", verified=True, **base)
                log.error("plan template failed the answer check: %s", check.unsupported)
                unsupported = check.unsupported
            answer = templates.table_answer(lang, result.columns, result.rows,
                                            "\n".join(p for p in (explanation, tail) if p))
            return AskResult(answer, answered=True, source="template", verified=True, unsupported=unsupported, **base)
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
    llm = build_llm(config.LLM_PROVIDERS, config.LLM_TIMEOUT_S)
    resolver = Resolver(db)
    if config.PLANNER == "grammar":
        from app.agent.grammar_writer import GrammarWriter
        writer = GrammarWriter(llm, db, resolver, config.MAX_TOOL_CALLS + 4, config.GRAMMAR_MAX_FIX_RETRIES,
                               config.LLM_TIMEOUT_S)
        if not config.CLOSED_VOCAB:
            writer.vocab = None          # both stages off
    else:
        writer = Writer(llm, db, resolver, ExampleStore.from_yaml(config.EXAMPLES_PATH),
                        render_schema_prompt(load_semantic()), config.MAX_TOOL_CALLS, config.MAX_FIX_RETRIES,
                        config.LLM_TIMEOUT_S)
    return Pipeline(writer, llm, config.DEADLINE_S, config.LLM_TIMEOUT_S, resolver.known_names(), resolver.alias_groups())
