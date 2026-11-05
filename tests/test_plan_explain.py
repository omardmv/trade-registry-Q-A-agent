"""Generic explanation sentences of grammar plans (RU and UZ)."""
from app.plan.build import build_sql
from app.plan.explain import explain_plan
from tests.plan_helpers import D, prepare


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def ex(plan, lang="ru", q="", variants=None):
    p = prepare(plan, q)
    return explain_plan(p, D, build_sql(p, D, variants).notes, lang)


def test_ranking_sentence():
    text = ex({"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}, {"agg": "sum", "of": "amount_uzs"}],
               "filters": [F("product", "Хлопок-сырец"), F("year", [2022, 2026], "between")],
               "group_by": ["buyer"], "limit": 3})
    assert text.startswith("Расчёт: сделки; за 2022–2026 годы (данные до 30.10.2026); товар: Хлопок-сырец")
    assert "по полю: покупатель" in text and "топ-3" in text and "без аннулированных" in text
    assert "в сумах" in text


def test_uzbek_sentence():
    text = ex({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025), F("month", 10)]}, "uz")
    assert text.startswith("Hisob: bitimlar; 10.2025;") and "alohida bekor qilinganlarsiz" in text


def test_all_time_and_default_limit():
    text = ex({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["seller"]})
    assert "за весь период (03.01.2022–30.10.2026)" in text and "показаны первые 10" in text


def test_data_end_note_for_2026():
    assert "(данные до 30.10.2026)" in ex({"subject": "deals", "measures": [{"agg": "count"}],
                                           "filters": [F("year", 2026)]})
    assert "данные до" not in ex({"subject": "deals", "measures": [{"agg": "count"}],
                                  "filters": [F("year", 2026), F("month", 3)]})
    assert "(данные до 30.10.2026)" in ex({"subject": "deals", "measures": [{"agg": "count"}],
                                           "compare": [{"year": 2025, "quarter": 1}, {"year": 2026, "quarter": 1}]})


def test_share_and_asc():
    text = ex({"subject": "deals", "measures": [{"agg": "share", "where": [F("contract_type", "Экспортный")]}],
               "filters": [F("year", 2024)]})
    assert "доля (по числу), где тип контракта: Экспортный" in text and "включая аннулированные" in text
    assert "без аннулированных сделок" not in text
    text = ex({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2023)],
               "group_by": ["region"], "order": "asc", "limit": 1})
    assert "по возрастанию" in text


def test_lookup_is_short():
    assert ex({"subject": "deals", "filters": [F("deal", "387830")], "group_by": ["deal"]}) == \
        "Расчёт: сделки; сделка: 387830."


def test_other_subjects():
    assert ex({"subject": "usd_rates", "measures": [{"agg": "max", "of": "rate"}], "filters": [F("year", 2024)],
               "group_by": ["day"], "limit": 1}).startswith("Расчёт: курс доллара; за 2024 год; максимум: курс")
    text = ex({"subject": "companies", "measures": [{"agg": "count"}], "filters": [F("vat_payer", True)]}, "uz")
    assert text == "Hisob: kompaniyalar (ishtirokchilar); QQS to‘lovchi: ha; kompaniyalar soni."
    assert "за 2024 год" in ex({"subject": "contracts", "measures": [{"agg": "count"}], "filters": [F("year", 2024)]})


def test_variant_spelling_count(db):
    from app.resolve import Resolver
    r = Resolver(db)
    text = ex({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("product_variant", "АИ-92")]},
              variants=r.variant_names)
    assert f"вариант названия товара: АИ-92 ({len(r.variant_names('АИ-92'))} написаний)" in text


def test_time_filters_not_in_the_period_phrase_are_listed():
    text = ex({"subject": "deals", "measures": [{"agg": "count"}],
               "filters": [F("year", 2024), F("month", [1, 2, 3], "in")]})
    assert "за 2024 год" in text and "месяц: 1, 2, 3" in text
    text = ex({"subject": "deals", "measures": [{"agg": "count"}],
               "filters": [F("year", [2023, 2024], "between"), F("quarter", 2)]})
    assert "2023–2024" in text and "квартал: 2" in text
