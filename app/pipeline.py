"""End-to-end question answering with a hard deadline."""
import logging
import time
from dataclasses import dataclass, field

from app import config
from app.agent.writer import Writer, WriterResult
from app.answer import templates
from app.answer.composer import compose
from app.answer.verifier import verify_answer
from app.compiler.explain import explain
from app.db import to_jsonable
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


class Pipeline:
    def __init__(self, writer, llm, deadline_s: float = 75.0, compose_reserve_s: float = 15.0):
        self.writer, self.llm = writer, llm
        self.deadline_s, self.compose_reserve_s = deadline_s, compose_reserve_s

    def ask(self, question: str, previous: dict | None = None) -> AskResult:
        t0 = time.monotonic()
        deadline = t0 + self.deadline_s
        lang = detect_lang(question)
        try:
            wr = self.writer.run(question, lang, deadline - self.compose_reserve_s, previous)
        except Exception as e:  # never surface a stack trace as an answer
            log.exception("writer crashed")
            wr = WriterResult("failed", reason=f"error: {type(e).__name__}")
        timings = {"writer_s": round(time.monotonic() - t0, 2)}
        res = self._answer(question, lang, wr, deadline)
        timings["total_s"] = round(time.monotonic() - t0, 2)
        res.timings = timings
        return res

    def _answer(self, question: str, lang: str, wr: WriterResult, deadline: float) -> AskResult:
        if wr.status == "unanswerable":
            return AskResult(templates.no_data(lang), lang, False, "no_data", reason=wr.reason)
        if wr.status == "failed":
            if wr.reason == "timeout":
                return AskResult(templates.timeout(lang), lang, False, "timeout", reason=wr.reason)
            return AskResult(templates.failed(lang), lang, False, "failed", reason=wr.reason)

        result = wr.result
        explanation = explain(wr.spec, wr.notes, lang) if wr.spec is not None else ""
        base = dict(lang=lang, sql=wr.sql, path=wr.path, columns=result.columns,
                    rows=[[to_jsonable(c) for c in r] for r in result.rows[:MAX_RETURNED_ROWS]],
                    row_count=len(result.rows), truncated=result.truncated, splits=wr.splits,
                    assumptions=wr.assumptions, explanation=explanation)
        if not result.rows or all(c is None for row in result.rows for c in row):
            return AskResult(templates.no_data(lang), answered=False, source="no_data", **base)

        context = " ".join(wr.assumptions) + " " + explanation + " " + " ".join(result.columns)
        count = None if result.truncated else len(result.rows)
        tail = templates.partial_note(lang, len(result.rows)) if result.truncated else ""
        feedback = None
        unsupported: list[str] = []
        for _ in range(2):
            if time.monotonic() > deadline:
                break
            try:
                text = compose(self.llm, question=question, lang=lang, sql=wr.sql, columns=result.columns,
                               rows=result.rows, row_count=len(result.rows), truncated=result.truncated,
                               splits=wr.splits, assumptions=wr.assumptions, feedback=feedback)
            except Exception:
                log.exception("composer failed")
                break
            check = verify_answer(text, question, result.rows, extra_text=context, row_count=count)
            if check.ok and text:
                answer = "\n\n".join(p for p in (text, explanation, tail) if p)
                return AskResult(answer, answered=True, source="llm", verified=True, **base)
            unsupported = check.unsupported
            feedback = unsupported
        answer = templates.table_answer(lang, result.columns, result.rows,
                                        "\n".join(p for p in (explanation, tail) if p))
        return AskResult(answer, answered=True, source="template", verified=True, unsupported=unsupported, **base)


def build_pipeline() -> Pipeline:
    from app.agent.llm import GeminiLLM
    from app.db import Database
    from app.examples import ExampleStore
    from app.resolve import Resolver
    from app.semantic import load_semantic, render_schema_prompt

    db = Database(config.DB_PATH)
    llm = GeminiLLM(config.GEMINI_API_KEY, config.GEMINI_MODEL, config.LLM_TIMEOUT_S)
    writer = Writer(llm, db, Resolver(db), ExampleStore.from_yaml(config.EXAMPLES_PATH),
                    render_schema_prompt(load_semantic()), config.MAX_TOOL_CALLS, config.MAX_FIX_RETRIES)
    return Pipeline(writer, llm, config.DEADLINE_S)
