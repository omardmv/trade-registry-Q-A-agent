import time

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
    wr.assumptions = ["учтён 2025 год"]
    llm = FakeLLM(completions=["Заключено 9 912 сделок; учтён 2025 год."])
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
    assert r.answer.endswith("Результат запроса неполный: показаны не все строки.")
    r = Pipeline(StubWriter(wr), FakeLLM(completions=["99", "98"])).ask("Покажи список")
    assert r.source == "template" and "Результат запроса неполный" in r.answer


def test_partial_note_templates():
    assert templates.partial_note("ru") == "Результат запроса неполный: показаны не все строки."
    assert templates.partial_note("uz") == "So‘rov natijasi to‘liq emas: barcha qatorlar ko‘rsatilmagan."
    assert templates.partial_note("uz-Cyrl") == "Сўров натижаси тўлиқ эмас: барча қаторлар кўрсатилмаган."


class SlowLLM(FakeLLM):
    def complete(self, system, prompt):
        time.sleep(0.8)
        return super().complete(system, prompt)


def test_second_compose_skipped_when_it_cannot_fit():
    llm = SlowLLM(completions=["Заключено 10 500 сделок.", "Заключено 9 912 сделок."])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), llm, deadline_s=1.0, llm_timeout_s=0.3).ask(Q)
    assert r.source == "template"
    assert len(llm.prompts) == 1
    assert r.timings["total_s"] < 1.2


def test_writer_budget_requires_llm_timeout():
    import app.agent.writer as w
    from tests.fakes import FakeLLM as F
    writer = w.Writer(F(), None, None, None, "schema", llm_timeout_s=25.0)
    res = writer.run("?", "ru", time.monotonic() + 10)
    assert res.status == "failed" and res.reason == "timeout"


def _with_spec(columns, rows):
    wr, _ = compiled_result()
    wr.result = QueryResult(list(columns), rows, False)
    return wr


def test_label_only_rows_are_no_data():
    wr = _with_spec(["role", "inn", "name", "total_uzs"], [("buyer", None, None, None)])
    r = Pipeline(StubWriter(wr), FakeLLM()).ask(Q)
    assert r.source == "no_data" and not r.answered


def test_names_list_with_null_region_is_answered():
    llm = FakeLLM(completions=["«ООО Альфа» — Ташкент; «ООО Бета»."])
    wr = _with_spec(["name", "region"], [("ООО Альфа", "Ташкент"), ("ООО Бета", None)])
    r = Pipeline(StubWriter(wr), llm).ask("Список компаний")
    assert r.answered and r.source != "no_data"


def test_sql_path_null_measure_not_no_data():
    r = Pipeline(StubWriter(ok([("buyer", None)], columns=("role", "total_uzs"))),
                 FakeLLM(completions=["buyer"])).ask(Q)
    assert r.source != "no_data"


def test_zero_count_is_a_real_answer():
    llm = FakeLLM(completions=["Сделок: 0."])
    r = Pipeline(StubWriter(ok([(0,)], columns=("deals",))), llm).ask(Q)
    assert r.answered and r.source == "llm"


def test_empty_llm_text_falls_back_to_template():
    r = Pipeline(StubWriter(ok([(9912, 9300)])), FakeLLM(completions=["", ""])).ask(Q)
    assert r.source == "template"


def test_verifier_exception_is_failed(monkeypatch):
    import app.pipeline as pl
    def boom(*a, **k):
        raise ValueError("x")
    monkeypatch.setattr(pl, "verify_answer", boom)
    r = Pipeline(StubWriter(ok([(9912, 9300)])), FakeLLM(completions=["9 912"])).ask(Q)
    assert r.source == "failed" and not r.answered


# --- final review ---
def test_assumption_with_invented_number_is_dropped_and_not_acceptable():
    wr = ok([(9912, 9300)])
    wr.assumptions = ["учтён срок 45 дней", "статус не указан: учтены все сделки"]
    llm = FakeLLM(completions=["Заключено 9 912 сделок; учтён срок 45 дней.", "Заключено 9 912 сделок."])
    r = Pipeline(StubWriter(wr), llm).ask(Q)
    assert r.assumptions == ["статус не указан: учтены все сделки"]
    assert "45" in llm.prompts[1] and r.source == "llm" and "45" not in r.answer
    assert "45 дней" not in llm.prompts[0]


def test_assumption_supported_by_question_is_kept():
    wr = ok([(9912, 9300)])
    wr.assumptions = ["период: октябрь 2025 года"]
    r = Pipeline(StubWriter(wr), FakeLLM(completions=["Заключено 9 912 сделок."])).ask(Q)
    assert r.assumptions == ["период: октябрь 2025 года"]


def test_resolved_names_feed_verifier():
    wr = ok([(5.0,)], columns=("volume_t",))
    wr.resolved_names = ["Хлопок-сырец"]
    r = Pipeline(StubWriter(wr), FakeLLM(completions=["Продано 5 т товара «Хлопок-сырец»."])).ask("Сколько хлопка?")
    assert r.source == "llm"
    r = Pipeline(StubWriter(ok([(5.0,)], columns=("volume_t",))),
                 FakeLLM(completions=["«Хлопок-сырец»: 5 т.", "5 т."])).ask("Сколько хлопка?")
    assert r.source == "llm" and r.answer == "5 т."


def test_known_names_reach_verifier():
    from app.text import normalize_name
    known = {normalize_name("Pakhtakor Agro")}
    llm = FakeLLM(completions=["Больше всех купила Pakhtakor Agro: 5 т.", "5 т."])
    r = Pipeline(StubWriter(ok([(5.0,)], columns=("volume_t",))), llm, known_names=known).ask("Кто купил больше?")
    assert r.source == "llm" and r.answer == "5 т."


def test_composer_transient_error_retried_once():
    from google.genai import errors

    class Flaky(FakeLLM):
        n = 0

        def complete(self, system, prompt):
            Flaky.n += 1
            if Flaky.n == 1:
                raise errors.ServerError(503, {})
            return super().complete(system, prompt)

    llm = Flaky(completions=["Заключено 9 912 сделок."])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), llm).ask(Q)
    assert r.source == "llm" and Flaky.n == 2


def test_composer_transient_error_not_retried_without_time():
    from google.genai import errors

    class Slow(FakeLLM):
        n = 0

        def complete(self, system, prompt):
            Slow.n += 1
            time.sleep(0.7)
            raise errors.ServerError(503, {})

    r = Pipeline(StubWriter(ok([(9912, 9300)])), Slow(), deadline_s=1.0, llm_timeout_s=0.4).ask(Q)
    assert r.source == "template" and Slow.n == 1


def test_ask_result_carries_writer_trace():
    from app.agent.writer import WriterResult
    from app.pipeline import Pipeline

    class W:
        def run(self, *a, **k):
            return WriterResult("unanswerable", reason="x", trace=[{"tool": "declare_unanswerable", "args": {}}])

    res = Pipeline(W(), None).ask("Сколько сделок?")
    assert res.trace == [{"tool": "declare_unanswerable", "args": {}}]
