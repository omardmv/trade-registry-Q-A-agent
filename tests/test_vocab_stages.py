"""Closed vocabulary stages: stage 1 in the pipeline (a narrow pre-filter before any LLM call), stage 2 in the
grammar writer (unknown words the plan does not use go to the judge, app/plan/judge.py)."""
import json
import time

import pytest

from app.agent.grammar_writer import GrammarWriter
from app.answer import templates
from app.catalog import load_catalog
from app.pipeline import Pipeline
from app.plan.vocab import build_vocab
from app.resolve import Resolver
from tests.fakes import FakeLLM, call

INN = "975923536"   # Pudexiz Plast, a seller of the test data


@pytest.fixture(scope="module")
def parts(db):
    resolver = Resolver(db)
    return db, resolver


@pytest.fixture(scope="module")
def vocab(db, parts):
    return build_vocab(load_catalog(db), parts[1])


def verdict(covered, missing=""):
    return json.dumps({"covered": covered, "missing": missing}, ensure_ascii=False)


def writer_for(parts, replies, verdicts=(), **kw):
    db, resolver = parts
    llm = FakeLLM(chat_replies=replies, completions=list(verdicts))
    return GrammarWriter(llm, db, resolver, **kw), llm


def deadline():
    return time.monotonic() + 60


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def M(text, used_as):
    return {"text": text, "used_as": used_as}


SELLER_2025 = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("seller", INN), F("year", 2025)],
               "mentions": [M("Pudexiz Plast", "filter:seller"), M("2025", "filter:year")]}


def seller_replies():
    return [call("resolve", kind="company", text="Pudexiz Plast"), call("run_plan", plan=SELLER_2025, assumptions=[])]


def judge_calls(res):
    return [t for t in res.trace if t.get("tool") == "judge"]


# ---- stage 1: a narrow pre-filter ----

def test_stage1_refuses_a_rate_of_an_unknown_currency_before_any_llm_call(parts):
    writer, llm = writer_for(parts, [])
    res = Pipeline(writer, FakeLLM()).ask("Курс евро на 1 марта 2025?")
    assert not res.answered and res.source == "no_data"
    assert "«евро»" in res.answer and "Данные охватывают" in res.answer
    assert llm.chat.sent == [] and "евро" in res.reason and "rate subject" in res.reason


def test_stage1_refuses_a_question_without_any_subject_of_the_data(parts):
    writer, llm = writer_for(parts, [])
    res = Pipeline(writer, FakeLLM()).ask("Какая инфляция в 2023 году?")
    assert res.source == "no_data" and "«инфляция»" in res.answer and llm.chat.sent == []


def test_stage1_rate_positions_follow_the_language(vocab):
    assert vocab.prefilter("Средний курс юаня в 2024").refuse.word == "юаня"
    assert vocab.prefilter("Какой был курс китайского юаня в 2024?").refuse is not None
    assert vocab.prefilter("2024 yilda Xitoy yuanining o'rtacha kursi qancha?").refuse.word == "yuanining"
    assert vocab.prefilter("01.03.2025 даги евро курси қанча?").refuse.word == "евро"
    # Russian: the word before «курс» is an attribute, not the currency
    pre = vocab.prefilter("Каким было минимальное значение курса доллара в 2023 году?")
    assert pre.refuse is None and [u.word for u in pre.unknown] == ["значение"]


def test_stage1_passes_an_ordinary_unknown_word_to_the_planner(vocab):
    pre = vocab.prefilter("Сколько сделок оформила Pudexiz Plast в 2025?")
    assert pre.refuse is None and [u.word for u in pre.unknown] == ["оформила"]


def test_stage1_passes_company_names_of_the_database(parts):
    writer, llm = writer_for(parts, seller_replies())
    res = Pipeline(writer, FakeLLM()).ask("Сколько продала Pudexiz Plast в 2025?")
    assert res.answered, res.reason
    assert llm.prompts == [] and judge_calls(res) == []      # no unknown word: no judge call


# ---- stage 2: the judge ----

def test_stage2_judge_covered_answers(parts):
    writer, llm = writer_for(parts, seller_replies(), [verdict(True)])
    res = Pipeline(writer, FakeLLM()).ask("Сколько сделок оформила Pudexiz Plast в 2025?")
    assert res.answered, res.reason
    prompt = llm.prompts[0]
    assert "Question: Сколько сделок оформила Pudexiz Plast в 2025?" in prompt
    assert "Words not in the vocabulary: оформила" in prompt
    assert "Calculation: Расчёт: сделки; за 2025 год" in prompt.split("Now this one.")[1]
    [j] = judge_calls(res)
    assert j["covered"] is True and j["args"]["words"] == ["оформила"]


def test_stage2_judge_not_covered_refuses_naming_the_missing_words(parts):
    writer, _ = writer_for(parts, seller_replies(), [verdict(False, "в кредит")])
    res = writer.run("Сколько сделок в кредит провела Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "в кредит" and "judge" in res.reason


def test_stage2_missing_text_not_from_the_question_is_replaced_by_the_unknown_word(parts):
    writer, _ = writer_for(parts, seller_replies(), [verdict(False, "loans taken by the company")])
    res = writer.run("Сколько сделок в кредит провела Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "кредит"


@pytest.mark.parametrize("reply", ["covered: yes", '{"covered": "maybe"}', ""])
def test_stage2_unreadable_verdict_refuses(parts, reply):
    writer, _ = writer_for(parts, seller_replies(), [reply])
    res = writer.run("Сколько сделок оформила Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "failed" and res.reason.startswith("judge:")


def test_stage2_judge_error_refuses_and_the_pipeline_says_it_could_not_answer(parts):
    writer, _ = writer_for(parts, seller_replies(), [RuntimeError("boom")])
    writer.llm.complete = lambda system, prompt: (_ for _ in ()).throw(TimeoutError("slow"))
    res = Pipeline(writer, FakeLLM()).ask("Сколько сделок оформила Pudexiz Plast в 2025?")
    assert not res.answered and res.source == "failed" and res.answer == templates.failed("ru")


def test_stage2_no_time_left_for_the_judge_refuses(parts, monkeypatch):
    import types
    import app.plan.judge as judge_mod
    writer, llm = writer_for(parts, seller_replies(), [verdict(True)])
    late = types.SimpleNamespace(monotonic=lambda: time.monotonic() + 1000)   # the judge sees the deadline passed
    monkeypatch.setattr(judge_mod, "time", late)
    res = writer.run("Сколько сделок оформила Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "failed" and res.reason == "judge: timeout" and llm.prompts == []


def test_stage2_judge_off_refuses_any_unused_unknown_word(parts):
    writer, llm = writer_for(parts, seller_replies(), use_judge=False)
    res = writer.run("Сколько сделок оформила Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "оформила" and llm.prompts == []


def test_stage2_company_words_the_plan_does_not_use_go_to_the_judge(parts):
    dropped = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
               "mentions": [M("2025", "filter:year")]}
    writer, llm = writer_for(parts, [call("run_plan", plan=dropped, assumptions=[])], [verdict(False, "Pudexiz Plast")])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "Pudexiz Plast"
    assert "Pudexiz" in llm.prompts[0].split("Now this one.")[1]


def test_stage2_guards_submit_sql(parts):
    writer, llm = writer_for(parts, [call("submit_sql", sql="SELECT COUNT(*) AS n FROM deals WHERE year = 2024")],
                             [verdict(False, "в кредит")])
    res = writer.run("Сколько сделок в кредит было в 2024?", "ru", deadline())
    assert res.status == "unanswerable" and res.term == "в кредит"
    assert "Calculation: SQL: SELECT" in llm.prompts[0]


def test_pipeline_shows_the_judge_term(parts):
    writer, _ = writer_for(parts, seller_replies(), [verdict(False, "в кредит")])
    res = Pipeline(writer, FakeLLM()).ask("Сколько сделок в кредит провела Pudexiz Plast в 2025?")
    assert res.source == "no_data" and "«в кредит»" in res.answer


# ---- max safety (the production default): fail-closed ----

@pytest.mark.max_safety
def test_max_any_unknown_word_refuses_before_any_llm_call(parts):
    writer, llm = writer_for(parts, seller_replies())
    res = Pipeline(writer, FakeLLM()).ask("Сколько сделок оформила Pudexiz Plast в 2025?")
    assert res.source == "no_data" and "«оформила»" in res.answer and llm.chat.sent == []


@pytest.mark.max_safety
def test_max_judge_must_confirm_every_answer(parts):
    writer, llm = writer_for(parts, seller_replies(), [verdict(True)])
    res = Pipeline(writer, FakeLLM()).ask("Сколько продала Pudexiz Plast в 2025?")
    assert res.answered, res.reason
    [j] = judge_calls(res)
    assert j["covered"] is True and "Words not in the vocabulary: (none)" in llm.prompts[0]


@pytest.mark.max_safety
def test_max_judge_not_covered_refuses(parts):
    writer, _ = writer_for(parts, seller_replies(), [verdict(False, "Pudexiz Plast")])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "unanswerable"


@pytest.mark.max_safety
def test_max_no_verdict_refuses_never_answers(parts):
    writer, _ = writer_for(parts, seller_replies(), ["no json here"])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "failed" and "judge" in res.reason


@pytest.mark.max_safety
def test_max_free_sql_is_off(parts):
    writer, llm = writer_for(parts, [call("submit_sql", sql="SELECT COUNT(*) FROM deals", assumptions=[]),
                                     call("declare_unanswerable", reason="cannot express")])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "unanswerable"
    sent = [p for kind, p in llm.chat.sent if kind == "tools"][0][0][1]
    assert "free SQL is turned off" in sent["error"]
