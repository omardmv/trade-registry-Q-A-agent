"""Generic consistency checks and business defaults of the grammar planner."""
from app.plan.checks import DEFAULT_LIMIT, apply_defaults, consistency_error
from tests.plan_helpers import D, prepare


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def check(plan, q=""):
    return consistency_error(prepare(plan, q), D, q)


def test_grouped_field_filtered_to_one_value_with_limit():
    msg = check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("product", "Сахар")],
                 "group_by": ["product"], "limit": 3})
    assert "one row" in msg
    assert check({"subject": "deals", "measures": [{"agg": "count"}],
                  "filters": [{"field": "product", "op": "in", "values": ["Сахар", "Пшеница"]}],
                  "group_by": ["product"], "limit": 3}) is None


def test_single_year_with_group_by_year():
    assert "one row" in check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                               "group_by": ["year"]}, "по годам 2025")
    assert check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                  "group_by": ["month"]}, "по месяцам 2025") is None


def test_distinct_of_grouped_field():
    assert "always 1" in check({"subject": "deals", "measures": [{"agg": "distinct", "of": "seller"}],
                                "group_by": ["seller"]})


def test_share_numerator_equals_scope():
    msg = check({"subject": "deals", "measures": [{"agg": "share", "where": [F("product", "Сахар")]}],
                 "filters": [F("product", "Сахар")]})
    assert "100%" in msg


def test_default_limit_for_large_grouping():
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["buyer"]})
    assert p.limit == DEFAULT_LIMIT and p.default_limit
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["contract_form"]})
    assert p.limit is None
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["month"],
                 "filters": [F("year", 2024)]})
    assert p.limit is None
    p = prepare({"subject": "deals", "filters": [F("deal", "387830")], "group_by": ["deal"]})
    assert p.limit is None and p.scope == "all"


def test_status_defaults():
    base = {"subject": "deals", "measures": [{"agg": "count"}]}
    assert prepare(base).scope == "split"
    assert prepare({**base, "group_by": ["region"]}).scope == "valid"
    assert prepare({**base, "filters": [F("status", "Исполнен")]}, "исполненные сделки").scope == "all"
    assert prepare({"subject": "deals", "measures": [{"agg": "cancelled_share"}]}).scope == "all"
    # a "without annulled" filter the question does not ask for falls back to the split view
    p = prepare({**base, "filters": [F("cancelled", False)]}, "Сколько сделок в 2024?")
    assert p.scope == "split" and p.filters == ()
    p = prepare({**base, "filters": [F("cancelled", False)]}, "Сколько сделок без аннулированных?")
    assert p.scope == "all" and p.filters[0].field == "cancelled"
    # other status filters are kept even without a status word
    p = prepare({**base, "filters": [F("status", "Новый")]}, "Сколько новых сделок?")
    assert p.scope == "all" and p.filters
    assert prepare({"subject": "contracts", "measures": [{"agg": "count"}]}).scope == ""


def test_period_named_in_question_must_be_in_plan():
    q = "Сколько сделок было в октябре 2025?"
    msg = check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)]}, q)
    assert "month/quarter" in msg
    assert check({"subject": "deals", "measures": [{"agg": "count"}],
                  "filters": [F("year", 2025), F("month", 10)]}, q) is None
    msg = check({"subject": "deals", "measures": [{"agg": "count"}]}, "Сколько сделок было 12.03.2024?")
    assert "12.03.2024" in msg
    msg = check({"subject": "deals", "measures": [{"agg": "count"}]}, "Сколько сделок в 2024 году?")
    assert "year 2024" in msg
    assert check({"subject": "deals", "measures": [{"agg": "count"}], "compare": [{"year": 2023}, {"year": 2024}]},
                 "2023 и 2024") is None
    assert check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                  "group_by": ["quarter"]}, "В каком квартале 2025?") is None


def test_year_with_undated_subject_points_to_deals():
    msg = check({"subject": "companies", "measures": [{"agg": "count"}]}, "Сколько компаний покупали в 2024?")
    assert "subject deals" in msg


def test_apply_defaults_is_pure():
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["buyer"]})
    assert apply_defaults(p, D, "") == p


def test_record_grouping_with_overall_aggregate():
    msg = check({"subject": "deals", "measures": [{"agg": "median", "of": "deal_price_per_kg"}],
                 "filters": [F("product", "Пшеница")], "group_by": ["deal"]})
    assert "drop group_by" in msg
    assert check({"subject": "deals", "measures": [{"agg": "max", "of": "amount_uzs"}], "group_by": ["deal"],
                  "limit": 1}) is None


# ---- fresh-set fixes ----
def test_uzbek_marta_is_times_not_march():
    """«бир марта» (once), «охирги марта» (the last time), «oxirgi marta» are not March (fresh f012, f034)."""
    plan = {"subject": "deals", "measures": [{"agg": "distinct", "of": "buyer"}], "filters": [F("year", 2025)]}
    for q in ("2025 йилда мис катодни сотиб олган харидорлар сони нечта (ҳар бир компания бир марта)?",
              "2025 yilda nechta xaridor bir marta sotib olgan?",
              "2025 йилда энг охирги марта қачон харид қилган?"):
        assert check(plan, q) is None, q
    # March itself is still a month in all three languages
    for q in ("сделки 8 марта 2025", "2025 yil mart oyida nechta xaridor", "2025 йил мартда нечта харидор"):
        assert "month" in (check(plan, q) or "") or "day" in (check(plan, q) or ""), q


def test_all_statuses_word_makes_grouped_scope_all():
    """«все статусы» / «barcha holatlar» / «барча ҳолатлар» / «включая аннулированные»: every state counts also in
    breakdowns, rankings and trends (fresh f039); without such words the grouped default stays without annulled."""
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2022, "between")],
            "group_by": ["year"]}
    plan["filters"] = [{"field": "year", "op": "between", "values": [2022, 2026]}]
    for q in ("сколько сделок по годам с 2022 по 2026, все статусы", "сделки по годам включая аннулированные",
              "yillar bo‘yicha bitimlar, barcha holatlar", "hamma holatlar bilan yillar bo‘yicha bitimlar",
              "ҳар йили нечтадан битим (2022–2026, барча ҳолатлар)?", "бекор қилинганлар билан ҳар йили нечта битим"):
        assert prepare(plan, q).scope == "all", q
    assert prepare(plan, "сколько сделок по годам с 2022 по 2026").scope == "valid"
    assert prepare(plan, "сделки по годам без аннулированных").scope == "valid"
    # a "without annulled" filter the planner added is dropped when the question asks for all statuses
    p = prepare({**plan, "filters": plan["filters"] + [F("cancelled", False)]}, "по годам, все статусы")
    assert p.scope == "all" and not p.filters_on("cancelled")
