"""Checks that turn 'answered a nearby question' into a fix message: named days, per-period words, facts the
data does not have, buyer/seller role on the registry; and day-level compare periods."""
import datetime as dt

import pytest

from app.answer.plan_templates import render
from app.db import Database
from app import config
from app.plan.build import build_sql
from app.plan.checks import consistency_error, named_days, not_in_data_error
from tests.plan_helpers import D, prepare


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def err(plan, q):
    return consistency_error(prepare(plan, q), D, q)


@pytest.mark.parametrize("q", ["Курс доллара на 1 января 2025 и на 1 января 2026",
                               "01.01.2025 va 01.01.2026 dagi dollar kursi",
                               "2025 йил 1 январь ва 2026 йил 1 январдаги доллар курси"])
def test_named_days_in_three_languages(q):
    assert named_days(q) == {dt.date(2025, 1, 1), dt.date(2026, 1, 1)}


def test_month_compare_for_named_days_is_rejected_and_day_compare_passes():
    q = "Курс доллара на 1 января 2025 и на 1 января 2026 — на сколько процентов изменился?"
    month = {"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
             "compare": [{"year": 2025, "month_from": 1}, {"year": 2026, "month_from": 1}]}
    assert "day" in err(month, q)
    day = {"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
           "compare": [{"year": 2025, "month_from": 1, "day": 1}, {"year": 2026, "month_from": 1, "day": 1}]}
    assert err(day, q) is None


def test_day_filter_satisfies_named_day_and_month_filter_does_not():
    q = "курс доллара на 15.06.2025"
    assert err({"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
                "filters": [F("year", 2025), F("month", 6)]}, q)
    assert err({"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
                "filters": [F("rate_date", "2025-06-15")]}, q) is None


def test_day_compare_sql_and_answer():
    p = prepare({"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
                 "compare": [{"year": 2025, "month_from": 1, "day": 1}, {"year": 2026, "month_from": 1, "day": 1}]})
    res = Database(config.DB_PATH).query(build_sql(p, D, None).sql)
    rates = Database(config.DB_PATH).query(
        "SELECT rate FROM usd_rates WHERE rate_date IN ('2025-01-01', '2026-01-01') ORDER BY rate_date").rows
    row1, row2 = res.rows
    i = res.columns.index("avg_rate")
    assert (row1[i], row2[i]) == (rates[0][0], rates[1][0])
    assert row2[res.columns.index("change_pct")] == round(100 * (rates[1][0] / rates[0][0] - 1), 2)
    text = render(p, D, res.columns, res.rows, "ru")
    assert text.startswith("01.01.2025:") and "01.01.2026:" in text


def test_day_compare_needs_days_in_both_periods():
    from app.plan.model import PlanError
    with pytest.raises(PlanError):
        prepare({"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
                 "compare": [{"year": 2025, "month_from": 1, "day": 1}, {"year": 2026, "month_from": 1}]})


@pytest.mark.parametrize("q", ["Средневзвешенная цена хлопка-сырца за кг по годам с 2022 по 2026",
                               "Paxta xomashyosining narxi 2022–2026 yillar bo‘yicha qanday bo‘lgan?",
                               "хлопок-сырецнинг нархи 2022 дан 2026 гача йиллар бўйича"])
def test_per_year_words_need_group_by_year(q):
    base = {"subject": "deals", "measures": [{"agg": "price_per_kg"}],
            "filters": [F("product", "Хлопок-сырец"), F("year", [2022, 2026], "between")]}
    assert "group_by" in err(base, q)
    assert err({**base, "group_by": ["year"]}, q) is None


@pytest.mark.parametrize("q", ["сколько сотрудников в Linom Energo", "Linom Energo da nechta xodim ishlaydi?",
                               "Linom Energo да нечта ходим ишлайди?", "Кто директор Linom Energo?",
                               "Zoket Invest 2025 йилда қанча соф фойда олган?", "Телефон брокера BK005?"])
def test_facts_not_in_the_data(q):
    assert "declare_unanswerable" in not_in_data_error(q)


@pytest.mark.parametrize("q", ["Сколько сделок по хлопку в 2025", "Kontraktlar soni 2024 yilda",
                               "Топ-5 продавцов цемента", "Сколько контрактов действовало на 1 июня 2025"])
def test_ordinary_questions_are_not_flagged(q):
    assert not_in_data_error(q) is None


def test_registry_buyers_need_the_role_filter():
    q = "Reyestrdagi xaridor kompaniyalar faoliyat turi bo'yicha: eng ko'p 5 ta tur"
    base = {"subject": "companies", "measures": [{"agg": "count"}], "group_by": ["activity"], "limit": 5}
    assert "role" in err(base, q)
    assert err({**base, "filters": [F("role", "Покупатель")]}, q) is None


@pytest.mark.parametrize("q", ["Курс евро на 1 марта 2025?", "Средний курс рубля в 2024 году",
                               "2025 yil 1 mart kuni yevro kursi", "Какая доля урожая хлопка 2025 года в стране продана через биржу?",
                               "2025 йилда мамлакатда етиштирилган пахта ҳосилининг неча фоизи"])
def test_other_currencies_and_outside_facts(q):
    assert "declare_unanswerable" in not_in_data_error(q)


def test_status_breakdown_keeps_annulled():
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2026)],
                 "group_by": ["status"], "limit": 5}, "Сделки 2026 года по статусам: топ-5")
    assert p.scope == "all"


def test_weighted_price_needs_price_per_kg():
    q = "2025-yilning qaysi oyida dizel yoqilg‘isining 1 kg uchun o‘rtacha tortilgan narxi eng past bo‘lgan?"
    base = {"subject": "deals", "filters": [F("product", "Дизельное топливо"), F("year", 2025)],
            "group_by": ["month"], "order": "asc", "limit": 1}
    from app.plan.checks import plan_missing_condition_error as pm
    assert "price_per_kg" in pm(q, prepare({**base, "measures": [{"agg": "avg", "of": "deal_price_per_kg"}]}, q), D, [])
    assert pm(q, prepare({**base, "measures": [{"agg": "price_per_kg"}]}, q), D, []) is None


def test_without_annulled_is_scope_not_the_share_part():
    q = "2025-yilda birja aylanmasining (so‘mda) necha foizi eksportga to‘g‘ri keladi? Bekor qilinganlar hisobga olinmasin"
    bad = {"subject": "deals", "measures": [{"agg": "share", "of": "amount_uzs"}],
           "filters": [F("year", 2025), F("contract_type", "Экспортный"), {**F("cancelled", False), "numerator": True}]}
    assert "scope" in err(bad, q)


@pytest.mark.parametrize("q", ["2026 йил январь-май ойлари кесимида бензин савдоси", "har bir choragida qancha",
                               "в разрезе месяцев 2025"])
def test_wider_per_period_words(q):
    from app.plan.checks import _GRAIN_WORDS
    assert any(rx.search(q) for rx in _GRAIN_WORDS.values())


def test_deal_listing_has_payment_and_delivery_dates():
    from app.db import Database
    from app import config
    p = prepare({"subject": "deals", "filters": [F("deal", 412345)], "group_by": ["deal"]})
    res = Database(config.DB_PATH).query(build_sql(p, D, None).sql)
    assert {"payment_date", "delivery_date", "contract_id"} <= set(res.columns)
    text = render(p, D, res.columns, res.rows, "uz")
    assert text is not None and "to‘lov sanasi" in text.lower() or "to‘lov" in text
