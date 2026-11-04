"""Lookups and registry counts, checked against hand-written queries on the source tables."""
import datetime as dt

import pytest

from app.compiler.spec import SpecError, parse_spec
from app.compiler.sql import compile_spec


def run(db, d):
    return db.query(compile_spec(parse_spec(d)).sql)


def one(db, sql):
    return db.query(sql).rows[0]


def test_contract_lookup_returns_every_card_field(db):
    res = run(db, {"kind": "contract_lookup", "contract_id": 100001})
    row = dict(zip(res.columns, res.rows[0]))
    cols = ["product_name", "brand", "size", "grade", "product_type", "product_class", "hs_code", "product_group",
            "category", "industry", "unit", "lot", "currency", "base_price", "deposit_pct", "delivery_basis",
            "region", "warehouse_address", "trade_type", "contract_type", "contract_form", "payment_term_days",
            "delivery_term_days", "packaging", "seller_inn", "registered_date", "valid_until"]
    want = one(db, f"SELECT {', '.join(cols)} FROM contracts WHERE contract_id = 100001")
    assert tuple(row[c] for c in cols) == tuple(want)
    assert row["seller_name"] and "deals_all" in row and "deals_valid" in row


def test_participant_lookup(db):
    res = run(db, {"kind": "participant_lookup", "inn": "915688255"})
    row = dict(zip(res.columns, res.rows[0]))
    want = one(db, """SELECT name, region, activity, role, vat_payer FROM participants WHERE inn='915688255'""")
    assert (row["name"], row["region"], row["activity"], row["role"], row["vat_payer"]) == tuple(want)
    nb, ns = one(db, """SELECT COUNT(*) FILTER (WHERE buyer_inn='915688255'), COUNT(*) FILTER (WHERE seller_inn='915688255')
                        FROM deals""")
    assert (row["deals_as_buyer"], row["deals_as_seller"]) == (nb, ns)
    sold = one(db, """SELECT SUM(amount_uzs) FROM deals WHERE seller_inn='915688255' AND NOT is_cancelled""")[0]
    assert row["sold_uzs_valid"] == pytest.approx(sold)
    bought = one(db, """SELECT COALESCE(SUM(amount_uzs), 0) FROM deals WHERE buyer_inn='915688255'
                        AND NOT is_cancelled""")[0]
    assert (row["bought_uzs_valid"] or 0) == pytest.approx(bought)
    first, last = one(db, """SELECT MIN(deal_date), MAX(deal_date) FROM deals
                             WHERE buyer_inn='915688255' OR seller_inn='915688255'""")
    assert (row["first_deal_date"], row["last_deal_date"]) == (first, last)


def test_broker_lookup(db):
    res = run(db, {"kind": "broker_lookup", "broker_code": "bk045"})
    row = dict(zip(res.columns, res.rows[0]))
    want = one(db, "SELECT name, inn, region FROM brokers WHERE broker_code='BK045'")
    assert (row["name"], row["inn"], row["region"]) == tuple(want)
    s_all, b_all, s_valid, b_valid, either = one(db, """SELECT
        COUNT(*) FILTER (WHERE seller_broker='BK045'), COUNT(*) FILTER (WHERE buyer_broker='BK045'),
        COUNT(*) FILTER (WHERE seller_broker='BK045' AND NOT is_cancelled),
        COUNT(*) FILTER (WHERE buyer_broker='BK045' AND NOT is_cancelled),
        COUNT(*) FILTER (WHERE seller_broker='BK045' OR buyer_broker='BK045') FROM deals""")
    assert (row["deals_as_seller_broker"], row["deals_as_buyer_broker"]) == (s_all, b_all)
    assert (row["deals_as_seller_broker_valid"], row["deals_as_buyer_broker_valid"]) == (s_valid, b_valid)
    assert row["deals_either_side"] == either


@pytest.mark.parametrize("bad", [{"kind": "participant_lookup", "inn": "12"},
                                 {"kind": "broker_lookup", "broker_code": "X1"},
                                 {"kind": "participant_lookup"}])
def test_lookup_validation(bad):
    with pytest.raises(SpecError):
        parse_spec(bad)


def test_usd_extreme_max_year(db):
    res = run(db, {"kind": "usd_rate", "year": 2024, "extreme": "max"})
    assert res.columns == ["rate_date", "rate"]
    mx = one(db, "SELECT MAX(rate) FROM usd_rates WHERE year(rate_date)=2024")[0]
    want = db.query(f"SELECT rate_date, rate FROM usd_rates WHERE year(rate_date)=2024 AND rate={mx} "
                    "ORDER BY rate_date").rows
    assert [tuple(r) for r in res.rows] == [tuple(r) for r in want]


def test_usd_extreme_min_month(db):
    res = run(db, {"kind": "usd_rate", "year": 2023, "month": 6, "extreme": "min"})
    want = db.query("""SELECT rate_date, rate FROM usd_rates WHERE year(rate_date)=2023 AND month(rate_date)=6
                       ORDER BY rate, rate_date LIMIT 1""").rows[0]
    assert tuple(res.rows[0]) == tuple(want)
    assert all(r[0].month == 6 for r in res.rows)


def test_usd_extreme_needs_period():
    with pytest.raises(SpecError):
        parse_spec({"kind": "usd_rate", "date": "2024-01-05", "extreme": "max"})
    with pytest.raises(SpecError):
        parse_spec({"kind": "usd_rate", "year": 2024, "extreme": "avg"})


@pytest.mark.parametrize("spec,sql", [
    ({"entity": "participants", "activity": "Транспортная компания"},
     "SELECT COUNT(*) FROM participants WHERE activity='Транспортная компания'"),
    ({"entity": "participants", "activity": "Кластер", "role": "Продавец"},
     "SELECT COUNT(*) FROM participants WHERE activity='Кластер' AND role='Продавец'"),
    ({"entity": "participants", "region": "Buxoro viloyati", "vat_payer": False},
     "SELECT COUNT(*) FROM participants WHERE region='Buxoro viloyati' AND NOT vat_payer"),
    ({"entity": "brokers", "region": "Toshkent shahri"},
     "SELECT COUNT(*) FROM brokers WHERE region='Toshkent shahri'"),
    ({"entity": "contracts", "products": ["Карбамид"], "contract_form": "Спот"},
     "SELECT COUNT(*) FROM contracts WHERE product_canonical='Карбамид' AND contract_form='Спот'"),
    ({"entity": "contracts", "seller_inn": "975923536"},
     "SELECT COUNT(*) FROM contracts WHERE seller_inn='975923536'"),
    ({"entity": "contracts", "active_on": "2024-05-01"},
     "SELECT COUNT(*) FROM contracts WHERE registered_date <= DATE '2024-05-01' AND valid_until >= DATE '2024-05-01'"),
    ({"entity": "contracts", "registered_year": 2023, "region": "Samarqand viloyati"},
     "SELECT COUNT(*) FROM contracts WHERE year(registered_date)=2023 AND region='Samarqand viloyati'"),
])
def test_registry_count_filters(db, spec, sql):
    res = run(db, {"kind": "registry_count", **spec})
    want = one(db, sql)[0]
    assert want > 0 and res.rows[0][0] == want


@pytest.mark.parametrize("bad", [
    {"entity": "brokers", "activity": "Кластер"}, {"entity": "participants", "products": ["Карбамид"]},
    {"entity": "participants", "active_on": "2024-01-01"}, {"entity": "brokers", "seller_inn": "975923536"},
    {"entity": "contracts", "seller_inn": "12"}])
def test_registry_count_field_rules(bad):
    with pytest.raises(SpecError):
        parse_spec({"kind": "registry_count", **bad})


def test_registry_literals_escaped():
    c = compile_spec(parse_spec({"kind": "registry_count", "entity": "participants", "activity": "a' OR 1=1 --"}))
    assert "'a'' OR 1=1 --'" in c.sql


def test_lookup_entity_values():
    from app.compiler.spec import entity_values
    assert entity_values(parse_spec({"kind": "participant_lookup", "inn": "915688255"})) == {("inn", "915688255")}
    assert entity_values(parse_spec({"kind": "broker_lookup", "broker_code": "BK045"})) == {("broker", "BK045")}
    assert entity_values(parse_spec({"kind": "registry_count", "entity": "contracts", "products": ["Карбамид"],
                                     "seller_inn": "975923536"})) == {("product", "Карбамид"), ("inn", "975923536")}
