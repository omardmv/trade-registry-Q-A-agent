"""New dimensions, filters, measures, order and compare_periods, each checked against an independent
hand-written query. Hand queries join contracts / participants instead of using the denormalized columns."""
import pytest

from app.compiler.spec import parse_spec
from app.compiler.sql import compile_spec


def run(db, spec_dict):
    compiled = compile_spec(parse_spec(spec_dict))
    return compiled, db.query(compiled.sql, max_rows=10_000)


def rows(db, sql):
    return [tuple(r) for r in db.query(sql, max_rows=10_000).rows]


def scalar(db, sql):
    return db.query(sql).rows[0][0]


def agg(measures, **kw):
    return {"kind": "deals_aggregate", "measures": measures, **kw}


# ---------- order ----------

def test_order_asc_ranks_least(db):
    c, res = run(db, agg(["amount_uzs"], group_by="seller", top=3, order="asc", status_scope="valid",
                         filters={"year": 2023, "products": ["Портландцемент"]}))
    want = rows(db, """SELECT seller_inn, SUM(amount_uzs) t FROM deals WHERE year=2023
                       AND product_canonical='Портландцемент' AND NOT is_cancelled
                       GROUP BY 1 ORDER BY t ASC NULLS LAST, 1 LIMIT 3""")
    assert [(r[0], r[2]) for r in res.rows] == [(w[0], pytest.approx(w[1])) for w in want]
    assert "order_asc" in c.notes


def test_order_asc_puts_nulls_last(db):
    # non-mass deals (litres) have no per-tonne price: they must not win the "cheapest" ranking
    _, res = run(db, agg(["mean_deal_price_uzs_per_t"], group_by="deal", top=1, order="asc", status_scope="valid",
                         filters={"year": 2022}))
    want = rows(db, """SELECT deal_id, amount_uzs / volume_tonnes p FROM deals WHERE year=2022 AND NOT is_cancelled
                       AND volume_tonnes IS NOT NULL ORDER BY p ASC, deal_id LIMIT 1""")[0]
    row = dict(zip(res.columns, res.rows[0]))
    assert row["deal_id"] == want[0]
    assert row["mean_price_uzs_per_t"] == pytest.approx(want[1])


def test_order_asc_per_unit_volume(db):
    _, res = run(db, agg(["volume"], group_by="buyer", top=2, order="asc", status_scope="valid",
                         filters={"year": 2025, "products": ["Дизельное топливо"]}))
    ui, bi = res.columns.index("unit"), res.columns.index("buyer_inn")
    for unit in {r[ui] for r in res.rows}:
        want = [r[0] for r in rows(db, f"""SELECT buyer_inn FROM deals WHERE year=2025 AND NOT is_cancelled
                    AND product_canonical='Дизельное топливо' AND unit='{unit}'
                    GROUP BY 1 ORDER BY SUM(volume) ASC NULLS LAST, 1 LIMIT 2""")]
        assert [r[bi] for r in res.rows if r[ui] == unit] == want


def test_order_asc_without_group_rejected():
    from app.compiler.spec import SpecError
    with pytest.raises(SpecError, match="group_by"):
        parse_spec(agg(["deal_count"], order="asc"))


# ---------- dimensions ----------

@pytest.mark.parametrize("dim,col", [("category", "category"), ("industry", "industry"),
                                     ("product_group", "product_group"), ("delivery_basis", "delivery_basis"),
                                     ("packaging", "packaging"), ("hs_code", "hs_code")])
def test_contract_attribute_dimensions(db, dim, col):
    _, res = run(db, agg(["deal_count"], group_by=dim, top=3, status_scope="valid", filters={"year": 2024}))
    want = rows(db, f"""SELECT c.{col}, COUNT(*) n FROM deals d JOIN contracts c USING (contract_id)
                        WHERE d.year=2024 AND NOT d.is_cancelled GROUP BY 1 ORDER BY n DESC, 1 LIMIT 3""")
    assert res.columns == [dim, "deals"]
    assert [tuple(r) for r in res.rows] == want


def test_product_variant_dimension(db):
    _, res = run(db, agg(["deal_count"], group_by="product_variant", top=5, status_scope="all",
                         filters={"products": ["Сахар"]}))
    want = rows(db, """SELECT product_name_full, COUNT(*) n FROM deals WHERE product_canonical='Сахар'
                       GROUP BY 1 ORDER BY n DESC, 1 LIMIT 5""")
    assert [tuple(r) for r in res.rows] == want


def test_quarter_dimension_is_time_series(db):
    _, res = run(db, agg(["deal_count"], group_by="quarter", status_scope="all", filters={"year": 2024}))
    want = rows(db, """SELECT 2024, (month + 2) // 3 q, COUNT(*) FROM deals WHERE year=2024 GROUP BY q ORDER BY q""")
    assert res.columns == ["year", "quarter", "deals"]
    assert [tuple(r) for r in res.rows] == want


def test_hour_dimension(db):
    _, res = run(db, agg(["deal_count"], group_by="hour", status_scope="all", filters={"year": 2025}))
    want = rows(db, "SELECT hour(deal_time) h, COUNT(*) FROM deals WHERE year=2025 GROUP BY h ORDER BY h")
    assert [tuple(r) for r in res.rows] == want


def test_hour_dimension_ranked(db):
    _, res = run(db, agg(["deal_count"], group_by="hour", top=1, status_scope="all"))
    want = rows(db, "SELECT hour(deal_time) h, COUNT(*) n FROM deals GROUP BY h ORDER BY n DESC, h LIMIT 1")
    assert [tuple(r) for r in res.rows] == want


@pytest.mark.parametrize("dim,side,col", [("buyer_region", "buyer", "region"), ("seller_region", "seller", "region"),
                                          ("buyer_activity", "buyer", "activity"),
                                          ("seller_activity", "seller", "activity")])
def test_participant_attribute_dimensions(db, dim, side, col):
    _, res = run(db, agg(["amount_uzs"], group_by=dim, top=3, status_scope="valid", filters={"year": 2023}))
    want = rows(db, f"""SELECT p.{col}, SUM(d.amount_uzs) t FROM deals d JOIN participants p ON p.inn = d.{side}_inn
                        WHERE d.year=2023 AND NOT d.is_cancelled GROUP BY 1 ORDER BY t DESC, 1 LIMIT 3""")
    assert [r[0] for r in res.rows] == [w[0] for w in want]
    assert [r[1] for r in res.rows] == [pytest.approx(w[1]) for w in want]


def test_deal_dimension_has_parties_and_price(db):
    _, res = run(db, agg(["amount_uzs"], group_by="deal", top=1, status_scope="valid", filters={"year": 2024}))
    row = dict(zip(res.columns, res.rows[0]))
    want = rows(db, """SELECT d.deal_id, d.seller_inn, ps.name, d.buyer_inn, pb.name, d.amount / (d.quantity * d.lot)
                       FROM deals d JOIN participants ps ON ps.inn = d.seller_inn
                       JOIN participants pb ON pb.inn = d.buyer_inn
                       WHERE d.year=2024 AND NOT d.is_cancelled ORDER BY d.amount_uzs DESC LIMIT 1""")[0]
    assert (row["deal_id"], row["seller_inn"], row["seller_name"], row["buyer_inn"], row["buyer_name"]) == want[:5]
    assert row["unit_price"] == pytest.approx(want[5])
    assert {"unit", "currency"} <= set(row)


# ---------- filters ----------

@pytest.mark.parametrize("key,col,value", [
    ("categories", "category", "Нефтепродукты"), ("industries", "industry", "Металлургия"),
    ("product_groups", "product_group", "Прочие товары"), ("delivery_bases", "delivery_basis", "FCA"),
    ("packagings", "packaging", "в мешках по 50 кг"), ("hs_codes", "hs_code", "520100"),
    ("units", "unit", "литр")])
def test_contract_attribute_filters(db, key, col, value):
    _, res = run(db, agg(["deal_count"], status_scope="all", filters={"year": 2025, key: [value]}))
    want = scalar(db, f"""SELECT COUNT(*) FROM deals d JOIN contracts c USING (contract_id)
                          WHERE d.year=2025 AND c.{col} = '{value}'""")
    assert want > 0 and res.rows[0][0] == want


def test_product_variant_filter(db):
    from app.resolve import Resolver
    c = compile_spec(parse_spec(agg(["deal_count"], status_scope="all",
                                    filters={"product_variant_query": "сахар белый"})), Resolver(db).variant_names)
    assert db.query(c.sql).rows[0][0] == scalar(db, "SELECT COUNT(*) FROM deals WHERE product_name_full = 'Сахар белый'")


@pytest.mark.parametrize("key,side,col,value", [
    ("buyer_regions", "buyer", "region", "Buxoro viloyati"), ("seller_regions", "seller", "region", "Toshkent shahri"),
    ("buyer_activities", "buyer", "activity", "Кластер"),
    ("seller_activities", "seller", "activity", "Производственное предприятие")])
def test_participant_attribute_filters(db, key, side, col, value):
    _, res = run(db, agg(["amount_uzs"], status_scope="valid", filters={"year": 2023, key: [value]}))
    want = scalar(db, f"""SELECT SUM(d.amount_uzs) FROM deals d JOIN participants p ON p.inn = d.{side}_inn
                          WHERE d.year=2023 AND NOT d.is_cancelled AND p.{col} = '{value}'""")
    assert res.rows[0][0] == pytest.approx(want)


def test_new_filter_literals_escaped():
    c = compile_spec(parse_spec(agg(["deal_count"], filters={"product_variant_query": "x",
                                                            "categories": ["a'b"]})), lambda t: ["x' OR 1=1 --"])
    assert "'x'' OR 1=1 --'" in c.sql and "'a''b'" in c.sql


# ---------- measures ----------

def _one(db, measure, filters=None):
    _, res = run(db, agg([measure], status_scope="valid", filters=filters or {"year": 2024}))
    return res


@pytest.mark.parametrize("measure,expr", [
    ("distinct_products", "COUNT(DISTINCT product_canonical)"),
    ("distinct_regions", "COUNT(DISTINCT region)"),
    ("distinct_seller_brokers", "COUNT(DISTINCT seller_broker)"),
    ("distinct_buyer_brokers", "COUNT(DISTINCT buyer_broker)"),
    ("median_price_uzs_per_kg", "MEDIAN(amount_uzs / (volume_tonnes * 1000))"),
    ("mean_deal_price_uzs_per_t", "AVG(amount_uzs / volume_tonnes)"),
    ("avg_price_change_pct", "AVG(100 * (amount / (quantity * lot) / start_price - 1))"),
    ("avg_days_to_payment", "AVG(payment_date - deal_date)"),
    ("avg_days_to_delivery", "AVG(delivery_date - payment_date)"),
    ("undelivered_lots", "SUM(quantity - COALESCE(delivered_lots, 0))"),
])
def test_simple_measures(db, measure, expr):
    res = _one(db, measure)
    want = scalar(db, f"SELECT {expr} FROM deals WHERE year=2024 AND NOT is_cancelled")
    assert len(res.columns) == 1
    assert res.rows[0][0] == pytest.approx(want)


def test_distinct_brokers_either_side(db):
    res = _one(db, "distinct_brokers", filters={"year": 2024, "products": ["Карбамид"]})
    want = scalar(db, """SELECT COUNT(DISTINCT b) FROM (
        SELECT seller_broker b FROM deals WHERE year=2024 AND product_canonical='Карбамид' AND NOT is_cancelled
        UNION ALL SELECT buyer_broker FROM deals WHERE year=2024 AND product_canonical='Карбамид' AND NOT is_cancelled)""")
    assert res.rows[0][0] == want


def test_distinct_brokers_split(db):
    _, res = run(db, agg(["distinct_brokers"], filters={"year": 2022, "month_from": 1,
                                                        "products": ["Хлопок-сырец"]}))
    assert res.columns == ["brokers_all", "brokers_valid"]
    want_all = scalar(db, """SELECT COUNT(DISTINCT b) FROM (SELECT seller_broker b FROM deals WHERE year=2022 AND month=1
        AND product_canonical='Хлопок-сырец' UNION ALL SELECT buyer_broker FROM deals WHERE year=2022 AND month=1
        AND product_canonical='Хлопок-сырец')""")
    assert res.rows[0][0] == want_all


def test_late_delivery_measures(db):
    _, res = run(db, agg(["late_delivery_count", "avg_delivery_delay_days"], status_scope="valid",
                         filters={"year": 2024}))
    want = rows(db, """SELECT COUNT(*), AVG(d.delivery_date - (d.payment_date + c.delivery_term_days))
                       FROM deals d JOIN contracts c USING (contract_id)
                       WHERE d.year=2024 AND NOT d.is_cancelled
                         AND d.delivery_date > d.payment_date + c.delivery_term_days""")[0]
    assert res.rows[0][0] == want[0] and want[0] > 0
    assert res.rows[0][1] == pytest.approx(want[1])


def test_late_payment_count_counts_every_status(db):
    _, res = run(db, agg(["late_payment_count", "deal_count"], filters={"year": 2024}))
    row = dict(zip(res.columns, res.rows[0]))
    want = scalar(db, """SELECT COUNT(*) FROM deals d JOIN contracts c USING (contract_id) WHERE d.year=2024
        AND (d.payment_date > d.deal_date + c.payment_term_days
             OR (d.payment_date IS NULL AND d.deal_date + c.payment_term_days < DATE '2026-10-30'))""")
    assert row["late_payments"] == want and want > 0
    assert row["deals_all"] == scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2024")


def test_unit_price_extremes_by_unit(db):
    c, res = run(db, agg(["min_unit_price_uzs", "max_unit_price_uzs"], status_scope="valid",
                         filters={"products": ["Дизельное топливо"], "year": 2024}))
    assert res.columns[0] == "unit" and "volume_by_unit" in c.notes
    want = rows(db, """SELECT unit, MIN(amount_uzs / (quantity * lot)), MAX(amount_uzs / (quantity * lot)) FROM deals
                       WHERE product_canonical='Дизельное топливо' AND year=2024 AND NOT is_cancelled
                       GROUP BY unit ORDER BY unit""")
    assert [r[0] for r in res.rows] == [w[0] for w in want]
    for r, w in zip(res.rows, want):
        assert r[1] == pytest.approx(w[1]) and r[2] == pytest.approx(w[2])


def test_delivered_and_undelivered_volume_by_unit(db):
    _, res = run(db, agg(["delivered_volume", "undelivered_volume"], status_scope="valid",
                         filters={"year": 2023, "statuses": ["Исполнен(Частично)"]}))
    want = rows(db, """SELECT unit, SUM(delivered_lots * lot), SUM((quantity - delivered_lots) * lot) FROM deals
                       WHERE year=2023 AND status='Исполнен(Частично)' GROUP BY unit ORDER BY unit""")
    assert [r[0] for r in res.rows] == [w[0] for w in want]
    for r, w in zip(res.rows, want):
        assert r[1] == pytest.approx(w[1]) and r[2] == pytest.approx(w[2])


def test_avg_price_both_definitions(db):
    c, res = run(db, agg(["avg_price_uzs_per_kg", "mean_deal_price_uzs_per_t"], status_scope="valid",
                         filters={"year": 2024, "products": ["Портландцемент"]}))
    w = rows(db, """SELECT SUM(amount_uzs) / (SUM(volume_tonnes) * 1000), AVG(amount_uzs / volume_tonnes) FROM deals
                    WHERE year=2024 AND product_canonical='Портландцемент' AND NOT is_cancelled
                      AND volume_tonnes IS NOT NULL""")[0]
    assert res.rows[0][0] == pytest.approx(w[0]) and res.rows[0][1] == pytest.approx(w[1])
    assert "price_average" in c.splits


# ---------- share_pct ----------

def test_share_pct_contract_form(db):
    c, res = run(db, agg(["share_pct"], share_of={"field": "contract_form", "value": "Форвард"}))
    want = scalar(db, "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE contract_form='Форвард') / COUNT(*), 2) FROM deals")
    assert res.columns == ["share_pct"] and res.rows[0][0] == want
    assert "excluded_cancelled" not in c.notes and "status_split" not in c.notes


def test_share_pct_status_within_product(db):
    _, res = run(db, agg(["share_pct", "deal_count"], share_of={"field": "status", "values": ["Исполнен"]},
                         filters={"products": ["Дизельное топливо"]}))
    row = dict(zip(res.columns, res.rows[0]))
    want = scalar(db, """SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status='Исполнен') / COUNT(*), 2) FROM deals
                         WHERE product_canonical='Дизельное топливо'""")
    assert row["share_pct"] == want


def test_share_pct_grouped_and_valid_scope_keeps_cancelled_in_denominator(db):
    _, res = run(db, agg(["amount_uzs", "share_pct"], group_by="region", top=2,
                         share_of={"field": "contract_type", "value": "Экспортный"}, filters={"year": 2024}))
    row = dict(zip(res.columns, res.rows[0]))
    want = scalar(db, f"""SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE contract_type='Экспортный') / COUNT(*), 2)
                          FROM deals WHERE year=2024 AND region='{row['region']}'""")
    assert row["share_pct"] == want


def test_share_pct_requires_share_of():
    from app.compiler.spec import SpecError
    with pytest.raises(SpecError, match="share_of"):
        parse_spec(agg(["share_pct"]))
    with pytest.raises(SpecError):
        parse_spec(agg(["share_pct"], share_of={"field": "contract_form", "value": "Бартер"}))
    with pytest.raises(SpecError):
        parse_spec(agg(["share_pct"], share_of={"field": "region", "value": "x"}))
    with pytest.raises(SpecError, match="share_pct"):
        parse_spec(agg(["deal_count"], share_of={"field": "contract_form", "value": "Спот"}))


# ---------- compare_periods ----------

def test_compare_periods_years(db):
    c, res = run(db, agg(["deal_count"], status_scope="all", compare_periods=[{"year": 2023}, {"year": 2024}]))
    assert res.columns == ["year", "month_from", "month_to", "deals", "change_pct"]
    a = scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2023")
    b = scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2024")
    assert [r[:4] for r in res.rows] == [(2023, None, None, a), (2024, None, None, b)]
    assert res.rows[0][4] is None
    assert res.rows[1][4] == pytest.approx(round(100.0 * (b / a - 1), 2))


def test_compare_periods_months_split(db):
    _, res = run(db, agg(["amount_uzs"], filters={"products": ["Хлопок-сырец"]},
                         compare_periods=[{"year": 2024, "month_from": 1, "month_to": 3},
                                          {"year": 2025, "month_from": 1, "month_to": 3}]))
    assert res.columns == ["year", "month_from", "month_to", "total_uzs_all", "total_uzs_valid",
                           "total_uzs_all_change_pct", "total_uzs_valid_change_pct"]
    base = "SELECT SUM(amount_uzs) FROM deals WHERE product_canonical='Хлопок-сырец' AND month BETWEEN 1 AND 3"
    a = scalar(db, base + " AND year=2024 AND NOT is_cancelled")
    b = scalar(db, base + " AND year=2025 AND NOT is_cancelled")
    assert res.rows[1][4] == pytest.approx(b)
    assert res.rows[1][6] == pytest.approx(round(100.0 * (b / a - 1), 2))


@pytest.mark.parametrize("bad,msg", [
    ({"compare_periods": [{"year": 2023}]}, "exactly two"),
    ({"compare_periods": [{"year": 2023}, {"year": 2023}]}, "different"),
    ({"compare_periods": [{"year": 2023}, {"year": 2024}], "group_by": "region"}, "without group_by"),
    ({"compare_periods": [{"year": 2023}, {"year": 2024}], "filters": {"year": 2023}}, "only in compare_periods"),
    ({"compare_periods": [{"year": 2021}, {"year": 2024}]}, "outside"),
    ({"compare_periods": [{"month_from": 1}, {"year": 2024}]}, "year"),
    ({"compare_periods": [{"year": 2023, "foo": 1}, {"year": 2024}]}, "unknown key"),
])
def test_compare_periods_validation(bad, msg):
    from app.compiler.spec import SpecError
    with pytest.raises(SpecError, match=msg):
        parse_spec(agg(["deal_count"], **bad))


# ---------- broker_side ----------

def test_broker_side_counts_each_side_separately(db):
    c, res = run(db, agg(["deal_count"], group_by="broker_side", filters={"year": 2025, "brokers": ["BK045"]}))
    assert res.columns == ["side", "broker_code", "broker_name", "deals_all", "deals_valid"]
    by = {r[0]: r for r in res.rows}
    s_all, s_valid, b_all, b_valid = rows(db, """SELECT
        COUNT(*) FILTER (WHERE seller_broker='BK045'), COUNT(*) FILTER (WHERE seller_broker='BK045' AND NOT is_cancelled),
        COUNT(*) FILTER (WHERE buyer_broker='BK045'), COUNT(*) FILTER (WHERE buyer_broker='BK045' AND NOT is_cancelled)
        FROM deals WHERE year=2025""")[0]
    assert (by["seller"][3], by["seller"][4]) == (s_all, s_valid)
    assert (by["buyer"][3], by["buyer"][4]) == (b_all, b_valid)
    assert "broker_side_split" in c.notes and "status_scope" in c.splits


def test_broker_side_needs_brokers_filter():
    from app.compiler.spec import SpecError
    with pytest.raises(SpecError, match="brokers"):
        parse_spec(agg(["deal_count"], group_by="broker_side"))


# ---------- review fixes ----------

@pytest.mark.parametrize("basis,col", [("amount_uzs", "amount_uzs"), ("volume_t", "volume_tonnes")])
def test_share_pct_by_amount_and_volume(db, basis, col):
    _, res = run(db, agg(["share_pct"], share_of={"field": "contract_type", "value": "Экспортный", "basis": basis},
                         filters={"year": 2024}))
    want = scalar(db, f"""SELECT ROUND(100.0 * SUM({col}) FILTER (WHERE contract_type='Экспортный')
                           / SUM({col}), 2) FROM deals WHERE year=2024 AND NOT is_cancelled""")
    assert res.rows[0][0] == want


def test_share_basis_validated():
    from app.compiler.spec import SpecError
    with pytest.raises(SpecError, match="basis"):
        parse_spec(agg(["share_pct"], share_of={"field": "contract_type", "value": "Экспортный", "basis": "lots"}))


def test_asc_ranking_with_scope_free_measure_skips_groups_without_valid_deals(db):
    _, res = run(db, agg(["deal_count", "cancelled_pct"], group_by="buyer", top=5, order="asc",
                         filters={"year": 2024}))
    by_deals = [r for r in res.rows if r[0] == "by_deals"]
    counts = [r[res.columns.index("deals")] for r in by_deals]
    assert counts and min(counts) > 0
    want = rows(db, """SELECT buyer_inn, COUNT(*) FILTER (WHERE NOT is_cancelled) n FROM deals WHERE year=2024
                       GROUP BY 1 HAVING n > 0 ORDER BY n ASC, 1 LIMIT 5""")
    assert [r[res.columns.index("buyer_inn")] for r in by_deals] == [w[0] for w in want]


def test_compare_periods_unequal_length_rejected():
    from app.compiler.spec import SpecError
    with pytest.raises(SpecError, match="same length"):
        parse_spec(agg(["deal_count"], compare_periods=[{"year": 2024}, {"year": 2025, "month_from": 1,
                                                                          "month_to": 3}]))


def test_compare_periods_percent_measure_change_in_points(db):
    _, res = run(db, agg(["cancelled_pct"], compare_periods=[{"year": 2023}, {"year": 2024}]))
    assert res.columns[-1] == "change_pp"
    assert res.rows[1][-1] == pytest.approx(res.rows[1][-2] - res.rows[0][-2])


def test_active_on_outside_data_rejected():
    from app.compiler.spec import SpecError
    with pytest.raises(SpecError, match="outside"):
        parse_spec({"kind": "registry_count", "entity": "contracts", "active_on": "2030-01-01"})
