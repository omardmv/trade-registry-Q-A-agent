"""Closed vocabulary stages: stage 1 in the pipeline (before any LLM call), stage 2 in the grammar writer."""
import time

import pytest

from app.agent.grammar_writer import GrammarWriter
from app.pipeline import Pipeline
from app.resolve import Resolver
from tests.fakes import FakeLLM, call

INN = "975923536"   # Pudexiz Plast, a seller of the test data


@pytest.fixture(scope="module")
def parts(db):
    resolver = Resolver(db)
    return db, resolver


def writer_for(parts, replies):
    db, resolver = parts
    llm = FakeLLM(chat_replies=replies)
    return GrammarWriter(llm, db, resolver), llm


def deadline():
    return time.monotonic() + 60


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def M(text, used_as):
    return {"text": text, "used_as": used_as}


def test_stage1_refuses_before_any_llm_call(parts):
    writer, llm = writer_for(parts, [])
    res = Pipeline(writer, FakeLLM()).ask("Курс евро на 1 марта 2025?")
    assert not res.answered and res.source == "no_data"
    assert "«евро»" in res.answer and "Данные охватывают" in res.answer
    assert llm.chat.sent == [] and "евро" in res.reason


def test_stage1_passes_company_names_of_the_database(parts):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("seller", INN), F("year", 2025)],
            "mentions": [M("Pudexiz Plast", "filter:seller"), M("2025", "filter:year")]}
    writer, llm = writer_for(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                                     call("run_plan", plan=plan, assumptions=[])])
    res = Pipeline(writer, FakeLLM()).ask("Сколько продала Pudexiz Plast в 2025?")
    assert res.answered, res.reason


def test_stage2_model_relinking_an_unknown_word_is_refused(parts):
    """A word that passed stage 1 (here: stage 1 skipped) and that the plan does not use is not answered by a
    nearby fact: the dollar rate plan for "евро" is unanswerable, not a fix message."""
    plan = {"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
            "filters": [F("rate_date", "2025-03-01")], "mentions": [M("1 марта 2025", "filter:rate_date")]}
    writer, llm = writer_for(parts, [call("run_plan", plan=plan, assumptions=[])])
    res = writer.run("Курс евро на 1 марта 2025?", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "евро"


def test_stage2_resolved_company_words_pass_only_when_the_plan_uses_the_company(parts):
    used = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("seller", INN), F("year", 2025)],
            "mentions": [M("Pudexiz Plast", "filter:seller"), M("2025", "filter:year")]}
    writer, _ = writer_for(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                                   call("run_plan", plan=used, assumptions=[])])
    assert writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline()).status == "ok"
    dropped = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
               "mentions": [M("2025", "filter:year")]}
    writer, _ = writer_for(parts, [call("run_plan", plan=dropped, assumptions=[])])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "Pudexiz"


def test_stage2_unknown_word_next_to_a_used_company_is_refused(parts):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("seller", INN), F("year", 2025)],
            "mentions": [M("Pudexiz Plast", "filter:seller"), M("2025", "filter:year")]}
    writer, _ = writer_for(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                                   call("run_plan", plan=plan, assumptions=[])])
    res = writer.run("Сколько сотрудников у Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "сотрудников"


def test_stage2_guards_submit_sql(parts):
    writer, _ = writer_for(parts, [call("submit_sql", sql="SELECT AVG(rate) FROM usd_rates WHERE year(rate_date)=2024")])
    res = writer.run("Средний курс юаня в 2024", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "юаня"


def test_pipeline_shows_the_stage2_term(parts):
    plan = {"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
            "filters": [F("year", 2024)], "mentions": [M("2024", "filter:year")]}
    writer, _ = writer_for(parts, [call("run_plan", plan=plan, assumptions=[])])
    res = Pipeline(writer, FakeLLM(), vocab=build_vocab_without_stage1(writer)).ask("Средний курс юаня в 2024")
    assert res.source == "no_data" and "«юаня»" in res.answer


def build_vocab_without_stage1(writer):
    """A vocab whose stage 1 knows every word (to reach stage 2 through the pipeline)."""
    class Everything:
        def unknown(self, question, names=None):
            return []
    return Everything()
