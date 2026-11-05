"""Mention check (after "Mention Extraction and Linking for SQL Query Generation", EMNLP 2020): every condition of
the plan is linked to a phrase of the question, and every condition-like phrase of the question is used."""
import pytest

from app.agent.guard import build_index
from app.catalog import load_catalog
from app.plan.mentions import Resolution, mention_error
from tests.plan_helpers import D, prepare


@pytest.fixture(scope="module")
def index(db):
    return build_index(load_catalog(db))


@pytest.fixture(scope="module")
def catalog(db):
    return load_catalog(db)


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def M(text, used_as):
    return {"text": text, "used_as": used_as}


def err(plan, question, index, catalog=None, resolutions=(), names=()):
    p = prepare(plan, question, catalog)
    return mention_error(p, D, question, index, resolutions, names)


CEMENT = [Resolution("product", "цемент", frozenset({"Портландцемент"}))]
Q_TRANSPORT = "Сколько транспортных компаний купили цемент в 2024 году?"


def transport_plan(filters, mentions):
    return {"subject": "deals", "measures": [{"agg": "distinct", "of": "buyer"}],
            "filters": [F("product", "Портландцемент"), F("year", 2024), *filters], "mentions": mentions}


def test_dropped_activity_filter_is_an_error(index, catalog):
    plan = transport_plan([], [M("цемент", "filter:product"), M("2024 году", "filter:year"),
                               M("Сколько", "measure:0")])
    e = err(plan, Q_TRANSPORT, index, catalog, CEMENT)
    assert e is not None and "транспортных" in e


def test_activity_mentioned_but_not_filtered_is_an_error(index, catalog):
    plan = transport_plan([], [M("цемент", "filter:product"), M("2024 году", "filter:year"),
                               M("транспортных компаний", "measure:0")])
    e = err(plan, Q_TRANSPORT, index, catalog, CEMENT)
    assert e is not None and "транспортных компаний" in e and "buyer_activity" in e


def test_activity_filter_with_mention_passes(index, catalog):
    plan = transport_plan([F("buyer_activity", "Транспортная компания")],
                          [M("цемент", "filter:product"), M("2024 году", "filter:year"),
                           M("транспортных компаний", "filter:buyer_activity"), M("Сколько", "measure:0")])
    assert err(plan, Q_TRANSPORT, index, catalog, CEMENT) is None


Q_YEARS = "Сколько сделок было с 2022 по 2026 по годам?"


def test_year_range_turned_into_one_year_is_an_error(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2022)],
            "mentions": [M("с 2022 по 2026", "filter:year")]}
    e = err(plan, Q_YEARS, index)
    assert e is not None and "2026" in e


def test_year_dropped_from_mentions_is_an_error(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2022)],
            "mentions": [M("2022", "filter:year")]}
    e = err(plan, Q_YEARS, index)
    assert e is not None and "2026" in e


def test_year_range_by_years_passes(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", [2022, 2026], "between")],
            "group_by": ["year"],
            "mentions": [M("с 2022 по 2026", "filter:year"), M("по годам", "group_by:year")]}
    assert err(plan, Q_YEARS, index) is None


def test_group_by_needs_a_mention(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", [2022, 2026], "between")],
            "group_by": ["year"], "mentions": [M("с 2022 по 2026", "filter:year")]}
    e = err(plan, Q_YEARS, index)
    assert e is not None and "group_by year" in e


def test_mention_not_in_question_is_an_error(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", [2022, 2026], "between")],
            "group_by": ["year"],
            "mentions": [M("с 2022 по 2026", "filter:year"), M("по месяцам", "group_by:year")]}
    e = err(plan, Q_YEARS, index)
    assert e is not None and "по месяцам" in e and "not in the question" in e


def test_mention_pointing_to_nothing_is_an_error(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", [2022, 2026], "between")],
            "group_by": ["year"],
            "mentions": [M("с 2022 по 2026", "filter:year"), M("по годам", "group_by:month")]}
    e = err(plan, Q_YEARS, index)
    assert e is not None and "group_by:month" in e


def test_filter_without_mention_is_an_error(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024), F("contract_form", "Спот")],
            "mentions": [M("2024", "filter:year")]}
    e = err(plan, "Сколько сделок в 2024 году?", index)
    assert e is not None and "contract_form" in e


def test_uncovered_id_is_an_error(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024)],
            "mentions": [M("2024", "filter:year")]}
    e = err(plan, "Сколько сделок у компании 301234567 в 2024?", index)
    assert e is not None and "301234567" in e


def test_limit_number_must_match(index):
    plan = {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("year", 2025)],
            "group_by": ["seller"], "limit": 5,
            "mentions": [M("Топ-3", "limit"), M("продавцов", "group_by:seller"), M("2025", "filter:year"),
                         M("по сумме", "measure:0")]}
    e = err(plan, "Топ-3 продавцов по сумме за 2025 год", index)
    assert e is not None and "3" in e and "limit" in e
    plan["limit"] = 3
    assert err(plan, "Топ-3 продавцов по сумме за 2025 год", index) is None


PAXTA = [Resolution("product", "paxta", frozenset({"Хлопок-сырец"}))]
UZ_PLAN = {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}],
           "filters": [F("product", "Хлопок-сырец"), F("year", 2023)]}


@pytest.mark.parametrize("question, mentions", [
    ("2023-yilda paxta xom ashyosi qancha tonna sotilgan?",
     [M("2023-yilda", "filter:year"), M("paxta xom ashyosi", "filter:product"), M("qancha tonna", "measure:0")]),
    ("2023 йилда пахта хом ашёси қанча тонна сотилган?",
     [M("2023 йилда", "filter:year"), M("пахта хом ашёси", "filter:product"), M("қанча тонна", "measure:0")]),
    ("2023 йилда пахта хом ашёси қанча тонна сотилган?",      # Latin mentions of a Cyrillic question
     [M("2023 yilda", "filter:year"), M("paxta xom ashyosi", "filter:product"), M("qancha tonna", "measure:0")]),
    ("2023-yilda paxta xom ashyosi qancha tonna sotilgan?",   # case and whitespace
     [M("2023-YILDA", "filter:year"), M("Paxta  xom ashyosi", "filter:product"), M("Qancha tonna", "measure:0")]),
])
def test_uzbek_latin_and_cyrillic_pass(index, question, mentions):
    plan = {**UZ_PLAN, "mentions": mentions}
    assert err(plan, question, index, resolutions=PAXTA) is None


def test_uzbek_uncovered_year_is_an_error(index):
    plan = {**UZ_PLAN, "filters": [F("product", "Хлопок-сырец")],
            "mentions": [M("paxta xom ashyosi", "filter:product"), M("qancha tonna", "measure:0")]}
    e = err(plan, "2023-yilda paxta xom ashyosi qancha tonna sotilgan?", index, resolutions=PAXTA)
    assert e is not None and "2023" in e


def test_resolved_value_must_link_to_its_mention(index):
    # the product filter is linked to a phrase that resolve() did not turn into this product
    plan = {**UZ_PLAN, "mentions": [M("2023-yilda", "filter:year"), M("sotilgan", "filter:product"),
                                    M("paxta xom ashyosi qancha tonna", "measure:0")]}
    e = err(plan, "2023-yilda paxta xom ashyosi qancha tonna sotilgan?", index, resolutions=PAXTA)
    assert e is not None and "Хлопок-сырец" in e


def test_compare_years_must_match(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "compare": [{"year": 2022}, {"year": 2024}],
            "mentions": [M("в 2023 году по сравнению с 2022", "compare")]}
    e = err(plan, "Как изменилось число сделок в 2023 году по сравнению с 2022?", index)
    assert e is not None and "2023" in e
    plan["compare"] = [{"year": 2022}, {"year": 2023}]
    assert err(plan, "Как изменилось число сделок в 2023 году по сравнению с 2022?", index) is None


def test_clamped_year_range_passes(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", [2022, 2026], "between")],
            "mentions": [M("с 2020 по 2026", "filter:year")]}
    assert err(plan, "Сколько сделок с 2020 по 2026?", index) is None


def test_no_conditions_needs_no_mentions(index):
    plan = {"subject": "contracts", "measures": [{"agg": "avg", "of": "payment_term_days"}]}
    assert err(plan, "Shartnomalarda o‘rtacha to‘lov muddati necha kun?", index) is None


def test_all_problems_are_reported_at_once(index):
    plan = {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("year", 2024)],
            "group_by": ["product"], "limit": 5, "mentions": [M("товаров", "group_by:product")]}
    e = err(plan, "Топ-5 товаров по сумме за 2024 год", index)
    assert e.startswith("mentions: 1) ") and "2) " in e and "Топ-5" in e and "2024" in e


def test_used_as_spellings_and_roles_are_read_tolerantly(index):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2023)],
            "group_by": ["region"], "order": "asc", "limit": 1,
            "mentions": [M("2023", "filters:year"), M("по региону склада", "filter:region"),
                         M("меньше всего", "order")]}
    assert err(plan, "В каком регионе меньше всего сделок в 2023 году (по региону склада)?", index) is None


def test_inflected_product_links_to_its_value(index):
    plan = {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}],
            "filters": [F("product", "Хлопок-сырец"), F("year", 2025)],
            "mentions": [M("хлопка-сырца", "filter:product"), M("2025", "filter:year"), M("тонн", "measure:0")]}
    assert err(plan, "Сколько тонн хлопка-сырца продали в 2025?", index,
               resolutions=[Resolution("product", "Хлопок-сырец", frozenset({"Хлопок-сырец"}))]) is None
