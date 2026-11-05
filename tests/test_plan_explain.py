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


def test_lookup_states_the_key_and_what_it_shows():
    text = ex({"subject": "deals", "filters": [F("deal", "387830")], "group_by": ["deal"]})
    assert text.startswith("Расчёт: сделки; сделка: 387830; показано: сделка, дата сделки, товар,")
    assert "название (продавец)" in text and "сумма в сумах" in text and "за весь период" not in text


def test_lookup_of_a_broker_lists_name_inn_and_region():
    # fresh set 2 g036 «название и область» was refused by the judge: the calculation did not say what is shown
    text = ex({"subject": "brokers", "filters": [F("broker", "BK068")], "group_by": ["broker"]})
    assert text.startswith("Расчёт: брокеры; брокер: BK068; показано: название (брокер), ИНН, регион,")
    uz = ex({"subject": "brokers", "filters": [F("broker", "BK068")], "group_by": ["broker"]}, "uz-Cyrl")
    assert "кўрсатилади: брокер номи, СТИР, ҳудуд," in uz


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


def test_uzbek_cyrillic_sentence_has_no_latin_words_but_keeps_values():
    import re
    text = ex({"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}],
               "filters": [F("product", "Cotton Lint Export"), F("year", 2024)], "group_by": ["buyer"], "limit": 3},
              "uz-Cyrl")
    assert text.startswith("Ҳисоб: битимлар; 2024-йил")
    assert "Cotton Lint Export" in text                          # data values are never transliterated
    assert not re.search(r"[A-Za-z]", text.replace("Cotton Lint Export", "")), text


def test_extreme_ranking_label_states_the_direction():
    """h041: a ranking by a minimum states which end it shows (it was «минимум; топ-1» over the highest rate)."""
    plan = {"subject": "usd_rates", "measures": [{"agg": "min", "of": "rate"}], "filters": [F("year", 2025)],
            "group_by": ["day"], "limit": 1, "order": "desc"}
    assert "минимум" in ex(plan, "ru", "Когда в 2025 году доллар был самым дешёвым?")
    assert "по возрастанию (наименьшие значения)" in ex(plan, "ru", "Когда в 2025 году доллар был самым дешёвым?")
    assert "o‘sish tartibida" in ex(plan, "uz", "2025-yilda dollar qaysi kuni eng arzon bo‘lgan?")
    assert "ўсиш тартибида" in ex(plan, "uz-Cyrl", "2025 йилда доллар қайси куни энг арзон бўлган?")
    # «самый высокий минимум» keeps desc, and the label says the largest values are shown
    month = {**plan, "group_by": ["month"]}
    assert "по убыванию (наибольшие значения)" in ex(month, "ru", "в каком месяце минимальный курс был самым высоким")
