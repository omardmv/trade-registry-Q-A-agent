import datetime as dt

import duckdb
import pytest


@pytest.fixture(scope="module")
def con(db_path):
    c = duckdb.connect(str(db_path), read_only=True)
    yield c
    c.close()


def one(con, sql, params=()):
    return con.execute(sql, params).fetchone()


def test_row_counts_match_readme(con):
    assert one(con, "SELECT COUNT(*) FROM deals")[0] == 500_000
    years = dict(con.execute("SELECT year, COUNT(*) FROM deals GROUP BY year").fetchall())
    assert years == {2022: 84_299, 2023: 93_985, 2024: 106_647, 2025: 115_763, 2026: 99_306}
    assert one(con, "SELECT COUNT(*) FROM contracts")[0] == 6_616
    assert one(con, "SELECT COUNT(*) FROM participants")[0] == 14_392
    assert one(con, "SELECT COUNT(*) FROM brokers")[0] == 120
    assert one(con, "SELECT COUNT(*) FROM usd_rates")[0] == 1_765


def test_domestic_deal_parsed(con):
    row = one(con, """SELECT deal_date, amount, volume, currency, amount_uzs, is_cancelled, contract_id
                      FROM deals WHERE deal_id = 387830""")
    assert row == (dt.date(2025, 1, 2), 85_256_750.0, 25.0, "UZS", 85_256_750.0, False, 102267)


def test_export_deal_converted_with_deal_date_rate(con):
    amount, currency, amount_uzs, rate = one(con, """
        SELECT d.amount, d.currency, d.amount_uzs, r.rate
        FROM deals d JOIN usd_rates r ON r.rate_date = d.deal_date WHERE d.deal_id = 280091""")
    assert currency == "USD"
    assert amount == pytest.approx(185_176.24)
    assert amount_uzs == pytest.approx(amount * rate)


def test_cancelled_and_status_counts(con):
    assert one(con, "SELECT COUNT(*) FROM deals WHERE is_cancelled")[0] == 33_719
    assert one(con, "SELECT COUNT(*) FROM deals WHERE status = 'Исполнен'")[0] == 431_061


def test_contract_region_mapped_and_product_canonical(con):
    assert one(con, "SELECT region, product_canonical FROM contracts WHERE contract_id = 100001") == (
        "Farg‘ona viloyati", "Профиль алюминиевый")


def test_no_nulls_in_critical_columns(con):
    assert one(con, """SELECT COUNT(*) FROM deals WHERE amount IS NULL OR quantity IS NULL OR lot IS NULL
                       OR currency IS NULL OR usd_rate IS NULL OR product_canonical IS NULL""")[0] == 0


def test_participant_normalized_name_and_counts(con):
    name_norm, buyer, seller = one(con, """SELECT name_norm, deals_as_buyer, deals_as_seller
                                           FROM participants WHERE inn = '915688255'""")
    assert name_norm == "harayom kimyo"
    exp_buyer = one(con, "SELECT COUNT(*) FROM deals WHERE buyer_inn = '915688255'")[0]
    exp_seller = one(con, "SELECT COUNT(*) FROM deals WHERE seller_inn = '915688255'")[0]
    assert (buyer, seller) == (exp_buyer, exp_seller)


def test_mass_volume_in_tonnes(con):
    unit, volume, tonnes = one(con, "SELECT unit, volume, volume_tonnes FROM deals WHERE deal_id = 100001")
    assert unit == "килограмм"
    assert tonnes == pytest.approx(volume / 1000)


def test_contract_attributes_denormalized_onto_deals(con):
    mismatched = one(con, """SELECT COUNT(*) FROM deals d JOIN contracts c USING (contract_id)
        WHERE d.category IS DISTINCT FROM c.category OR d.industry IS DISTINCT FROM c.industry
           OR d.product_group IS DISTINCT FROM c.product_group OR d.delivery_basis IS DISTINCT FROM c.delivery_basis
           OR d.packaging IS DISTINCT FROM c.packaging OR d.hs_code IS DISTINCT FROM c.hs_code
           OR d.product_name IS DISTINCT FROM c.product_name
           OR d.payment_term_days IS DISTINCT FROM c.payment_term_days
           OR d.delivery_term_days IS DISTINCT FROM c.delivery_term_days""")[0]
    assert mismatched == 0
    assert one(con, "SELECT COUNT(*) FROM deals WHERE category IS NULL OR hs_code IS NULL")[0] == 0


def test_participant_attributes_denormalized_onto_deals(con):
    mismatched = one(con, """SELECT COUNT(*) FROM deals d
        JOIN participants pb ON pb.inn = d.buyer_inn JOIN participants ps ON ps.inn = d.seller_inn
        WHERE d.buyer_region IS DISTINCT FROM pb.region OR d.seller_region IS DISTINCT FROM ps.region
           OR d.buyer_activity IS DISTINCT FROM pb.activity OR d.seller_activity IS DISTINCT FROM ps.activity""")[0]
    assert mismatched == 0
    assert one(con, "SELECT COUNT(*) FROM deals WHERE buyer_region IS NULL OR seller_activity IS NULL")[0] == 0


def test_derived_time_columns(con):
    row = one(con, "SELECT deal_date, deal_time, quarter, deal_hour FROM deals WHERE deal_id = 387830")
    deal_date, deal_time, quarter, hour = row
    assert quarter == (deal_date.month - 1) // 3 + 1
    assert hour == deal_time.hour
    assert one(con, "SELECT COUNT(*) FROM deals WHERE quarter <> (month + 2) // 3 OR deal_hour <> hour(deal_time)")[0] == 0


def test_derived_payment_and_delivery_columns(con):
    bad = one(con, """SELECT COUNT(*) FROM deals d JOIN contracts c USING (contract_id)
        WHERE d.days_to_payment IS DISTINCT FROM (d.payment_date - d.deal_date)
           OR d.days_to_delivery IS DISTINCT FROM (d.delivery_date - d.payment_date)""")[0]
    assert bad == 0
    late = one(con, """SELECT COUNT(*) FROM deals d JOIN contracts c USING (contract_id)
        WHERE d.delivery_date > d.payment_date + c.delivery_term_days""")[0]
    assert one(con, "SELECT COUNT(*) FROM deals WHERE delivery_late_days > 0")[0] == late
    assert one(con, """SELECT COUNT(*) FROM deals d JOIN contracts c USING (contract_id)
        WHERE d.delivery_late_days > 0
          AND d.delivery_late_days <> d.delivery_date - (d.payment_date + c.delivery_term_days)""")[0] == 0
    assert one(con, "SELECT COUNT(*) FROM deals WHERE delivery_date IS NULL AND delivery_late_days IS NOT NULL")[0] == 0
    # late payment: paid after the term, or unpaid with the term already over before 30.10.2026
    expected = one(con, """SELECT COUNT(*) FROM deals d JOIN contracts c USING (contract_id)
        WHERE d.payment_date > d.deal_date + c.payment_term_days
           OR (d.payment_date IS NULL AND d.deal_date + c.payment_term_days < DATE '2026-10-30')""")[0]
    assert one(con, "SELECT COUNT(*) FROM deals WHERE payment_late")[0] == expected
    assert one(con, "SELECT COUNT(*) FROM deals WHERE payment_late IS NULL")[0] == 0


def test_price_vs_start_pct(con):
    unit_price, start_price, pct = one(con, "SELECT unit_price, start_price, price_vs_start_pct FROM deals WHERE deal_id = 387830")
    assert pct == pytest.approx(100 * (unit_price / start_price - 1))
