import pytest

from app.compiler.spec import parse_spec
from app.compiler.sql import compile_spec


def run(db, spec_dict):
    compiled = compile_spec(parse_spec(spec_dict))
    return compiled, db.query(compiled.sql)


def scalar(db, sql):
    return db.query(sql).rows[0][0]


def test_count_with_status_split(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count"],
                      "filters": {"year": 2025, "month_from": 10}})
    assert res.columns == ["deals_all", "deals_valid"]
    assert res.rows[0][0] == scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2025 AND month=10")
    assert res.rows[0][1] == scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2025 AND month=10 AND NOT is_cancelled")
    assert c.splits == ["status_scope"]


def test_top_buyer_of_raw_cotton_by_volume_and_value(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["volume_tonnes", "amount_uzs"],
                      "group_by": "buyer", "top": 1, "filters": {"products": ["Хлопок-сырец"]}})
    assert res.columns[:3] == ["ranking", "buyer_inn", "buyer_name"]
    by = {r[0]: r for r in res.rows}
    assert set(by) == {"by_volume_t", "by_total_uzs"}
    expected = scalar(db, """SELECT buyer_inn FROM deals WHERE product_canonical='Хлопок-сырец' AND NOT is_cancelled
                             GROUP BY 1 ORDER BY SUM(volume_tonnes) DESC LIMIT 1""")
    assert by["by_volume_t"][1] == expected
    assert "measure" in c.splits and "excluded_cancelled" in c.notes


def test_participant_role_split(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "participant",
                      "top": 2, "filters": {"year": 2025}})
    assert res.columns[:3] == ["role", "inn", "name"]
    assert sorted(r[0] for r in res.rows) == ["buyer", "buyer", "seller", "seller"]
    assert "role" in c.splits


def test_broker_either_side(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "broker", "top": 1})
    expected = scalar(db, """WITH s AS (SELECT seller_broker b, amount_uzs FROM deals WHERE NOT is_cancelled
                                       UNION ALL SELECT buyer_broker, amount_uzs FROM deals WHERE NOT is_cancelled)
                             SELECT b FROM s GROUP BY b ORDER BY SUM(amount_uzs) DESC LIMIT 1""")
    assert res.rows[0][0] == expected


def test_volume_is_grouped_by_unit(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["volume"],
                      "filters": {"products": ["Дизельное топливо"], "year": 2023}, "status_scope": "valid"})
    assert res.columns[0] == "unit"
    assert "volume_by_unit" in c.notes


def test_month_series_ordered(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count"], "group_by": "month",
                      "filters": {"year": 2024}, "status_scope": "valid"})
    assert [r[1] for r in res.rows] == list(range(1, 13))


def test_broker_filter_either_side(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count"], "status_scope": "all",
                      "filters": {"year": 2025, "brokers": ["BK001"]}})
    assert res.rows[0][0] == scalar(db, """SELECT COUNT(*) FROM deals WHERE year=2025
                                          AND (seller_broker='BK001' OR buyer_broker='BK001')""")


def test_cancelled_pct(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["cancelled_pct"], "filters": {"year": 2025}})
    assert res.columns == ["cancelled_pct"]
    assert 0 < res.rows[0][0] < 100


def test_lookups(db):
    _, res = run(db, {"kind": "deal_lookup", "deal_id": 387830})
    row = dict(zip(res.columns, res.rows[0]))
    assert row["buyer_broker"] == "BK106" and row["contract_id"] == 102267
    _, res = run(db, {"kind": "contract_lookup", "contract_id": 100001})
    row = dict(zip(res.columns, res.rows[0]))
    assert row["seller_inn"] == "953758401" and row["product_canonical"] == "Профиль алюминиевый"
    _, res = run(db, {"kind": "usd_rate", "date": "2026-10-30"})
    assert res.rows[0][1] == pytest.approx(13369.54)
    _, res = run(db, {"kind": "usd_rate", "year": 2024, "month": 3})
    assert res.columns == ["avg_rate", "min_rate", "max_rate", "days"]
    _, res = run(db, {"kind": "registry_count", "entity": "brokers"})
    assert res.rows == [(120,)]
    _, res = run(db, {"kind": "registry_count", "entity": "participants", "role": "Продавец"})
    assert res.rows == [(392,)]


def test_literals_are_escaped():
    c = compile_spec(parse_spec({"kind": "deals_aggregate", "measures": ["deal_count"],
                                 "filters": {"products": ["x' OR 1=1 --"]}}))
    assert "'x'' OR 1=1 --'" in c.sql


def test_participant_volume_keeps_unit(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["volume"], "group_by": "participant",
                      "top": 1, "filters": {"year": 2025, "products": ["Дизельное топливо"]}})
    assert "unit" in res.columns
