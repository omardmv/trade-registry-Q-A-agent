"""Grammar plans compiled by app/plan/build.py vs independent hand-written SQL on the real data.

Per subject, one plan per shape: count, sum, avg/median, share, top_n, least, distinct, compare, trend, breakdown,
record extreme (shapes that do not exist for a subject are noted: companies and brokers have no dates; contract and
rate numbers are not summable).
"""
import pytest

from app.resolve import Resolver
from tests.plan_helpers import assert_rows, cols, run_plan


@pytest.fixture(scope="module")
def resolver(db):
    return Resolver(db)


def check(db, resolver, plan, sql, *names, question=""):
    res, built, _ = run_plan(db, resolver, plan, question)
    exp = db.query(sql, max_rows=10_000)
    got = cols(res, *names) if names else res.rows
    assert_rows(got, exp.rows)
    return res, built


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


# ---------------------------------------------------------------- deals
W24 = "product_canonical = 'Пшеница' AND year = 2024"


def test_deals_count_split(db, resolver):
    res, built = check(db, resolver, {"subject": "deals", "measures": [{"agg": "count"}],
                                      "filters": [F("product", "Пшеница"), F("year", 2024)]},
                       f"SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT is_cancelled) FROM deals WHERE {W24}")
    assert res.columns == ["deals_all", "deals_valid"] and "status_scope" in built.splits


def test_deals_sum_tonnes(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}],
                         "filters": [F("product", "Хлопок-сырец"), F("year", 2023)]},
          "SELECT SUM(volume_tonnes), SUM(volume_tonnes) FILTER (WHERE NOT is_cancelled) FROM deals "
          "WHERE product_canonical = 'Хлопок-сырец' AND year = 2023")


def test_deals_median_price_valid_only_when_asked(db, resolver):
    res, _ = check(db, resolver, {"subject": "deals", "measures": [{"agg": "median", "of": "deal_price_per_kg"}],
                                  "filters": [F("product", "Пшеница"), F("year", 2024), F("cancelled", False)]},
                   f"SELECT MEDIAN(amount_uzs / (volume_tonnes * 1000)) FROM deals WHERE {W24} AND NOT is_cancelled",
                   question="Медиана цены пшеницы 2024 без аннулированных")
    assert res.columns == ["median_deal_price_per_kg"]


def test_deals_weighted_and_simple_price(db, resolver):
    res, built = check(
        db, resolver, {"subject": "deals", "measures": [{"agg": "price_per_kg"}, {"agg": "avg", "of": "deal_price_per_kg"}],
                       "filters": [F("product", "Сахар"), F("year", 2023), F("cancelled", False)]},
        "SELECT SUM(amount_uzs) / (SUM(volume_tonnes) * 1000), AVG(amount_uzs / (volume_tonnes * 1000)) FROM deals "
        "WHERE product_canonical = 'Сахар' AND year = 2023 AND NOT is_cancelled", question="цена сахара, аннулированные не считать")
    assert "price_average" in built.splits


def test_deals_share_by_count_over_all_deals(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "share", "where": [F("contract_type", "Экспортный")]}],
                         "filters": [F("year", 2024)]},
          "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE contract_type = 'Экспортный') / COUNT(*), 2) FROM deals WHERE year = 2024")


def test_deals_share_by_amount_without_annulled(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "share", "of": "amount_uzs",
                                                          "where": [F("product", "Хлопок-сырец")]}],
                         "filters": [F("year", 2025)]},
          "SELECT ROUND(100.0 * SUM(amount_uzs) FILTER (WHERE product_canonical = 'Хлопок-сырец') / SUM(amount_uzs), 2) "
          "FROM deals WHERE year = 2025 AND NOT is_cancelled")


def test_deals_cancelled_share(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "cancelled_share"}], "filters": [F("year", 2025)]},
          "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE is_cancelled) / COUNT(*), 2) FROM deals WHERE year = 2025")


def test_deals_top_sellers(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                         "filters": [F("product", "Сахар"), F("year", 2025)], "group_by": ["seller"], "limit": 3},
          "SELECT d.seller_inn, p.name, SUM(d.amount_uzs) s FROM deals d JOIN participants p ON p.inn = d.seller_inn "
          "WHERE d.product_canonical = 'Сахар' AND d.year = 2025 AND NOT d.is_cancelled GROUP BY 1, 2 "
          "ORDER BY s DESC, 1 LIMIT 3", "seller_inn", "seller_name", "sum_amount_uzs")


def test_deals_least_region(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2023)],
                         "group_by": ["region"], "order": "asc", "limit": 1},
          "SELECT region, COUNT(*) n FROM deals WHERE year = 2023 AND NOT is_cancelled GROUP BY 1 ORDER BY n, 1 LIMIT 1")


def test_deals_distinct_brokers_either_side(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "distinct", "of": "broker"}],
                         "filters": [F("product", "Портландцемент"), F("year", 2022)]},
          "SELECT (SELECT COUNT(DISTINCT b) FROM (SELECT seller_broker b FROM deals WHERE product_canonical = "
          "'Портландцемент' AND year = 2022 UNION ALL SELECT buyer_broker FROM deals WHERE product_canonical = "
          "'Портландцемент' AND year = 2022)), (SELECT COUNT(DISTINCT b) FROM (SELECT seller_broker b FROM deals WHERE "
          "product_canonical = 'Портландцемент' AND year = 2022 AND NOT is_cancelled UNION ALL SELECT buyer_broker FROM "
          "deals WHERE product_canonical = 'Портландцемент' AND year = 2022 AND NOT is_cancelled))")


def test_deals_distinct_sellers(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "distinct", "of": "seller"}],
                         "filters": [F("product", "Пшеница"), F("year", 2024), F("cancelled", False)]},
          f"SELECT COUNT(DISTINCT seller_inn) FROM deals WHERE {W24} AND NOT is_cancelled",
          question="сколько продавцов, аннулированные не считаем")


def test_deals_compare_quarters(db, resolver):
    res, _ = check(db, resolver, {"subject": "deals", "measures": [{"agg": "count"}],
                                  "compare": [{"year": 2025, "quarter": 1}, {"year": 2026, "quarter": 1}]},
                   "SELECT year, COUNT(*) FROM deals WHERE quarter = 1 AND year IN (2025, 2026) GROUP BY 1 ORDER BY 1",
                   "year", "deals_all")
    a, b = cols(res, "deals_all")[0][0], cols(res, "deals_all")[1][0]
    assert cols(res, "deals_all_change_pct")[1][0] == pytest.approx(round(100.0 * (b / a - 1), 2))


def test_deals_compare_years_sum(db, resolver):
    res, _ = check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                                  "filters": [F("cancelled", False)], "compare": [{"year": 2023}, {"year": 2024}]},
                   "SELECT year, SUM(amount_uzs) FROM deals WHERE year IN (2023, 2024) AND NOT is_cancelled GROUP BY 1 "
                   "ORDER BY 1", "year", "sum_amount_uzs", question="без аннулированных")
    assert res.columns[-1] == "change_pct"


def test_deals_monthly_trend(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}],
                         "filters": [F("product", "Пшеница"), F("year", 2024)], "group_by": ["month"]},
          f"SELECT year, month, SUM(volume_tonnes) FROM deals WHERE {W24} AND NOT is_cancelled GROUP BY 1, 2 ORDER BY 1, 2")


def test_deals_breakdown_by_family_products(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}],
                         "filters": [F("product_family", "Хлопок"), F("year", 2025)], "group_by": ["product"]},
          "SELECT product_canonical, SUM(volume_tonnes) t FROM deals WHERE product_family = 'Хлопок' AND year = 2025 "
          "AND NOT is_cancelled GROUP BY 1 ORDER BY t DESC LIMIT 10")


def test_deals_breakdown_contract_form(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_usd"}],
                         "filters": [F("year", 2023)], "group_by": ["contract_form"]},
          "SELECT contract_form, SUM(amount_usd) s FROM deals WHERE year = 2023 AND NOT is_cancelled GROUP BY 1 "
          "ORDER BY s DESC, 1")


def test_deals_record_extreme(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                         "group_by": ["deal"], "limit": 1},
          "SELECT deal_id, deal_date FROM deals WHERE NOT is_cancelled ORDER BY amount_uzs DESC, deal_id LIMIT 1",
          "deal_id", "deal_date")


def test_deals_cheapest_deal_per_kg(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "min", "of": "deal_price_per_kg"}],
                         "filters": [F("product", "Хлопок-сырец"), F("year", 2025)], "group_by": ["deal"],
                         "order": "asc", "limit": 1},
          "SELECT deal_id, amount_uzs / (volume_tonnes * 1000) p FROM deals WHERE product_canonical = 'Хлопок-сырец' "
          "AND year = 2025 AND NOT is_cancelled ORDER BY p, deal_id LIMIT 1", "deal_id", "min_deal_price_per_kg")


def test_deals_lookup_shows_annulled_deal(db, resolver):
    deal = db.query("SELECT MIN(deal_id) FROM deals WHERE is_cancelled").rows[0][0]
    res, _ = check(db, resolver, {"subject": "deals", "filters": [F("deal", str(deal))], "group_by": ["deal"]},
                   f"SELECT deal_id, buyer_inn, seller_inn, amount_uzs, status FROM deals WHERE deal_id = {deal}",
                   "deal_id", "buyer_inn", "seller_inn", "amount_uzs", "status")


def test_deals_broker_ranking_counts_deal_once(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                         "group_by": ["broker"], "limit": 3},
          "SELECT b, COUNT(DISTINCT deal_id) n FROM (SELECT deal_id, seller_broker b FROM deals WHERE year = 2025 AND "
          "NOT is_cancelled UNION ALL SELECT deal_id, buyer_broker FROM deals WHERE year = 2025 AND NOT is_cancelled) "
          "GROUP BY 1 ORDER BY n DESC, 1 LIMIT 3", "broker_code", "deals")


def test_deals_participant_ranked_per_side(db, resolver):
    res, built = check(
        db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                       "filters": [F("year", 2024)], "group_by": ["participant"], "limit": 2},
        "SELECT * FROM (SELECT 'buyer', buyer_inn, SUM(amount_uzs) s FROM deals WHERE year = 2024 AND NOT is_cancelled "
        "GROUP BY 2 ORDER BY s DESC, 2 LIMIT 2) UNION ALL SELECT * FROM (SELECT 'seller', seller_inn, SUM(amount_uzs) s "
        "FROM deals WHERE year = 2024 AND NOT is_cancelled GROUP BY 2 ORDER BY s DESC, 2 LIMIT 2)",
        "role", "inn", "sum_amount_uzs")
    assert "role" in built.splits


def test_deals_two_measures_two_rankings(db, resolver):
    res, built, _ = run_plan(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"},
                                                                          {"agg": "sum", "of": "amount_uzs"}],
                                         "filters": [F("year", 2024)], "group_by": ["product"], "limit": 2})
    exp_t = db.query("SELECT product_canonical FROM deals WHERE year = 2024 AND NOT is_cancelled GROUP BY 1 "
                     "ORDER BY SUM(volume_tonnes) DESC NULLS LAST, 1 LIMIT 2").rows
    exp_s = db.query("SELECT product_canonical FROM deals WHERE year = 2024 AND NOT is_cancelled GROUP BY 1 "
                     "ORDER BY SUM(amount_uzs) DESC, 1 LIMIT 2").rows
    assert cols(res, "product") == [tuple(r) for r in exp_t + exp_s] and "measure" in built.splits


def test_deals_volume_ranked_within_unit(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "volume"}], "filters": [F("year", 2025)],
                         "group_by": ["product"], "limit": 1},
          "SELECT unit, product_canonical, s FROM (SELECT unit, product_canonical, SUM(volume) s, ROW_NUMBER() OVER "
          "(PARTITION BY unit ORDER BY SUM(volume) DESC, product_canonical) rn FROM deals WHERE year = 2025 AND NOT "
          "is_cancelled GROUP BY 1, 2) WHERE rn = 1 ORDER BY unit", "unit", "product", "sum_volume")


def test_deals_top_per_year(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                         "filters": [F("year", [2024, 2025], "between")], "group_by": ["year", "product"], "limit": 2},
          "SELECT year, product_canonical, s FROM (SELECT year, product_canonical, SUM(amount_uzs) s, ROW_NUMBER() OVER "
          "(PARTITION BY year ORDER BY SUM(amount_uzs) DESC, product_canonical) rn FROM deals WHERE year BETWEEN 2024 "
          "AND 2025 AND NOT is_cancelled GROUP BY 1, 2) WHERE rn <= 2 ORDER BY 1, 3 DESC")


def test_deals_product_variant_expansion(db, resolver):
    names = resolver.variant_names("АИ-92")
    assert names and all("АИ-92" in n.upper() for n in names)
    res, built = check(db, resolver, {"subject": "deals", "measures": [{"agg": "count"}],
                                      "filters": [F("year", 2025), F("product_variant", "АИ-92")]},
                       "SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT is_cancelled) FROM deals WHERE year = 2025 AND "
                       "product_canonical = 'Бензин автомобильный' AND product_name_full ILIKE '%АИ-92%'")
    assert f"variant_spellings_{len(names)}" in built.notes


def test_deals_cancelled_share_by_year_with_valid_counts(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "cancelled_share"}, {"agg": "count"}],
                         "group_by": ["year"]},
          "SELECT year, ROUND(100.0 * COUNT(*) FILTER (WHERE is_cancelled) / COUNT(*), 2), COUNT(*) FILTER (WHERE NOT "
          "is_cancelled) FROM deals GROUP BY 1 ORDER BY 1")


def test_deals_buyer_region_top(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                         "filters": [F("product", "Бензин автомобильный"), F("year", 2025)],
                         "group_by": ["buyer_region"], "limit": 3},
          "SELECT buyer_region, SUM(amount_uzs) s FROM deals WHERE product_canonical = 'Бензин автомобильный' AND "
          "year = 2025 AND NOT is_cancelled GROUP BY 1 ORDER BY s DESC LIMIT 3")


def test_deals_date_range_and_bool(db, resolver):
    check(db, resolver, {"subject": "deals", "measures": [{"agg": "count"}],
                         "filters": [F("deal_date", ["2024-03-01", "2024-03-31"], "between"),
                                     F("payment_late", True)]},
          "SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT is_cancelled) FROM deals WHERE deal_date BETWEEN "
          "'2024-03-01' AND '2024-03-31' AND payment_late")


def test_deals_literal_escaping(db, resolver):
    res, built, _ = run_plan(db, resolver, {"subject": "deals", "measures": [{"agg": "count"}],
                                            "filters": [F("product", "O'Reilly")]})
    assert "'O''Reilly'" in built.sql and res.rows[0][0] == 0


# ---------------------------------------------------------------- contracts
def test_contracts_count(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "count"}],
                         "filters": [F("contract_form", "Форвард"), F("year", 2025)]},
          "SELECT COUNT(*) FROM contracts WHERE contract_form = 'Форвард' AND year(registered_date) = 2025")


def test_contracts_avg_and_median(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "avg", "of": "payment_term_days"},
                                                              {"agg": "median", "of": "validity_days"}]},
          "SELECT AVG(payment_term_days), MEDIAN(valid_until - registered_date) FROM contracts")


def test_contracts_share(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "share", "where": [F("contract_form", "Форвард")]}],
                         "filters": [F("year", 2024)]},
          "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE contract_form = 'Форвард') / COUNT(*), 2) FROM contracts "
          "WHERE year(registered_date) = 2024")


def test_contracts_top_sellers(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "count"}], "group_by": ["seller"], "limit": 3},
          "SELECT seller_inn, COUNT(*) n FROM contracts GROUP BY 1 ORDER BY n DESC, 1 LIMIT 3", "seller_inn", "contracts")


def test_contracts_least_product(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "count"}], "filters": [F("year", 2023)],
                         "group_by": ["product"], "order": "asc", "limit": 1},
          "SELECT product_canonical, COUNT(*) n FROM contracts WHERE year(registered_date) = 2023 GROUP BY 1 "
          "ORDER BY n, 1 LIMIT 1")


def test_contracts_distinct_products(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "distinct", "of": "product"}],
                         "filters": [F("year", 2024)]},
          "SELECT COUNT(DISTINCT product_canonical) FROM contracts WHERE year(registered_date) = 2024")


def test_contracts_compare(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "count"}],
                         "compare": [{"year": 2023}, {"year": 2024}]},
          "SELECT year(registered_date), COUNT(*) FROM contracts WHERE year(registered_date) IN (2023, 2024) "
          "GROUP BY 1 ORDER BY 1", "year", "contracts")


def test_contracts_quarterly_trend(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                         "group_by": ["quarter"]},
          "SELECT year(registered_date), quarter(registered_date), COUNT(*) FROM contracts WHERE "
          "year(registered_date) = 2025 GROUP BY 1, 2 ORDER BY 1, 2")


def test_contracts_breakdown(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "avg", "of": "delivery_term_days"}],
                         "group_by": ["contract_form"]},
          "SELECT contract_form, AVG(delivery_term_days) a FROM contracts GROUP BY 1 ORDER BY a DESC, 1")


def test_contracts_record_extreme(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "max", "of": "validity_days"}],
                         "group_by": ["contract"], "limit": 1},
          "SELECT contract_id, valid_until - registered_date d FROM contracts ORDER BY d DESC, contract_id LIMIT 1",
          "contract_id", "max_validity_days")


def test_contracts_active_on(db, resolver):
    check(db, resolver, {"subject": "contracts", "measures": [{"agg": "count"}],
                         "filters": [F("active_on", "2025-06-01")]},
          "SELECT COUNT(*) FROM contracts WHERE registered_date <= DATE '2025-06-01' AND valid_until >= DATE '2025-06-01'")


# ---------------------------------------------------------------- companies (no dates: no compare / trend)
def test_companies_count(db, resolver):
    check(db, resolver, {"subject": "companies", "measures": [{"agg": "count"}],
                         "filters": [F("role", "Покупатель"), F("vat_payer", True), F("region", "Samarqand viloyati")]},
          "SELECT COUNT(*) FROM participants WHERE role = 'Покупатель' AND vat_payer AND region = 'Samarqand viloyati'")


def test_companies_sum_and_avg(db, resolver):
    check(db, resolver, {"subject": "companies", "measures": [{"agg": "sum", "of": "deals_as_buyer"},
                                                              {"agg": "avg", "of": "deals_as_seller"}],
                         "filters": [F("activity", "Кластер")]},
          "SELECT SUM(deals_as_buyer), AVG(deals_as_seller) FROM participants WHERE activity = 'Кластер'")


def test_companies_share(db, resolver):
    check(db, resolver, {"subject": "companies", "measures": [{"agg": "share", "where": [F("vat_payer", True)]}]},
          "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE vat_payer) / COUNT(*), 2) FROM participants")


def test_companies_top_and_least_regions(db, resolver):
    check(db, resolver, {"subject": "companies", "measures": [{"agg": "count"}], "group_by": ["region"], "limit": 3},
          "SELECT region, COUNT(*) n FROM participants GROUP BY 1 ORDER BY n DESC, 1 LIMIT 3")
    check(db, resolver, {"subject": "companies", "measures": [{"agg": "count"}], "group_by": ["activity"],
                         "order": "asc", "limit": 1},
          "SELECT activity, COUNT(*) n FROM participants GROUP BY 1 ORDER BY n, 1 LIMIT 1")


def test_companies_distinct_and_breakdown(db, resolver):
    check(db, resolver, {"subject": "companies", "measures": [{"agg": "distinct", "of": "activity"}]},
          "SELECT COUNT(DISTINCT activity) FROM participants")
    check(db, resolver, {"subject": "companies", "measures": [{"agg": "count"}], "group_by": ["role"]},
          "SELECT role, COUNT(*) n FROM participants GROUP BY 1 ORDER BY n DESC, 1")


def test_companies_record_extreme(db, resolver):
    check(db, resolver, {"subject": "companies", "measures": [{"agg": "max", "of": "deals_as_buyer"}],
                         "group_by": ["company"], "limit": 1},
          "SELECT inn, name FROM participants ORDER BY deals_as_buyer DESC, inn LIMIT 1", "inn", "name")


# ---------------------------------------------------------------- brokers (no dates)
def test_brokers_count_sum_avg(db, resolver):
    check(db, resolver, {"subject": "brokers", "measures": [{"agg": "count"}, {"agg": "sum", "of": "deals_as_seller_broker"}],
                         "filters": [F("region", "Toshkent shahri")]},
          "SELECT COUNT(*), SUM(deals_as_seller_broker) FROM brokers WHERE region = 'Toshkent shahri'")
    check(db, resolver, {"subject": "brokers", "measures": [{"agg": "median", "of": "deals_as_buyer_broker"}]},
          "SELECT MEDIAN(deals_as_buyer_broker) FROM brokers")


def test_brokers_share_distinct_breakdown(db, resolver):
    check(db, resolver, {"subject": "brokers", "measures": [{"agg": "share", "where": [F("region", "Toshkent shahri")]}]},
          "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE region = 'Toshkent shahri') / COUNT(*), 2) FROM brokers")
    check(db, resolver, {"subject": "brokers", "measures": [{"agg": "distinct", "of": "region"}]},
          "SELECT COUNT(DISTINCT region) FROM brokers")
    check(db, resolver, {"subject": "brokers", "measures": [{"agg": "count"}], "group_by": ["region"]},
          "SELECT region, COUNT(*) n FROM brokers GROUP BY 1 ORDER BY n DESC, 1")


def test_brokers_top_least_record(db, resolver):
    check(db, resolver, {"subject": "brokers", "measures": [{"agg": "sum", "of": "deals_as_buyer_broker"}],
                         "group_by": ["broker"], "limit": 3},
          "SELECT broker_code, deals_as_buyer_broker FROM brokers ORDER BY 2 DESC, 1 LIMIT 3",
          "broker_code", "sum_deals_as_buyer_broker")
    check(db, resolver, {"subject": "brokers", "measures": [{"agg": "count"}], "group_by": ["region"],
                         "order": "asc", "limit": 1},
          "SELECT region, COUNT(*) n FROM brokers GROUP BY 1 ORDER BY n, 1 LIMIT 1")


# ---------------------------------------------------------------- usd_rates (rate is not summable; no dimensions)
def test_rates_count_avg_median(db, resolver):
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "count"}, {"agg": "avg", "of": "rate"}],
                         "filters": [F("year", 2024)]},
          "SELECT COUNT(*), AVG(rate) FROM usd_rates WHERE year(rate_date) = 2024")
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "median", "of": "rate"}],
                         "filters": [F("year", 2023), F("month", 5)]},
          "SELECT MEDIAN(rate) FROM usd_rates WHERE year(rate_date) = 2023 AND month(rate_date) = 5")


def test_rates_on_a_day(db, resolver):
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
                         "filters": [F("rate_date", "2024-03-15")]},
          "SELECT rate FROM usd_rates WHERE rate_date = DATE '2024-03-15'", question="Курс на 15.03.2024")


def test_rates_share_of_days(db, resolver):
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "share", "where": [F("rate", 12500, ">=")]}],
                         "filters": [F("year", 2024)]},
          "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE rate >= 12500) / COUNT(*), 2) FROM usd_rates "
          "WHERE year(rate_date) = 2024")


def test_rates_top_least_record(db, resolver):
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "max", "of": "rate"}], "filters": [F("year", 2024)],
                         "group_by": ["day"], "limit": 3},
          "SELECT rate_date, rate FROM usd_rates WHERE year(rate_date) = 2024 ORDER BY rate DESC, rate_date LIMIT 3")
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "min", "of": "rate"}], "filters": [F("year", 2022)],
                         "group_by": ["day"], "order": "asc", "limit": 1},
          "SELECT rate_date, rate FROM usd_rates WHERE year(rate_date) = 2022 ORDER BY rate, rate_date LIMIT 1")


def test_rates_compare_trend_breakdown(db, resolver):
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}],
                         "compare": [{"year": 2023}, {"year": 2024}]},
          "SELECT year(rate_date), AVG(rate) FROM usd_rates WHERE year(rate_date) IN (2023, 2024) GROUP BY 1 ORDER BY 1",
          "year", "avg_rate")
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "avg", "of": "rate"}], "filters": [F("year", 2025)],
                         "group_by": ["month"]},
          "SELECT year(rate_date), month(rate_date), AVG(rate) FROM usd_rates WHERE year(rate_date) = 2025 "
          "GROUP BY 1, 2 ORDER BY 1, 2")
    check(db, resolver, {"subject": "usd_rates", "measures": [{"agg": "max", "of": "rate"}], "group_by": ["year"]},
          "SELECT year(rate_date), MAX(rate) FROM usd_rates GROUP BY 1 ORDER BY 1")
