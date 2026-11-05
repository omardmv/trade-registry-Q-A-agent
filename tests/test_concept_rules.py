"""Concept identity rules over a plan (app/plan/checks.py): the currency and the side the question names must be the
ones the plan computes."""
from app.plan.checks import consistency_error, currency_error, side_error
from app.plan.dictionary import load_dictionary
from app.plan.model import parse_plan

D = load_dictionary()


def P(**kw):
    return parse_plan(kw)


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


RATE = dict(subject="usd_rates", measures=[{"agg": "avg", "of": "rate"}], filters=[F("year", 2024)])
SUM_UZS = dict(subject="deals", measures=[{"agg": "sum", "of": "amount_uzs"}], filters=[F("year", 2024)])
SUM_USD = dict(subject="deals", measures=[{"agg": "sum", "of": "amount_usd"}], filters=[F("year", 2024)])


def test_dollar_rate_needs_the_dollar_or_a_bare_rate():
    assert currency_error(P(**RATE), "Средний курс доллара в 2024") is None
    assert currency_error(P(**RATE), "2024 yilda dollar kursi o'rtacha qancha?") is None
    assert currency_error(P(**RATE), "Курс на 15.03.2024") is None              # the data's only rate
    assert currency_error(P(**RATE), "Сколько стоило золото в среднем в 2024?") is not None
    assert currency_error(P(**RATE), "") is None


def test_money_in_the_currency_the_question_names():
    assert currency_error(P(**SUM_UZS), "Сумма сделок в долларах за 2024") is not None
    assert currency_error(P(**SUM_USD), "Сумма сделок в долларах за 2024") is None
    assert currency_error(P(**SUM_USD), "2024 yilda bitimlar summasi so‘mda qancha?") is not None
    assert currency_error(P(**SUM_USD), "2024 йилда битимлар суммаси сўмда қанча?") is not None
    assert currency_error(P(**SUM_UZS), "Сумма сделок в сумах за 2024") is None
    assert currency_error(P(**SUM_UZS), "Сумма сделок за 2024") is None           # nothing named: no rule
    both = dict(SUM_UZS, measures=[{"agg": "sum", "of": "amount_uzs"}, {"agg": "sum", "of": "amount_usd"}])
    assert currency_error(P(**both), "Сумма сделок в сумах и в долларах за 2024") is None
    # «сумма» (amount) is not «сум» (so‘m)
    assert currency_error(P(**SUM_USD), "Какая сумма сделок в долларах за 2024?") is None
    price = dict(subject="deals", measures=[{"agg": "price_per_kg"}], filters=[F("year", 2024)])
    assert currency_error(P(**price), "Средняя цена за кг в долларах в 2024") is not None


def test_usd_contracts_in_a_dollar_question_are_fine():
    plan = dict(SUM_UZS, filters=[F("year", 2024), F("currency", "USD")])
    assert currency_error(P(**plan), "Сумма сделок по контрактам в долларах за 2024") is None


def test_side_named_in_the_question_must_be_the_plan_side():
    sellers = dict(subject="deals", measures=[{"agg": "count"}], filters=[F("year", 2024)], group_by=["seller_broker"],
                   limit=3)
    assert side_error(P(**sellers), "Топ-3 брокера покупателей в 2024") is not None
    assert side_error(P(**sellers), "Топ-3 брокера продавцов в 2024") is None
    assert side_error(P(**sellers), "2024 yilda xaridorlar brokerlari orasida top-3") is not None
    assert side_error(P(**sellers), "Топ-3 брокера в 2024") is None                # no side named
    assert side_error(P(**sellers), "Брокеры продавцов, продававших покупателям из Ташкента") is None  # both
    buyers = dict(subject="deals", measures=[{"agg": "distinct", "of": "buyer"}], filters=[F("year", 2024)],
                  group_by=["seller_broker"], limit=3)
    assert side_error(P(**buyers), "Какой брокер обслужил больше всего покупателей в 2024") is None
    side = dict(subject="deals", measures=[{"agg": "count"}], filters=[F("year", 2024), F("broker_side", "seller")])
    assert side_error(P(**side), "Сколько сделок брокеры провели на стороне покупателя в 2024") is not None
    plain = dict(subject="deals", measures=[{"agg": "count"}], filters=[F("year", 2024)])
    assert side_error(P(**plain), "Сколько сделок у покупателей в 2024") is None    # no side field: no rule


def test_rules_run_in_the_consistency_check():
    assert "amount_usd" in consistency_error(P(**SUM_UZS), D, "Сумма сделок в долларах за 2024 год")
