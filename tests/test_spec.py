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
    ({"measures": ["deal_count"], "filters": {"date_from": "2021-12-01"}}, "outside"),
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
    assert entity_values(s) == {"Карбамид", "Samarqand viloyati", "915688255", "BK001"}
