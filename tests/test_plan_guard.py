"""The legacy missing-condition guard on grammar plans, and the period-value check."""
import time

import pytest

from app.agent.grammar_writer import GrammarWriter
from app.agent.guard import build_index
from app.catalog import load_catalog, load_product_attrs
from app.plan.checks import consistency_error, plan_missing_condition_error
from app.resolve import Resolver
from tests.fakes import FakeLLM, call
from tests.plan_helpers import D, prepare


@pytest.fixture(scope="module")
def ctx(db):
    catalog = load_catalog(db)
    return build_index(catalog), load_product_attrs(db), catalog


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def guard(ctx, plan, q):
    index, attrs, catalog = ctx
    return plan_missing_condition_error(q, prepare(plan, q, catalog), D, index, (), attrs)


CASES = [
    ("Сколько экспортных сделок в 2024 году?",
     {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024)]},
     {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024), F("contract_type", "Экспортный")]},
     "Экспортный"),
    ("Какой товар продан на наименьшую сумму в 2023 году?",
     {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("year", 2023)],
      "group_by": ["product"], "limit": 1},
     {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("year", 2023)],
      "group_by": ["product"], "limit": 1, "order": "asc"},
     "asc"),
    ("Сколько тонн хлопка-сырца 1 сорт продано?",
     {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}], "filters": [F("product", "Хлопок-сырец")]},
     {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}],
      "filters": [F("product_variant", "хлопка-сырца 1 сорт")]},
     "product_variant"),
    ("Какая доля хлопка-сырца по сумме сделок?",
     {"subject": "deals", "measures": [{"agg": "share"}],
      "filters": [{"field": "product", "value": "Хлопок-сырец", "numerator": True}]},
     {"subject": "deals", "measures": [{"agg": "share", "of": "amount_uzs"}],
      "filters": [{"field": "product", "value": "Хлопок-сырец", "numerator": True}]},
     "amount_uzs"),
    ("Сколько разных покупателей купили сахар?",
     {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("product", "Сахар")], "group_by": ["buyer"]},
     {"subject": "deals", "measures": [{"agg": "distinct", "of": "buyer"}], "filters": [F("product", "Сахар")]},
     "distinct"),
    ("Сумма сделок категории Нефтепродукты в 2025 году",
     {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}], "filters": [F("year", 2025)]},
     {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
      "filters": [F("year", 2025), F("category", "Нефтепродукты")]},
     "Нефтепродукты"),
    ("Сколько форвардных контрактов зарегистрировано?",
     {"subject": "contracts", "measures": [{"agg": "count"}]},
     {"subject": "contracts", "measures": [{"agg": "count"}], "filters": [F("contract_form", "Форвард")]},
     "Форвард"),
    ("Какой процент сделок экспортный?",
     {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("contract_type", "Экспортный")]},
     {"subject": "deals", "measures": [{"agg": "share"}],
      "filters": [{"field": "contract_type", "value": "Экспортный", "numerator": True}]},
     "share"),
]


@pytest.mark.parametrize("q, bad, good, needle", CASES, ids=[c[3] for c in CASES])
def test_guard_on_plans(ctx, q, bad, good, needle):
    e = guard(ctx, bad, q)
    assert e is not None and needle in e
    assert guard(ctx, good, q) is None


def test_guard_error_blocks_submit_sql(db):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024)],
            "mentions": [{"text": "2024", "used_as": "filter:year"}]}
    sql = "SELECT COUNT(*) AS n FROM deals WHERE year = 2024"
    llm = FakeLLM(chat_replies=[call("run_plan", plan=plan, assumptions=[]),
                                call("submit_sql", sql=sql, splits=[], assumptions=[]),
                                call("declare_unanswerable", reason="x")])
    writer = GrammarWriter(llm, db, Resolver(db))
    res = writer.run("Сколько экспортных сделок в 2024 году?", "ru", time.monotonic() + 60)
    tools = [p for kind, p in llm.chat.sent if kind == "tools"]
    assert "Экспортный" in tools[0][0][1]["error"]
    assert "not allowed after run_plan" in tools[1][0][1]["error"]
    assert res.status == "unanswerable"


@pytest.mark.parametrize("q, filters, compare, bad", [
    ("Сколько сделок с 2022 по 2026?", [F("year", 2022)], None, True),
    ("Сколько сделок с 2022 по 2026?", [F("year", [2022, 2026], "between")], None, False),
    ("Сколько сделок в марте 2024?", [F("year", 2024), F("month", 4)], None, True),
    ("Сколько сделок в марте 2024?", [F("year", 2024), F("month", 3)], None, False),
    ("Сколько сделок с января по март 2024?", [F("year", 2024), F("month", [1, 3], "between")], None, False),
    ("Сравни март 2024 и март 2025", [], [{"year": 2024, "month_from": 4}, {"year": 2025, "month_from": 4}], True),
    ("Сравни март 2024 и март 2025", [], [{"year": 2024, "month_from": 3}, {"year": 2025, "month_from": 3}], False),
])
def test_period_values(q, filters, compare, bad):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": filters}
    if compare:
        plan["compare"] = compare
    e = consistency_error(prepare(plan, q), D, q)
    assert (e is not None) == bad, e


def test_share_numerator_on_the_grouped_field_is_an_error():
    plan = {"subject": "deals", "measures": [{"agg": "share"}], "group_by": ["contract_type"],
            "filters": [{"field": "contract_type", "value": "Экспортный", "numerator": True}]}
    e = consistency_error(prepare(plan, "доля экспорта по типам"), D, "доля экспорта по типам")
    assert e is not None and "0% or 100%" in e
