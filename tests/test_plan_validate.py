"""Plan parsing and validation against the field dictionary: rejections list the allowed alternatives."""
import datetime as dt

import pytest

from app.catalog import load_catalog
from app.plan.model import PlanError, parse_plan
from app.plan.validate import grounding_pairs, validate_plan
from tests.plan_helpers import D


def V(plan, catalog=None):
    return validate_plan(parse_plan(plan), D, catalog)


def err(plan, catalog=None) -> str:
    with pytest.raises(PlanError) as e:
        V(plan, catalog)
    return str(e.value)


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


COUNT = [{"agg": "count"}]


def test_unknown_subject_lists_allowed():
    msg = err({"subject": "profits", "measures": COUNT})
    assert "deals" in msg and "usd_rates" in msg


def test_unknown_plan_key():
    assert "allowed" in err({"subject": "deals", "measures": COUNT, "kind": "x"})


def test_unknown_field_lists_fields():
    msg = err({"subject": "deals", "measures": COUNT, "filters": [F("profit", 1)]})
    assert "unknown field 'profit'" in msg and "tonnes" in msg and "buyer" in msg


def test_field_of_other_subject_rejected():
    assert "unknown field 'vat_payer'" in err({"subject": "deals", "measures": COUNT, "filters": [F("vat_payer", True)]})


def test_unknown_agg_lists_named_measures():
    msg = err({"subject": "deals", "measures": [{"agg": "profit"}]})
    assert "price_per_kg" in msg and "share" in msg


def test_agg_does_not_fit_field():
    msg = err({"subject": "usd_rates", "measures": [{"agg": "sum", "of": "rate"}]})
    assert "does not fit" in msg and "avg" in msg


def test_count_of_dimension_points_to_distinct():
    assert "distinct" in err({"subject": "deals", "measures": [{"agg": "count", "of": "buyer"}]})


def test_measure_name_used_as_field_is_explained():
    assert "is a measure" in err({"subject": "deals", "measures": COUNT, "group_by": ["price_per_kg"]})


def test_named_measure_via_of_is_accepted():
    p = V({"subject": "deals", "measures": [{"agg": "avg", "of": "price_per_kg"}]})
    assert p.measures[0].agg == "price_per_kg"


def test_op_must_fit_type():
    assert "allowed ops" in err({"subject": "deals", "measures": COUNT, "filters": [F("product", "x", ">=")]})
    assert "allowed ops" in err({"subject": "deals", "measures": COUNT, "filters": [F("cancelled", [True, False], "between")]})


def test_invalid_op():
    assert "invalid op" in err({"subject": "deals", "measures": COUNT, "filters": [F("year", 2024, "like")]})


def test_enum_value_lists_allowed():
    msg = err({"subject": "deals", "measures": COUNT, "filters": [F("contract_form", "Опцион")]})
    assert "Спот" in msg and "Форвард" in msg


def test_catalog_value_checked(db):
    cat = load_catalog(db)
    msg = err({"subject": "deals", "measures": COUNT, "filters": [F("category", "Космос")]}, cat)
    assert "unknown category value" in msg


def test_inn_and_broker_shapes():
    assert "9-digit INN" in err({"subject": "deals", "measures": COUNT, "filters": [F("buyer", "Agro Savdo")]})
    assert "BK001" in err({"subject": "deals", "measures": COUNT, "filters": [F("broker", "Naxon")]})
    p = V({"subject": "deals", "measures": COUNT, "filters": [F("broker", "bk005")]})
    assert p.filters[0].value == "BK005"


def test_values_are_typed():
    p = V({"subject": "deals", "measures": COUNT,
           "filters": [F("year", "2024"), F("cancelled", "false"), F("deal_date", "01.03.2024", ">="),
                       F("tonnes", "10,5", ">=")]})
    assert [f.value for f in p.filters] == [2024, False, dt.date(2024, 3, 1), 10.5]


def test_in_and_between_values():
    p = V({"subject": "deals", "measures": COUNT, "filters": [{"field": "year", "op": "between", "values": [2022, 2024]},
                                                              {"field": "region", "op": "in", "values": ["A"]}]})
    assert p.filters[0].value == (2022, 2024) and p.filters[1].op == "="
    assert "first value" in err({"subject": "deals", "measures": COUNT, "filters": [F("year", [2025, 2022], "between")]})


def test_year_outside_data_is_unanswerable():
    assert "declare_unanswerable" in err({"subject": "deals", "measures": COUNT, "filters": [F("year", 2021)]})


def test_year_range_partly_outside_is_clamped():
    msg = err({"subject": "deals", "measures": COUNT, "filters": [F("year", [2020, 2025], "between")]})
    assert "clamp" in msg and "[2022, 2025]" in msg


def test_month_after_data_end():
    assert "declare_unanswerable" in err({"subject": "deals", "measures": COUNT,
                                          "filters": [F("year", 2026), F("month", 12)]})
    assert "end the month filter at 10" in err({"subject": "deals", "measures": COUNT,
                                                "filters": [F("year", 2026), F("month", [9, 12], "between")]})


def test_dates_outside_data():
    assert "clamp" in err({"subject": "deals", "measures": COUNT,
                           "filters": [F("deal_date", ["2021-12-01", "2022-02-01"], "between")]})
    assert "declare_unanswerable" in err({"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
                                          "filters": [F("rate_date", "2027-01-01")]})


def test_month_needs_year():
    assert "needs a year" in err({"subject": "deals", "measures": COUNT, "filters": [F("month", 3)]})


def test_month_and_quarter_ranges():
    assert "1..12" in err({"subject": "deals", "measures": COUNT, "filters": [F("year", 2024), F("month", 13)]})
    assert "1..4" in err({"subject": "deals", "measures": COUNT, "filters": [F("year", 2024), F("quarter", 5)]})


def test_compare_rules():
    assert "exactly two" in err({"subject": "deals", "measures": COUNT, "compare": [{"year": 2024}]})
    assert "same length" in err({"subject": "deals", "measures": COUNT,
                                 "compare": [{"year": 2024}, {"year": 2025, "quarter": 1}]})
    assert "month_to 10" in err({"subject": "deals", "measures": COUNT, "compare": [{"year": 2025}, {"year": 2026}]})
    assert "only in compare" in err({"subject": "deals", "measures": COUNT, "filters": [F("year", 2024)],
                                     "compare": [{"year": 2023}, {"year": 2024}]})
    assert "without group_by" in err({"subject": "deals", "measures": COUNT, "group_by": ["product"],
                                      "compare": [{"year": 2023}, {"year": 2024}]})
    assert "dated subject" in err({"subject": "companies", "measures": COUNT,
                                   "compare": [{"year": 2023}, {"year": 2024}]})


def test_share_needs_where_and_summable_of():
    assert "numerator:true" in err({"subject": "deals", "measures": [{"agg": "share"}]})
    assert "summable" in err({"subject": "deals", "measures": [{"agg": "share", "of": "deal_price_per_kg",
                                                                "where": [F("product", "Сахар")]}]})
    assert "only used with agg share" in err({"subject": "deals", "measures": [{"agg": "count", "where": [F("year", 2024)]}]})


def test_group_by_rules():
    assert "cannot group_by tonnes" in err({"subject": "deals", "measures": COUNT, "group_by": ["tonnes"]})
    assert "at most 2" in err({"subject": "deals", "measures": COUNT, "group_by": ["year", "month", "product"]})
    assert "either-side" in err({"subject": "deals", "measures": COUNT, "group_by": ["broker", "participant"]})


def test_measures_required_except_record_listing():
    assert "measures needs" in err({"subject": "deals", "group_by": ["product"]})
    assert V({"subject": "deals", "filters": [F("deal", "387830")], "group_by": ["deal"]}).measures == ()
    assert "at most 2 measures" in err({"subject": "deals", "measures": COUNT * 3})


def test_order_and_limit_need_group_by():
    assert "rankings only" in err({"subject": "deals", "measures": COUNT, "order": "asc"})
    assert "rankings only" in err({"subject": "deals", "measures": COUNT, "limit": 3})
    assert "limit must be" in err({"subject": "deals", "measures": COUNT, "group_by": ["product"], "limit": 500})


def test_variant_takes_only_equals():
    assert "allowed ops: =" in err({"subject": "deals", "measures": COUNT,
                                    "filters": [{"field": "product_variant", "op": "in", "values": ["a", "b"]}]})


def test_grounding_pairs_cover_filters_and_share_where():
    p = V({"subject": "deals", "measures": [{"agg": "share", "where": [F("product", "Сахар")]}],
           "filters": [F("buyer", "301234567"), F("broker", "BK001"), F("region", "Toshkent shahri"),
                       F("product_variant", "АИ-92"), F("deal", "387830"), F("category", "x")]})
    assert grounding_pairs(p, D) == {("product", "Сахар"), ("inn", "301234567"), ("broker", "BK001"),
                                     ("region", "Toshkent shahri"), ("product_variant_query", "АИ-92"),
                                     ("deal", "387830")}


def test_value_and_values_are_merged():
    p = V({"subject": "deals", "measures": COUNT,
           "filters": [{"field": "deal_date", "op": "between", "value": "2025-10-01", "values": ["2025-10-31"]}]})
    assert p.filters[0].value == (dt.date(2025, 10, 1), dt.date(2025, 10, 31))


def test_numerator_flag_moves_filter_into_share():
    p = V({"subject": "deals", "measures": [{"agg": "share", "of": "amount_uzs"}],
           "filters": [{"field": "product", "value": "Сахар", "numerator": True}, F("year", 2024)]})
    assert [f.field for f in p.filters] == ["year"] and p.measures[0].where[0].value == "Сахар"
    assert "only used with the share" in err({"subject": "deals", "measures": COUNT,
                                              "filters": [{"field": "year", "value": 2024, "numerator": True}]})


def test_zero_limit_means_no_limit():
    assert V({"subject": "deals", "measures": COUNT, "group_by": ["year"], "limit": 0}).limit is None
