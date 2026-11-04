from types import SimpleNamespace

from eval.v1_bench import gold_in_answer, score


def res(answer, answered=True):
    return SimpleNamespace(answer=answer, answered=answered)


def test_iso_gold_date_matches_dotted_answer():
    assert gold_in_answer("2026-09-22", "Сделка 22.09.2026 на сумму 13 500,03.")
    assert gold_in_answer("2026-09-22", "Сделка 2026-09-22.")
    assert not gold_in_answer("2026-09-22", "Сделка 21.09.2026.")
    assert not gold_in_answer("2026-09-22", "Сделка 22.09.2025.")


def test_score_with_iso_date_and_number():
    q = {"kind": "lookup", "gold": [["2026-09-22", 13500.03]]}
    assert score(q, res("Самая дорогая — 22.09.2026, цена 13 500,03 сум."))
    assert not score(q, res("Самая дорогая — 22.09.2026."))  # other gold columns are still required


def test_non_date_gold_unchanged():
    assert gold_in_answer(5854, "Сделок: 5 854.")
    assert not gold_in_answer("Карбамид", "Хлопок-сырец")
