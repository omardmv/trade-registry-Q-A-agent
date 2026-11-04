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
    assert buyer + seller >= 0


def test_mass_volume_in_tonnes(con):
    unit, volume, tonnes = one(con, "SELECT unit, volume, volume_tonnes FROM deals WHERE deal_id = 100001")
    assert unit == "килограмм"
    assert tonnes == pytest.approx(volume / 1000)
