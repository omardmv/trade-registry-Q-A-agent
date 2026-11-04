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


def test_cancelled_pct_alone_grouped_uses_all_statuses(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["cancelled_pct"], "group_by": "region", "top": 3})
    expected = db.query("""SELECT region, ROUND(100.0 * COUNT(*) FILTER (WHERE is_cancelled) / COUNT(*), 2)
                           FROM deals GROUP BY region ORDER BY 2 DESC NULLS LAST, region LIMIT 3""").rows
    assert [tuple(r) for r in res.rows] == [tuple(r) for r in expected]
    assert any(r[1] > 0 for r in res.rows)
    assert "all_statuses" in c.notes and "excluded_cancelled" not in c.notes


def test_avg_price_ignores_non_mass_money(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["avg_price_uzs_per_kg"], "status_scope": "valid",
                      "filters": {"year": 2025, "products": ["Бензин автомобильный"]}})
    expected = scalar(db, """SELECT SUM(amount_uzs) / (SUM(volume_tonnes) * 1000) FROM deals
                             WHERE year=2025 AND product_canonical='Бензин автомобильный'
                               AND volume_tonnes IS NOT NULL AND NOT is_cancelled""")
    assert res.rows[0][0] == pytest.approx(expected)


def test_avg_price_split_ignores_non_mass_money(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["avg_price_uzs_per_kg"],
                      "filters": {"year": 2025, "products": ["Бензин автомобильный"]}})
    base = """SELECT SUM(amount_uzs) / (SUM(volume_tonnes) * 1000) FROM deals
              WHERE year=2025 AND product_canonical='Бензин автомобильный' AND volume_tonnes IS NOT NULL"""
    assert res.rows[0][0] == pytest.approx(scalar(db, base))
    assert res.rows[0][1] == pytest.approx(scalar(db, base + " AND NOT is_cancelled"))


def test_broker_group_dedupes_same_broker_both_sides(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count"], "group_by": "broker",
                      "top": 5, "status_scope": "valid",
                      "filters": {"brokers": ["BK111"]}})
    row = [r for r in res.rows if r[0] == "BK111"][0]
    expected = scalar(db, """SELECT COUNT(*) FROM deals WHERE (seller_broker='BK111' OR buyer_broker='BK111')
                             AND NOT is_cancelled""")
    assert row[res.columns.index("deals")] == expected


def test_volume_ranked_within_unit(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["volume"], "group_by": "buyer", "top": 3,
                      "filters": {"year": 2025}, "status_scope": "valid"})
    ui = res.columns.index("unit")
    got = [r[res.columns.index("buyer_inn")] for r in res.rows if r[ui] == "тонна"]
    expected = [r[0] for r in db.query("""SELECT buyer_inn FROM deals WHERE year=2025 AND NOT is_cancelled
                  AND unit='тонна' GROUP BY buyer_inn ORDER BY SUM(volume) DESC NULLS LAST, buyer_inn LIMIT 3""").rows]
    assert got == expected
    units = [r[ui] for r in res.rows]
    assert units == sorted(units)
    for u in set(units):
        assert units.count(u) <= 3


def test_participant_two_measures(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["amount_uzs", "volume_tonnes"],
                      "group_by": "participant", "top": 1,
                      "filters": {"year": 2025, "products": ["Хлопок-сырец"]}})
    assert res.columns[:4] == ["role", "ranking", "inn", "name"]
    assert "measure" in c.splits and "role" in c.splits
    by = {(r[0], r[1]): r[2] for r in res.rows}
    for role, col in (("buyer", "buyer_inn"), ("seller", "seller_inn")):
        for rank, m in (("by_total_uzs", "amount_uzs"), ("by_volume_t", "volume_tonnes")):
            exp = scalar(db, f"""SELECT {col} FROM deals WHERE year=2025 AND product_canonical='Хлопок-сырец'
                                 AND NOT is_cancelled GROUP BY 1 ORDER BY SUM({m}) DESC NULLS LAST, 1 LIMIT 1""")
            assert by[(role, rank)] == exp


def test_literal_double_space_preserved():
    c = compile_spec(parse_spec({"kind": "deals_aggregate", "measures": ["deal_count"], "group_by": "region",
                                 "top": 2, "filters": {"products": ["A  B"]}}))
    assert "'A  B'" in c.sql
    assert "  " not in c.sql.replace("'A  B'", "")


def test_ranked_ties_are_deterministic(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count"], "group_by": "region", "top": 3,
                      "status_scope": "valid"})
    assert "DESC NULLS LAST, d.region" in c.sql
    assert db.query(c.sql).rows == res.rows


def test_cancelled_pct_keeps_valid_scope_for_other_measures(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["amount_uzs", "cancelled_pct"],
                      "group_by": "region", "top": 1})
    top = db.query("""SELECT region, SUM(amount_uzs) FILTER (WHERE NOT is_cancelled) t FROM deals
                      GROUP BY region ORDER BY t DESC NULLS LAST, region LIMIT 1""").rows[0]
    rate = scalar(db, f"""SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE is_cancelled) / COUNT(*), 2) FROM deals
                          WHERE region = '{top[0]}'""")
    row = dict(zip(res.columns, res.rows[0]))
    assert row["region"] == top[0]
    assert row["total_uzs"] == pytest.approx(top[1])
    assert row["cancelled_pct"] == rate
    assert "excluded_cancelled" in c.notes and "all_statuses" not in c.notes


def test_cancelled_pct_with_count_keeps_status_split(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count", "cancelled_pct"],
                      "filters": {"year": 2025}})
    row = dict(zip(res.columns, res.rows[0]))
    assert row["deals_all"] == scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2025")
    assert row["deals_valid"] == scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2025 AND NOT is_cancelled")
    assert row["cancelled_pct"] == scalar(db, """SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE is_cancelled)
                                                 / COUNT(*), 2) FROM deals WHERE year=2025""")
