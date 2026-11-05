"""Gaps of the grammar vs the old structured form: broker sides in one plan, tied extremes, lookup columns."""
import pytest

from app.plan.model import PlanError
from app.resolve import Resolver
from tests.plan_helpers import assert_rows, cols, prepare, run_plan


@pytest.fixture(scope="module")
def resolver(db):
    return Resolver(db)


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def busy_broker(db):
    return db.query("SELECT seller_broker FROM deals GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1").rows[0][0]


def test_broker_side_split_in_one_plan(db, resolver):
    code = busy_broker(db)
    res, built, _ = run_plan(db, resolver, {"subject": "deals", "measures": [{"agg": "count"}],
                                            "filters": [F("broker", code), F("year", 2024)],
                                            "group_by": ["broker_side"]})
    exp = db.query(f"""SELECT side, COUNT(*) FROM (
        SELECT 'seller' AS side FROM deals WHERE seller_broker = '{code}' AND year = 2024 AND NOT is_cancelled
        UNION ALL SELECT 'buyer' FROM deals WHERE buyer_broker = '{code}' AND year = 2024 AND NOT is_cancelled)
        GROUP BY side ORDER BY 2 DESC, 1""")
    assert_rows(cols(res, "broker_side", "deals"), exp.rows)
    assert "broker_side_split" in built.notes


def test_broker_side_needs_a_broker_filter():
    with pytest.raises(PlanError, match="broker"):
        prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["broker_side"]})
    with pytest.raises(PlanError, match="group_by"):
        prepare({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("broker_side", "seller")]})


def test_usd_extreme_returns_every_tied_date(db, resolver):
    # a month where the maximum rate occurs on several days
    year, month, n = db.query("""SELECT y, m, COUNT(*) FROM (SELECT year(rate_date) y, month(rate_date) m, rate,
        MAX(rate) OVER (PARTITION BY year(rate_date), month(rate_date)) mx FROM usd_rates) WHERE rate = mx
        GROUP BY y, m ORDER BY 3 DESC, 1, 2 LIMIT 1""").rows[0]
    assert n > 1
    res, built, _ = run_plan(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "max", "of": "rate"}],
                                            "filters": [F("year", year), F("month", month)], "group_by": ["day"],
                                            "limit": 1})
    w = f"year(rate_date) = {year} AND month(rate_date) = {month}"
    exp = db.query(f"SELECT rate_date, rate FROM usd_rates WHERE {w} "
                   f"AND rate = (SELECT MAX(rate) FROM usd_rates WHERE {w}) ORDER BY rate_date")
    assert_rows(cols(res, "rate_date", "max_rate"), exp.rows)
    assert len(res.rows) == n and "ties_kept" in built.notes


def test_company_lookup_has_derived_columns(db, resolver):
    inn = db.query("SELECT buyer_inn FROM deals GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1").rows[0][0]
    res, _, _ = run_plan(db, resolver, {"subject": "companies", "filters": [F("company", inn)],
                                        "group_by": ["company"]})
    exp = db.query(f"""SELECT
        (SELECT COUNT(*) FROM deals d WHERE d.buyer_inn = '{inn}' AND NOT d.is_cancelled),
        (SELECT COUNT(*) FROM deals d WHERE d.seller_inn = '{inn}' AND NOT d.is_cancelled),
        (SELECT SUM(d.amount_uzs) FROM deals d WHERE d.buyer_inn = '{inn}' AND NOT d.is_cancelled),
        (SELECT MIN(d.deal_date) FROM deals d WHERE d.buyer_inn = '{inn}' OR d.seller_inn = '{inn}'),
        (SELECT MAX(d.deal_date) FROM deals d WHERE d.buyer_inn = '{inn}' OR d.seller_inn = '{inn}')""")
    assert_rows(cols(res, "deals_as_buyer_valid", "deals_as_seller_valid", "bought_uzs_valid", "first_deal_date",
                     "last_deal_date"), exp.rows)


def test_broker_lookup_has_derived_columns(db, resolver):
    code = busy_broker(db)
    res, _, _ = run_plan(db, resolver, {"subject": "brokers", "filters": [F("broker", code)], "group_by": ["broker"]})
    exp = db.query(f"""SELECT
        (SELECT COUNT(*) FROM deals d WHERE d.seller_broker = '{code}' AND NOT d.is_cancelled),
        (SELECT COUNT(*) FROM deals d WHERE d.buyer_broker = '{code}' AND NOT d.is_cancelled),
        (SELECT COUNT(*) FROM deals d WHERE d.seller_broker = '{code}' OR d.buyer_broker = '{code}')""")
    assert_rows(cols(res, "deals_as_seller_broker_valid", "deals_as_buyer_broker_valid", "deals_either_side"),
                exp.rows)


def test_contract_lookup_has_derived_columns(db, resolver):
    cid = db.query("SELECT contract_id FROM deals GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1").rows[0][0]
    res, _, _ = run_plan(db, resolver, {"subject": "contracts", "filters": [F("contract", cid)],
                                        "group_by": ["contract"]})
    exp = db.query(f"""SELECT
        (SELECT COUNT(*) FROM deals d WHERE d.contract_id = {cid}),
        (SELECT COUNT(*) FROM deals d WHERE d.contract_id = {cid} AND NOT d.is_cancelled),
        (SELECT string_agg(DISTINCT d.seller_broker, ', ' ORDER BY d.seller_broker) FROM deals d
           WHERE d.contract_id = {cid})""")
    assert_rows(cols(res, "deals_all", "deals_valid", "seller_brokers"), exp.rows)
