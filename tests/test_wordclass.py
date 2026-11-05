"""Word classifier (app/plan/wordclass.py): closed-choice labels for the unknown words of a question in max safety.

Parsing, the plan-uses-concept check, errors -> refuse, and both stages through the pipeline in ru / uz Latin /
uz Cyrillic. FakeLLM only (no API calls)."""
import json
import time

import pytest

from app.agent.grammar_writer import GrammarWriter
from app.answer import templates
from app.pipeline import Pipeline
from app.plan import wordclass
from app.plan.dictionary import load_dictionary
from app.plan.model import Filter, Measure, Period, Plan
from app.plan.wordclass import (OTHER, WORDING, WordClasses, build_prompt, classify, concepts, labels, parse_labels,
                                plan_uses, system_prompt)
from app.resolve import Resolver
from tests.fakes import FakeLLM, call

INN = "975923536"   # Pudexiz Plast, a seller of the test data
ALLOWED = labels()


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def M(text, used_as):
    return {"text": text, "used_as": used_as}


def lab(**kw):
    return json.dumps(kw, ensure_ascii=False)


def later():
    return time.monotonic() + 60


# ---- concepts and prompt ----

def test_concepts_come_from_the_semantic_layer_and_the_shapes():
    c = concepts()
    for k in ("deals", "companies", "brokers", "seller", "broker", "amount_uzs", "price_per_kg", "rate",
              "deposit_pct", "top", "share", "count", "total", "average", "minimum", "maximum", "trend", "period"):
        assert k in c, k
    assert WORDING in ALLOWED and OTHER in ALLOWED and "euro" not in ALLOWED


def test_prompt_names_the_rules_and_has_examples_in_three_scripts():
    s = system_prompt()
    assert "MEANS" in s and "When unsure -> \"other\"" in s and "price_per_kg:" in s
    p = build_prompt("Pudexiz Plast direktori kim?", ["direktori"])
    assert "евро" in p and "yetakchi" in p and "Чиндан" in p          # ru, uz Latin, uz Cyrillic
    assert '"other"' in p and '"top"' in p and '"wording"' in p        # both sides
    assert p.rstrip().endswith('Words: ["direktori"]\nLabels:')


# ---- parsing ----

def test_parse_labels_reads_every_word():
    out = parse_labels('{"yetakchi": "top", "ayting": "wording", "evro": "other"}', ["yetakchi", "ayting", "evro"],
                       ALLOWED)
    assert out.ok and out.labels == {"yetakchi": "top", "ayting": "wording", "evro": OTHER}
    assert out.other() == ["evro"] and out.concept_words() == {"yetakchi": "top"}


def test_parse_labels_tolerates_text_around_and_key_case():
    out = parse_labels('Here: {"Лидер": "Top"} done', ["лидер"], ALLOWED)
    assert out.labels == {"лидер": "top"} and out.label_of("ЛИДЕР") == "top"


def test_a_label_outside_the_closed_list_is_other():
    assert parse_labels('{"евро": "currency_eur"}', ["евро"], ALLOWED).labels == {"евро": OTHER}


@pytest.mark.parametrize("reply", ["no json", '{"a": "top"}', '{"qism": 3}', '{"qism": ""}', "[1, 2]", "{bad json}"])
def test_no_verdict_for_every_word_is_an_error(reply):
    out = parse_labels(reply, ["qism"], ALLOWED)
    assert not out.ok and out.labels == {}


def test_classify_errors_never_raise():
    class Boom:
        def complete(self, system, prompt):
            raise RuntimeError("down")
    assert classify(Boom(), "q", ["x"], later(), 5).error.startswith("llm error")
    assert classify(FakeLLM(), "q", ["x"], time.monotonic() + 1, 5).error == "timeout"
    assert classify(FakeLLM(), "q", [], later(), 5).labels == {}


def test_classify_sends_the_question_and_words(tmp_path, monkeypatch):
    log = tmp_path / "wc.jsonl"
    monkeypatch.setenv("UZEX_WORDCLASS_LOG", str(log))
    llm = FakeLLM(completions=[lab(qismi="share")])
    out = classify(llm, "Bitimlar qismi qancha?", ["qismi", "qismi"], later(), 5)
    assert out.labels == {"qismi": "share"} and 'Words: ["qismi"]' in llm.prompts[0]
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert rec["labels"] == {"qismi": "share"} and rec["question"] == "Bitimlar qismi qancha?"


# ---- the plan uses the concept ----

def P(**kw):
    return Plan(**{"subject": "deals", "measures": (Measure("count"),), **kw})


@pytest.mark.parametrize("concept,plan,uses", [
    ("top", P(group_by=("seller",), limit=3), True),
    ("top", P(group_by=("seller",)), False),
    ("share", P(measures=(Measure("share", None, (Filter("contract_type", "=", "Экспортный"),)),)), True),
    ("share", P(measures=(Measure("cancelled_share"),)), True),
    ("share", P(), False),
    ("count", P(), True),
    ("count", P(measures=(Measure("sum", "amount_uzs"),)), False),
    ("total", P(measures=(Measure("sum", "amount_uzs"),)), True),
    ("average", P(measures=(Measure("price_per_kg"),)), True),
    ("average", P(measures=(Measure("avg", "deal_price_per_kg"),)), True),
    ("average", P(), False),
    ("minimum", P(measures=(Measure("min", "deal_price_per_kg"),)), True),
    ("minimum", P(group_by=("seller",), limit=1, order="asc"), True),
    ("maximum", P(group_by=("seller",), limit=1, order="asc"), False),
    ("trend", P(group_by=("year",)), True),
    ("trend", P(compare=(Period(2023), Period(2024))), True),
    ("trend", P(filters=(Filter("year", "=", 2024),)), False),
    ("period", P(filters=(Filter("year", "=", 2024),)), True),
    ("period", P(), False),
    ("broker", P(group_by=("seller_broker",)), True),           # the generic broker: either side
    ("seller", P(group_by=("buyer",)), False),                  # a side is not the other side
    ("seller", P(filters=(Filter("seller", "=", INN),)), True),
    ("companies", P(group_by=("buyer",)), True),
    ("brokers", P(group_by=("seller",)), False),
    ("deals", P(), True),
    ("contracts", P(), False),
    ("amount_uzs", P(measures=(Measure("sum", "amount_uzs"),)), True),
    ("amount_uzs", P(measures=(Measure("sum", "amount_usd"),)), False),
    ("price_per_kg", P(measures=(Measure("price_per_kg"),)), True),
    ("deposit_pct", P(), False),                                # a contracts field the deals plan does not use
    ("rate", P(), False),
])
def test_plan_uses(concept, plan, uses):
    assert plan_uses(concept, plan, load_dictionary()) is uses


# ---- through the pipeline (stage 1) and the writer (stage 2) ----

@pytest.fixture(scope="module")
def parts(db):
    return db, Resolver(db)


def verdict(covered, missing=""):
    return json.dumps({"covered": covered, "missing": missing}, ensure_ascii=False)


def ask(parts, question, labels_reply, replies=(), verdicts=()):
    db, resolver = parts
    llm = FakeLLM(chat_replies=list(replies), completions=list(verdicts))
    writer = GrammarWriter(llm, db, resolver)
    front = FakeLLM(completions=[labels_reply] if labels_reply is not None else [])
    return Pipeline(writer, front).ask(question), llm, front


SELLER_2025 = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("seller", INN), F("year", 2025)],
               "mentions": [M("Pudexiz Plast", "filter:seller"), M("2025", "filter:year")]}


def seller_replies(plan=SELLER_2025):
    return [call("resolve", kind="company", text="Pudexiz Plast"), call("run_plan", plan=plan, assumptions=[])]


@pytest.mark.max_safety
@pytest.mark.word_classifier
@pytest.mark.parametrize("question,word,lang", [
    ("Сколько директоров у Pudexiz Plast?", "директоров", "ru"),
    ("Pudexiz Plast direktori kim?", "direktori", "uz"),
    ("Pudexiz Plast директори ким?", "директори", "uz_cyrl"),
])
def test_other_refuses_naming_the_word_before_the_planner(parts, question, word, lang):
    res, llm, front = ask(parts, question, lab(**{word: "other"}))
    assert res.source == "no_data" and f"«{word}»" in res.answer and llm.chat.sent == []
    assert "word classifier" in res.reason
    [t] = res.trace
    assert t["tool"] == "word_classifier" and t["labels"] == {word: "other"} and "classes" not in t


@pytest.mark.max_safety
@pytest.mark.word_classifier
@pytest.mark.parametrize("reply", ["no json", '{"евро": 1}', RuntimeError("provider down")])
def test_no_verdict_refuses(parts, reply):
    res, llm, _ = ask(parts, "Какой курс евро в 2025?", reply)
    assert not res.answered and res.source == "failed" and llm.chat.sent == []
    assert res.answer == templates.failed("ru")


@pytest.mark.max_safety
@pytest.mark.word_classifier
def test_one_other_word_among_wording_refuses(parts):
    res, llm, _ = ask(parts, "Сколько сделок в рассрочку оформила Pudexiz Plast в 2025?",
                      lab(рассрочку="other", оформила="wording"))
    assert res.source == "no_data" and "«рассрочку»" in res.answer and llm.chat.sent == []


@pytest.mark.max_safety
@pytest.mark.word_classifier
@pytest.mark.parametrize("question,word", [
    ("Pudexiz Plast 2025 yilda chindan nechta bitim tuzgan?", "chindan"),          # uz Latin, was a noun refusal
    ("Сколько сделок реально оформила Pudexiz Plast в 2025?", "реально"),
])
def test_wording_goes_on_and_the_judge_confirms(parts, question, word):
    words = [word] + (["оформила"] if "оформила" in question else [])
    res, llm, front = ask(parts, question, lab(**{w: "wording" for w in words}), seller_replies(), [verdict(True)])
    assert res.answered, res.reason
    [j] = [t for t in res.trace if t.get("tool") == "judge"]
    assert word in j["args"]["words"]
    assert [t["tool"] for t in res.trace][0] == "word_classifier"


@pytest.mark.max_safety
@pytest.mark.word_classifier
def test_the_judge_still_refuses_after_wording(parts):
    res, _, _ = ask(parts, "Pudexiz Plast 2025 yilda chindan nechta bitim tuzgan?", lab(chindan="wording"),
                    seller_replies(), [verdict(False, "chindan")])
    assert not res.answered and res.source == "no_data"


@pytest.mark.max_safety
@pytest.mark.word_classifier
def test_a_concept_the_plan_does_not_use_refuses(parts):
    # «yetakchi» = top, but the plan only counts the deals: it does not answer whether the company leads
    res, llm, _ = ask(parts, "Pudexiz Plast 2025 yilda nechta bitim bilan yetakchi?", lab(yetakchi="top"),
                      seller_replies())
    assert not res.answered and res.source == "no_data" and "«yetakchi»" in res.answer
    assert llm.prompts == []                    # refused before the judge
    [c] = [t for t in res.trace if t.get("tool") == "concept_check"]
    assert c["unused"] == ["yetakchi -> top"]


@pytest.mark.max_safety
@pytest.mark.word_classifier
def test_a_concept_the_plan_uses_goes_to_the_judge(parts):
    top = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
           "group_by": ["seller"], "limit": 1,
           "mentions": [M("2025", "filter:year"), M("продавцов", "group_by:seller"), M("лидер", "limit")]}
    res, llm, _ = ask(parts, "Кто лидер по числу сделок среди продавцов в 2025?", lab(лидер="top"),
                      [call("run_plan", plan=top, assumptions=[])], [verdict(True)])
    assert res.answered, res.reason
    [j] = [t for t in res.trace if t.get("tool") == "judge"]
    assert j["args"]["words"] == ["лидер"]


@pytest.mark.max_safety
@pytest.mark.word_classifier
def test_no_unknown_words_no_classifier_call(parts):
    res, _, front = ask(parts, "Сколько продала Pudexiz Plast в 2025?", None, seller_replies(), [verdict(True)])
    assert res.answered and front.prompts == []
    assert wordclass.CURRENT.get() is None          # reset after the request


@pytest.mark.max_safety
@pytest.mark.word_classifier
def test_stage2_a_name_word_the_plan_drops_still_blocks(parts):
    # «Pudexiz» is a database name at stage 1 (not classified); the plan drops it: the noun gate refuses
    dropped = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
               "mentions": [M("2025", "filter:year")]}
    res, llm, _ = ask(parts, "Сколько сделок реально оформила Pudexiz Plast в 2025?",
                      lab(реально="wording", оформила="wording"), [call("run_plan", plan=dropped, assumptions=[])])
    assert not res.answered and "Pudexiz" in res.reason and llm.prompts == []


@pytest.mark.max_safety
def test_classifier_off_restores_the_noun_gate(parts):
    res, llm, front = ask(parts, "Pudexiz Plast 2025 yilda chindan nechta bitim tuzgan?", lab(chindan="wording"))
    assert res.source == "no_data" and "«chindan»" in res.answer and front.prompts == [] and llm.chat.sent == []


def test_word_classes_label_of():
    wc = WordClasses({"Ayting-chi": WORDING})
    assert wc.label_of("ayting-chi") == WORDING and wc.label_of("x") is None and wc.concept_words() == {}
