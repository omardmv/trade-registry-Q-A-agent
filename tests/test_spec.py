import datetime as dt

import pytest

from app.compiler.spec import (DealLookup, DealsAggregate, RegistryCount, SpecError, UsdRate,
                               entity_values, parse_spec)


def test_parse_aggregate_defaults_and_month_fill():
    s = parse_spec({"kind": "deals_aggregate", "measures": ["deal_count"],
                    "filters": {"year": 2025, "month_from": 10}})
    assert isinstance(s, DealsAggregate)
    assert s.filters.month_from == s.filters.month_to == 10
    assert s.status_scope == "split"
    assert s.group_by is None


def test_grouped_default_top_is_3():
    s = parse_spec({"kind": "deals_aggregate", "measures": ["volume_tonnes"], "group_by": "buyer",
                    "filters": {"products": ["Хлопок-сырец"]}})
    assert s.top == 3


@pytest.mark.parametrize("bad, message", [
    ({"measures": ["profit"]}, "measure"),
    ({"measures": []}, "measure"),
    ({"measures": ["deal_count", "amount_uzs", "amount_usd"]}, "at most 2"),
    ({"measures": ["deal_count"], "group_by": "ceo"}, "group_by"),
    ({"measures": ["deal_count"], "filters": {"year": 2021}}, "outside"),
    ({"measures": ["deal_count"], "filters": {"year": 2026, "month_from": 12}}, "outside"),
    ({"measures": ["deal_count"], "filters": {"buyer_inns": ["12345"]}}, "INN"),
    ({"measures": ["deal_count"], "filters": {"brokers": ["X1"]}}, "broker"),
    ({"measures": ["deal_count"], "filters": {"statuses": ["Done"]}}, "status"),
    ({"measures": ["deal_count"], "filters": {"date_from": "2021-12-01"}}, "clamp date_from/date_to"),
])
def test_invalid_aggregate(bad, message):
    with pytest.raises(SpecError, match=message):
        parse_spec({"kind": "deals_aggregate", **bad})


def test_parse_lookups():
    assert parse_spec({"kind": "deal_lookup", "deal_id": 387830}) == DealLookup(387830)
    assert parse_spec({"kind": "usd_rate", "date": "2025-06-15"}) == UsdRate(date=dt.date(2025, 6, 15))
    with pytest.raises(SpecError, match="outside"):
        parse_spec({"kind": "usd_rate", "date": "2026-12-01"})
    assert parse_spec({"kind": "registry_count", "entity": "brokers"}) == RegistryCount("brokers")
    with pytest.raises(SpecError, match="kind"):
        parse_spec({"kind": "forecast"})


def test_entity_values():
    s = parse_spec({"kind": "deals_aggregate", "measures": ["deal_count"],
                    "filters": {"products": ["Карбамид"], "regions": ["Samarqand viloyati"],
                                "buyer_inns": ["915688255"], "brokers": ["BK001"]}})
    assert entity_values(s) == {("product", "Карбамид"), ("region", "Samarqand viloyati"),
                                ("inn", "915688255"), ("broker", "BK001")}


def _agg(filters):
    return {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": filters}


@pytest.mark.parametrize("spec, message", [
    (_agg({"year": 2026, "month_from": 9, "month_to": 12}), "data ends 30.10.2026"),
    (_agg({"year": 2026, "month_to": 11, "month_from": 10}), "data ends 30.10.2026"),
    (_agg({"date_from": "2025-05-01", "date_to": "2026-11-15"}), "clamp date_from/date_to"),
    (_agg({"date_from": "2021-01-01", "date_to": "2021-12-31"}), "outside"),
    (_agg({"date_from": "2026-11-01"}), "outside"),
    (_agg({"date_to": "2021-12-31"}), "outside"),
    (_agg({"date_from": "2022-01-01", "date_to": "2022-03-31"}), "data covers 03.01.2022–30.10.2026"),
    (_agg({"date_from": "2025-06-01", "date_to": "2025-05-01"}), "date_from must be <= date_to"),
    (_agg({"month_from": 3, "month_to": 5}), "needs a year"),
    (_agg({"month_from": 3}), "needs a year"),
    (_agg({"year": 2025, "date_from": "2025-01-01"}), "not both"),
    (_agg({"month_from": 3, "year": 2025, "date_to": "2025-04-01"}), "not both"),
])
def test_period_rules(spec, message):
    with pytest.raises(SpecError, match=message):
        parse_spec(spec)


@pytest.mark.parametrize("value", ["yes", "maybe", 1, 0, "", [], {}, "1"])
def test_vat_payer_invalid(value):
    with pytest.raises(SpecError, match="vat_payer"):
        parse_spec({"kind": "registry_count", "entity": "participants", "vat_payer": value})


def test_vat_payer_valid():
    assert parse_spec({"kind": "registry_count", "entity": "participants", "vat_payer": "False"}).vat_payer is False
    assert parse_spec({"kind": "registry_count", "entity": "participants", "vat_payer": "TRUE"}).vat_payer is True
    assert parse_spec({"kind": "registry_count", "entity": "participants", "vat_payer": True}).vat_payer is True


@pytest.mark.parametrize("spec, message", [
    ({"entity": "brokers", "role": "Покупатель"}, "role.*brokers"),
    ({"entity": "contracts", "vat_payer": True}, "vat_payer.*contracts"),
    ({"entity": "brokers", "registered_year": 2024}, "registered_year.*brokers"),
    ({"entity": "participants", "registered_year": 2024}, "registered_year.*participants"),
])
def test_registry_field_entity_rules(spec, message):
    with pytest.raises(SpecError, match=message):
        parse_spec({"kind": "registry_count", **spec})


@pytest.mark.parametrize("spec", [
    ["deals_aggregate"],
    "deal_lookup",
    None,
    {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": "2025"},
    {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": ["year"]},
    {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"products": 5}},
    {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"regions": {"a": 1}}},
    {"kind": "deals_aggregate", "measures": 7},
])
def test_malformed_shapes_raise_spec_error(spec):
    with pytest.raises(SpecError):
        parse_spec(spec)


@pytest.mark.parametrize("value", [True, 3.9, "3.9", "abc", [1]])
def test_opt_int_rejects(value):
    with pytest.raises(SpecError, match="integer"):
        parse_spec(_agg({"year": value}))


def test_opt_int_accepts_integral():
    assert parse_spec(_agg({"year": "2025"})).filters.year == 2025
    assert parse_spec(_agg({"year": 2025.0})).filters.year == 2025
    assert parse_spec(_agg({"year": 2025})).filters.year == 2025


@pytest.mark.parametrize("spec, message", [
    ({"year": 2025, "month": 13}, "1..12"),
    ({"year": 2025, "month": 0}, "1..12"),
    ({"month": 5, "date": "2025-05-01"}, "use either date or year/month"),
    ({"month": 5}, "a month needs a year"),
    ({"date": "2025-05-01", "year": 2025}, "use either date or year/month"),
    ({"date": "2025-05-01", "year": 2025, "month": 5}, "use either date or year/month"),
])
def test_usd_rate_rules(spec, message):
    with pytest.raises(SpecError, match=message):
        parse_spec({"kind": "usd_rate", **spec})


def test_usd_rate_valid_month():
    assert parse_spec({"kind": "usd_rate", "year": 2025, "month": 5}) == UsdRate(year=2025, month=5)


@pytest.mark.parametrize("spec", [
    _agg({"product_family": 5}),
    _agg({"product_family": ["a"]}),
    {"kind": "registry_count", "entity": "participants", "region": 5},
    {"kind": "registry_count", "entity": "participants", "region": ["x"]},
])
def test_string_fields(spec):
    with pytest.raises(SpecError, match="must be a string"):
        parse_spec(spec)
