"""Judge plumbing (app/plan/judge.py): prompt, verdict parsing, the term named in the refusal."""
import time

from app.plan.judge import EXAMPLES, SYSTEM, Verdict, build_prompt, judge, parse_verdict, term_for
from tests.fakes import FakeLLM


def test_few_shots_cover_both_sides_in_three_scripts():
    assert 4 <= len(EXAMPLES) <= 6
    assert {e[3]["covered"] for e in EXAMPLES} == {True, False}
    cyr = [q for q, *_ in EXAMPLES if any("Ѐ" <= ch <= "ӿ" for ch in q)]
    assert any("ў" in q or "қ" in q or "ҳ" in q for q in cyr)                      # Uzbek Cyrillic
    assert any(not any("Ѐ" <= ch <= "ӿ" for ch in q) for q, *_ in EXAMPLES)  # Uzbek Latin
    assert "covered" in SYSTEM and "wording" in SYSTEM


def test_prompt_has_the_case_last():
    p = build_prompt("Q?", "Расчёт: сделки.", ["foo", "bar"])
    assert p.endswith("Question: Q?\nCalculation: Расчёт: сделки.\nWords not in the vocabulary: foo, bar\nAnswer:")


def test_parse_verdict():
    assert parse_verdict('{"covered": true, "missing": ""}') == Verdict(True, "")
    assert parse_verdict('Sure: {"covered": false, "missing": "евро"} done') == Verdict(False, "евро")
    assert parse_verdict('```json\n{"covered": false}\n```') == Verdict(False, "")
    assert parse_verdict('{"covered": "false"}').covered is None
    assert parse_verdict("no json").covered is None


def test_judge_calls_the_llm_once_and_survives_errors():
    llm = FakeLLM(completions=['{"covered": true, "missing": ""}'])
    assert judge(llm, "Q", "C", ["w"], time.monotonic() + 60, 15).covered is True and len(llm.prompts) == 1
    bad = FakeLLM()
    bad.complete = lambda s, p: (_ for _ in ()).throw(RuntimeError("x"))
    v = judge(bad, "Q", "C", ["w"], time.monotonic() + 60, 15)
    assert v.covered is None and "RuntimeError" in v.error
    assert judge(llm, "Q", "C", ["w"], time.monotonic() + 5, 15).error == "timeout"


def test_term_is_the_users_words():
    q = "Сколько сделок в кредит было в 2024?"
    assert term_for(Verdict(False, "в кредит"), q, ["кредит"]) == "в кредит"
    assert term_for(Verdict(False, "«В кредит»"), q, ["кредит"]) == "В кредит"
    assert term_for(Verdict(False, "loans"), q, ["кредит"]) == "кредит"
    assert term_for(Verdict(False, ""), q, ["кредит"]) == "кредит"
