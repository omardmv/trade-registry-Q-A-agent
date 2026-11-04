from app.agent.writer import WriterResult
from app.db import QueryResult
from app.answer import templates
from app.compiler.explain import explain
from app.compiler.spec import parse_spec
from app.compiler.sql import compile_spec
from app.pipeline import Pipeline
from tests.fakes import FakeLLM


class StubWriter:
    def __init__(self, result):
        self.result = result

    def run(self, question, lang, deadline, previous=None):
        return self.result


def ok(rows, columns=("deals_all", "deals_valid")):
    return WriterResult("ok", "sql", "SELECT 1", QueryResult(list(columns), rows, False), splits=["status_scope"])


Q = "Сколько сделок было заключено в октябре 2025 года?"


def test_good_answer_is_returned():
    llm = FakeLLM(completions=["Заключено 9 912 сделок, без аннулированных — 9 300."])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), llm).ask(Q)
    assert r.answered and r.verified and r.source == "llm"
    assert r.lang == "ru"
    assert "9 912" in r.answer


def test_hallucination_is_regenerated_then_templated():
    llm = FakeLLM(completions=["Заключено 10 500 сделок.", "Заключено 10 600 сделок."])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), llm).ask(Q)
    assert r.source == "template" and r.verified
    assert "10 500" not in r.answer and "9 912" in r.answer
    assert "10 500" in llm.prompts[1]


def test_regeneration_can_fix():
    llm = FakeLLM(completions=["Заключено 10 500 сделок.", "Заключено 9 912 сделок."])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), llm).ask(Q)
    assert r.source == "llm" and r.answer.startswith("Заключено 9 912")


def test_empty_and_null_results_are_no_data():
    for rows in ([], [(None, None)]):
        r = Pipeline(StubWriter(ok(rows)), FakeLLM()).ask(Q)
        assert not r.answered and r.source == "no_data"
        assert r.answer.startswith("В данных нет ответа")


def test_unanswerable_in_uzbek():
    r = Pipeline(StubWriter(WriterResult("unanswerable", reason="no profit data")), FakeLLM()).ask(
        "Kompaniyaning foydasi qancha?")
    assert not r.answered and r.answer.startswith("Ma’lumotlarda bu savolga javob yo‘q.")


def test_failed_and_timeout():
    r = Pipeline(StubWriter(WriterResult("failed", reason="timeout")), FakeLLM()).ask(Q)
    assert r.source == "timeout" and not r.answered
    r = Pipeline(StubWriter(WriterResult("failed", reason="fix budget exceeded")), FakeLLM()).ask(Q)
    assert r.source == "failed" and not r.answered


def test_composer_exception_falls_back_to_template():
    class Boom(FakeLLM):
        def complete(self, system, prompt):
            raise TimeoutError
    r = Pipeline(StubWriter(ok([(9912, 9300)])), Boom()).ask(Q)
    assert r.source == "template" and r.answered


def compiled_result(rows=((9912, 9300),)):
    spec = parse_spec({"kind": "deals_aggregate", "measures": ["deal_count"],
                       "filters": {"year": 2025, "month_from": 10, "month_to": 10}})
    comp = compile_spec(spec)
    return WriterResult("ok", "compiled", "SELECT 1", QueryResult(["deals_all", "deals_valid"], list(rows), False),
                        spec=spec, splits=comp.splits, notes=comp.notes), explain(spec, comp.notes, "ru")


def test_explanation_appended_after_llm_answer():
    wr, expl = compiled_result()
    assert expl
    r = Pipeline(StubWriter(wr), FakeLLM(completions=["Заключено 9 912 сделок, без аннулированных — 9 300."])).ask(Q)
    assert r.source == "llm" and r.explanation == expl
    assert r.answer.endswith("\n\n" + expl)
    assert r.answer.startswith("Заключено 9 912")


def test_verifier_receives_explanation_text():
    wr, expl = compiled_result()
    # "10.2025" appears only in the explanation (the question has the word form), so it must be allowed
    llm = FakeLLM(completions=["За период 10.2025 заключено 9 912 сделок."])
    r = Pipeline(StubWriter(wr), llm).ask("Сколько сделок?")
    assert "10.2025" in expl
    assert r.source == "llm" and r.verified


def test_assumptions_and_columns_feed_verifier():
    wr = ok([(9912, 9300)])
    wr.assumptions = ["учтён срок 45 дней"]
    llm = FakeLLM(completions=["Заключено 9 912 сделок; учтён срок 45 дней."])
    r = Pipeline(StubWriter(wr), llm).ask(Q)
    assert r.source == "llm"


def test_writer_exception_is_failed():
    class Crash:
        def run(self, *a, **k):
            raise RuntimeError("boom")
    r = Pipeline(Crash(), FakeLLM()).ask(Q)
    assert r.source == "failed" and not r.answered


def test_truncated_result_says_partial():
    wr = ok([(1, 2), (3, 4)])
    wr.result = QueryResult(["a", "b"], [(1, 2), (3, 4)], True)
    llm = FakeLLM(completions=["Первая строка: 1 и 2."])
    r = Pipeline(StubWriter(wr), llm).ask("Покажи список")
    assert r.source == "llm" and r.truncated
    assert r.answer.endswith("Показаны первые 2 строк.")
    r = Pipeline(StubWriter(wr), FakeLLM(completions=["99", "98"])).ask("Покажи список")
    assert r.source == "template" and "Показаны первые 2 строк." in r.answer


def test_partial_note_templates():
    assert templates.partial_note("ru", 5) == "Показаны первые 5 строк."
    assert templates.partial_note("uz", 5) == "Dastlabki 5 ta qator ko‘rsatilgan."
    assert templates.partial_note("uz-Cyrl", 5)
