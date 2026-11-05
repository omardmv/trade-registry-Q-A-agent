"""Template answers of the grammar planner: every shape, three languages, the answer checker passes by construction."""
import datetime as dt
import random
import time

import pytest

from app.answer import plan_templates
from app.answer.verifier import verify_answer
from app.resolve import Resolver
from tests.fakes import FakeLLM, call
from tests.plan_helpers import D, run_plan

LANGS = ("ru", "uz", "uz-Cyrl")


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


@pytest.fixture(scope="module")
def resolver(db):
    return Resolver(db)


@pytest.fixture(scope="module")
def ids(db):
    q = lambda sql: db.query(sql).rows[0][0]  # noqa: E731
    return {"broker": q("SELECT seller_broker FROM deals GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1"),
            "inn": q("SELECT buyer_inn FROM deals GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1"),
            "deal": q("SELECT MIN(deal_id) FROM deals"),
            "contract": q("SELECT contract_id FROM deals GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1")}


def shapes(ids):
    return {
        "scalar": {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024)]},
        "scalar_two": {"subject": "deals", "measures": [{"agg": "count"}, {"agg": "sum", "of": "amount_uzs"}],
                       "filters": [F("year", 2024)]},
        "ranking": {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("year", 2024)],
                    "group_by": ["seller"], "limit": 3},
        "ranking_two_by_role": {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"},
                                                                 {"agg": "sum", "of": "amount_uzs"}],
                                "filters": [F("product", "Хлопок-сырец")], "group_by": ["participant"], "limit": 3},
        "least": {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("year", 2022)],
                  "group_by": ["product"], "order": "asc", "limit": 1},
        "breakdown": {"subject": "contracts", "measures": [{"agg": "count"}], "group_by": ["contract_form"]},
        "trend_month": {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}], "filters": [F("year", 2024)],
                        "group_by": ["month"]},
        "trend_quarter": {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                          "group_by": ["quarter"]},
        "compare": {"subject": "deals", "measures": [{"agg": "count"}], "compare": [{"year": 2022}, {"year": 2023}]},
        "compare_months": {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                           "compare": [{"year": 2024, "month_from": 1, "month_to": 6},
                                       {"year": 2026, "month_from": 1, "month_to": 6}]},
        "share": {"subject": "deals", "measures": [{"agg": "share"}],
                  "filters": [{"field": "contract_type", "value": "Экспортный", "numerator": True}]},
        "distinct": {"subject": "deals", "measures": [{"agg": "distinct", "of": "buyer"}], "filters": [F("year", 2024)]},
        "record_extreme": {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                           "filters": [F("year", 2024)], "group_by": ["deal"], "limit": 1},
        "deal_lookup": {"subject": "deals", "filters": [F("deal", ids["deal"])], "group_by": ["deal"]},
        "company_lookup": {"subject": "companies", "filters": [F("company", ids["inn"])], "group_by": ["company"]},
        "broker_lookup": {"subject": "brokers", "filters": [F("broker", ids["broker"])], "group_by": ["broker"]},
        "contract_lookup": {"subject": "contracts", "filters": [F("contract", ids["contract"])],
                            "group_by": ["contract"]},
        "broker_side": {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("broker", ids["broker"])],
                        "group_by": ["broker_side"]},
        "per_unit": {"subject": "deals", "measures": [{"agg": "sum", "of": "volume"}], "filters": [F("year", 2024)]},
        "usd_extreme": {"subject": "usd_rates", "measures": [{"agg": "max", "of": "rate"}],
                        "filters": [F("year", 2025), F("month", 5)], "group_by": ["day"], "limit": 1},
        "usd_day": {"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
                    "filters": [F("rate_date", "2024-03-15")]},
        "price": {"subject": "deals", "measures": [{"agg": "price_per_kg"}, {"agg": "avg", "of": "deal_price_per_kg"}],
                  "filters": [F("product", "Сахар")]},
        "top_per_group": {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024)],
                          "group_by": ["region", "product"], "limit": 2},
    }


@pytest.fixture(scope="module")
def results(db, resolver, ids):
    out = {}
    for name, plan in shapes(ids).items():
        res, _, p = run_plan(db, resolver, plan)
        out[name] = (p, res.columns, res.rows[:200])
    return out


def check(text, rows):
    v = verify_answer(text, "", rows, row_count=len(rows))
    assert v.ok, (v.unsupported, text)


@pytest.mark.parametrize("lang", LANGS)
def test_every_shape_has_a_template_that_verifies(results, lang):
    for name, (p, columns, rows) in results.items():
        text = plan_templates.render(p, D, columns, rows, lang)
        assert text, name
        check(text, rows)


def test_shape_contents(results, ids):
    r = {n: plan_templates.render(p, D, c, rows, "ru") for n, (p, c, rows) in results.items()}
    assert r["scalar"].startswith("число сделок: ") and "(без аннулированных: " in r["scalar"]
    assert r["ranking"].startswith("1. «") and "(ИНН " in r["ranking"] and "\n3. «" in r["ranking"]
    assert "как покупатель" in r["ranking_two_by_role"] and "По показателю: объём в тоннах" in r["ranking_two_by_role"]
    assert "\n" not in r["least"] and r["least"].startswith("«")
    assert r["trend_month"].startswith("январь 2024 — ") and "декабрь 2024 — " in r["trend_month"]
    assert r["trend_quarter"].startswith("1 квартал 2025")
    assert r["compare"].startswith("2022: ") and "изменение: +" in r["compare"]
    assert "январь–июнь 2024" in r["compare_months"]
    assert r["share"].startswith("доля: ") and r["share"].endswith("%")
    assert "дата сделки: " in r["deal_lookup"] and str(ids["deal"]) in r["deal_lookup"]
    assert "первая сделка: " in r["company_lookup"] and "последняя сделка: " in r["company_lookup"]
    assert f"(код {ids['broker']})" in r["broker_lookup"] and "сделок на любой стороне" in r["broker_lookup"]
    assert "сделок без аннулированных" in r["contract_lookup"]
    assert "как брокер продавца — " in r["broker_side"] and "как брокер покупателя — " in r["broker_side"]
    assert r["usd_extreme"].count("\n") == 1 and "07.05.2025" in r["usd_extreme"]     # both tied days
    assert "килограмм" in r["per_unit"]


def test_uzbek_scripts(results):
    p, c, rows = results["scalar"]
    uz, cyr = (plan_templates.render(p, D, c, rows, lang) for lang in ("uz", "uz-Cyrl"))
    assert uz.startswith("bitimlar soni: ") and "bekor qilinganlarsiz" in uz
    assert cyr.startswith("битимлар сони: ") and "бекор қилинганларсиз" in cyr


def test_unusual_rows_fall_back(results):
    p, c, rows = results["ranking"]
    assert plan_templates.render(p, D, c + ["mystery"], [list(r) + [1] for r in rows], "ru") is None
    assert plan_templates.render(p, D, c, [], "ru") is None


# ---- property test: the checker passes on generated rows of every shape ----
_NAMES = ["«Ёлка» MChJ", "\"Qo‘qon Don\" AJ", "O'ZBEK TEKSTIL", "Альфа-2 Групп", "ООО «Рост»", "Sement 400",
          "Toshkent", "A", "Хлопок-сырец 1 сорт", "M00K"]


def _gen(col: str, v, rng: random.Random):
    if v is None or rng.random() < 0.05 and col not in ("year", "month", "quarter", "month_from", "month_to"):
        return None if col not in ("year", "month", "quarter", "month_from", "month_to", "role", "broker_side",
                                   "ranking", "unit") else v
    if isinstance(v, bool):
        return rng.random() < 0.5
    if col == "year":
        return rng.randint(2022, 2026)
    if col in ("month", "month_from", "month_to"):
        return v
    if col in ("quarter", "hour"):
        return v
    if isinstance(v, int):
        if col.endswith(("_id", "inn")):
            return v
        return rng.choice([0, 1, 7, 12, 999, 1000, 123456, 10 ** 9 + 7, -5])
    if isinstance(v, float):
        return rng.choice([0.0, 0.5, 1 / 3, 12.345, 99.995, 0.00123, 1234567.891, -2.5, 1e12 + 0.25])
    if isinstance(v, dt.date):
        return dt.date(2022, 1, 3) + dt.timedelta(days=rng.randint(0, 1700))
    if isinstance(v, str) and col not in ("role", "broker_side", "ranking", "unit", "currency") \
            and not v.isdigit() and not v.upper().startswith("BK"):
        return rng.choice(_NAMES)
    return v


def test_property_generated_rows_always_verify(results):
    rng = random.Random(20261105)
    for _ in range(40):
        for name, (p, columns, rows) in results.items():
            base = rows[: rng.randint(1, max(1, min(len(rows), 8)))]
            gen = [tuple(_gen(c, v, rng) for c, v in zip(columns, row)) for row in base]
            for lang in LANGS:
                text = plan_templates.render(p, D, columns, gen, lang)
                if text is None:
                    continue
                v = verify_answer(text, "", gen, row_count=len(gen))
                assert v.ok, (name, lang, v.unsupported, text, gen)


# ---- pipeline: grammar mode renders by code and never calls the composer ----
def test_grammar_pipeline_uses_templates_not_the_composer(db, resolver):
    from app.agent.grammar_writer import GrammarWriter
    from app.pipeline import Pipeline

    plan = {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("year", 2024)],
            "group_by": ["seller"], "limit": 3,
            "mentions": [{"text": "Топ-3", "used_as": "limit"}, {"text": "продавцов", "used_as": "group_by:seller"},
                         {"text": "2024", "used_as": "filter:year"}, {"text": "по сумме", "used_as": "measure:0"}]}
    writer = GrammarWriter(FakeLLM(chat_replies=[call("run_plan", plan=plan, assumptions=[])]), db, resolver)
    composer = FakeLLM()            # no completions scripted: any composer call would fail
    res = Pipeline(writer, composer).ask("Топ-3 продавцов по сумме за 2024 год")
    assert res.answered and res.source == "plan_template" and res.verified
    assert composer.prompts == []
    assert res.answer.startswith("1. «") and "Расчёт: сделки; за 2024 год" in res.answer
