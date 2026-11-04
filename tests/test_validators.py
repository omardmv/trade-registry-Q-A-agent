from app.agent.validators import validate_sql


def v(sql, allowed=frozenset(), question="", db=None, **kw):
    return validate_sql(sql, set(allowed), question, db, **kw)


def test_plain_select_ok_and_limit_added():
    r = v("SELECT COUNT(*) AS n FROM deals WHERE year = 2025")
    assert r.ok, r.errors
    assert r.sql.rstrip().endswith("LIMIT 1000")


def test_existing_limit_kept():
    r = v("SELECT deal_id FROM deals ORDER BY amount_uzs DESC LIMIT 5")
    assert r.ok and r.sql.count("LIMIT") == 1


def test_rejects_non_select_and_multiple_statements():
    assert not v("DELETE FROM deals").ok
    assert not v("SELECT 1; SELECT 2").ok


def test_trailing_semicolon_and_comment_is_one_statement():
    r = v("SELECT 1; -- x")
    assert r.ok, r.errors


def test_rejects_unknown_table_and_table_functions():
    assert any("unknown table" in e for e in v("SELECT * FROM secrets").errors)
    assert not v("SELECT * FROM read_csv('data/deals_2022.csv')").ok


def test_cte_names_allowed():
    assert v("WITH t AS (SELECT buyer_inn FROM deals) SELECT COUNT(*) FROM t").ok


def test_money_without_currency_rejected():
    r = v("SELECT SUM(amount) FROM deals")
    assert not r.ok and any("amount_uzs" in e for e in r.errors)
    assert v("SELECT currency, SUM(amount) FROM deals GROUP BY currency").ok


def test_name_filters_rejected():
    r = v("SELECT COUNT(*) FROM participants WHERE name LIKE '%Plast%'")
    assert not r.ok and any("resolve" in e for e in r.errors)
    assert not v("SELECT COUNT(*) FROM deals WHERE product_name_full ILIKE '%хлоп%'").ok


def test_ids_must_be_resolved_or_in_question():
    sql = "SELECT COUNT(*) FROM deals WHERE buyer_inn = '975923536'"
    assert not v(sql).ok
    assert v(sql, allowed={"975923536"}).ok
    assert v(sql, question="Сколько купила компания с ИНН 975923536?").ok
    assert not v("SELECT COUNT(*) FROM deals WHERE product_canonical = 'Золото'").ok
    assert v("SELECT COUNT(*) FROM deals WHERE seller_inn = '975923536'", check_ids=False).ok


def test_explain_catches_bad_column(db):
    r = v("SELECT nope FROM deals", db=db)
    assert not r.ok and r.errors


def test_returned_sql_regenerated_without_semicolon_or_comment():
    r = v("SELECT 1; -- x")
    assert r.ok, r.errors
    assert ";" not in r.sql and "--" not in r.sql


def test_union_gets_exactly_one_limit_and_explains(db):
    r = v("SELECT buyer_inn FROM deals UNION ALL SELECT seller_inn FROM deals", db=db)
    assert r.ok, r.errors
    assert r.sql.upper().count("LIMIT") == 1 and r.sql.rstrip().endswith("LIMIT 1000")


def test_wrapped_name_filters_rejected():
    assert not v("SELECT COUNT(*) FROM participants WHERE lower(name) = 'x'").ok
    r = v("SELECT COUNT(*) FROM participants p WHERE trim(p.name) LIKE '%x%'")
    assert not r.ok and any("resolve" in e for e in r.errors)


def test_wrapped_resolved_ids_still_checked():
    sql = "SELECT COUNT(*) FROM deals WHERE upper(buyer_broker) = 'BK001'"
    assert not v(sql).ok
    assert v(sql, allowed={"BK001"}).ok


import pytest

REJECTED = [
    "SELECT 1 FROM participants WHERE regexp_matches(name, 'x')",
    "SELECT 1 FROM deals WHERE contains(product_name_full, 'x')",
    "SELECT 1 FROM participants WHERE name SIMILAR TO 'x'",
    "SELECT 1 FROM participants WHERE name ~ 'x'",
    "SELECT 1 FROM participants WHERE starts_with(name, 'x')",
    "SELECT 1 FROM participants WHERE name GLOB 'x*'",
    "SELECT 1 FROM participants WHERE position('x' IN name) > 0",
    "SELECT 1 FROM deals WHERE starts_with(region, 'x')",
    "SELECT 1 FROM deals WHERE contains(product_canonical, 'x')",
    "SELECT 1 FROM deals WHERE buyer_inn = 123456789",
    "SELECT 1 FROM deals WHERE region BETWEEN 'A' AND 'Z'",
    "SELECT 1 FROM deals, LATERAL parquet_scan('x')",
    "SELECT 1 FROM deals, LATERAL read_ndjson('x')",
    "SELECT 1 FROM other.deals",
    "WITH x AS (DELETE FROM deals RETURNING *) SELECT * FROM x",
    "SELECT SUM(amount), MAX(currency) FROM deals",
    "SELECT SUM(amount) FROM deals WHERE currency IS NOT NULL",
]


@pytest.mark.parametrize("sql", REJECTED)
def test_review_probes_rejected(sql):
    assert not v(sql).ok


ACCEPTED = [
    "SELECT region FROM deals WHERE region IN (SELECT region FROM deals WHERE status = 'done')",
    "SELECT 1 FROM deals WHERE buyer_inn = '975923536'",
    "SELECT 1 FROM participants p JOIN deals d ON p.inn = d.buyer_inn",
    "SELECT 1 FROM deals WHERE lower(region) = 'samarqand viloyati'",
    "SELECT currency, SUM(amount) FROM deals GROUP BY currency",
    "SELECT SUM(amount) FROM deals WHERE currency = 'UZS'",
    "SELECT name FROM participants ORDER BY name",
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_structural_accepted(sql):
    r = v(sql, allowed={"975923536", "samarqand viloyati"})
    assert r.ok, r.errors


def test_huge_limit_clamped():
    r = v("SELECT deal_id FROM deals LIMIT 100000000")
    assert r.ok and r.sql.endswith("LIMIT 1000") and "100000000" not in r.sql


def test_round_trip_filter_qualify_cast_explain(db):
    sql = ("SELECT deal_id, COUNT(*) FILTER (WHERE amount_uzs > 0) OVER () AS n, DATE '2025-01-01'::DATE AS d "
           "FROM deals QUALIFY ROW_NUMBER() OVER (ORDER BY deal_id) = 1")
    r = v(sql, db=db)
    assert r.ok, r.errors


REJECTED_2 = [
    # case
    "SELECT 1 FROM participants WHERE NAME = 'x'",
    'SELECT 1 FROM participants WHERE "Name" LIKE \'%x%\'',
    "SELECT 1 FROM deals WHERE REGION = 'Atlantis'",
    "SELECT SUM(AMOUNT) FROM deals",
    # aliases
    "WITH t AS (SELECT amount AS a FROM deals) SELECT SUM(a) FROM t",
    "SELECT SUM(a) FROM (SELECT unit_price AS a FROM deals)",
    "WITH t AS (SELECT name AS n FROM participants) SELECT * FROM t WHERE n = 'x'",
    "SELECT name AS n FROM participants WHERE n = 'x'",
    "SELECT * FROM (SELECT region AS r FROM deals) WHERE r = 'Atlantis'",
    "SELECT * FROM (SELECT buyer_inn AS b FROM deals) WHERE b = '12345678' || '9'",
    # CASE / IF
    "SELECT SUM(CASE WHEN region = 'Atlantis' THEN 1 END) FROM deals",
    "SELECT SUM(CASE WHEN contains(name, 'X') THEN 1 ELSE 0 END) FROM participants",
    "SELECT IF(region = 'Atlantis', 1, 0) FROM deals",
    # currency conjunct
    "SELECT SUM(amount) FROM deals WHERE currency = 'UZS' OR currency = 'USD'",
    "SELECT SUM(amount) FROM deals WHERE NOT currency = 'UZS'",
]


@pytest.mark.parametrize("sql", REJECTED_2)
def test_second_review_rejected(sql):
    assert not v(sql).ok


ACCEPTED_2 = [
    "SELECT Currency, SUM(Amount) FROM deals GROUP BY CURRENCY",
    "SELECT p.name AS buyer_name, SUM(d.amount_uzs) AS total FROM deals d "
    "JOIN participants p ON p.inn = d.buyer_inn GROUP BY p.name ORDER BY total DESC",
    "WITH t AS (SELECT buyer_inn, SUM(amount_uzs) AS total FROM deals GROUP BY buyer_inn) "
    "SELECT * FROM t WHERE total > 0",
    "SELECT SUM(CASE WHEN region = 'Samarqand viloyati' THEN 1 END) FROM deals",
    "SELECT 1 FROM deals WHERE region = ('Samarqand viloyati')",
    "SELECT currency, SUM(amount) FROM deals GROUP BY 1",
    "SELECT currency, SUM(amount) FROM deals GROUP BY ALL",
    "SELECT SUM(amount) FROM deals WHERE year = 2025 AND currency = 'UZS'",
]


@pytest.mark.parametrize("sql", ACCEPTED_2)
def test_second_review_accepted(sql):
    r = v(sql, allowed={"Samarqand viloyati"})
    assert r.ok, r.errors


@pytest.mark.parametrize("sql", [
    "WITH t(a) AS (SELECT amount FROM deals) SELECT SUM(a) FROM t",
    "SELECT * FROM (SELECT name FROM participants) AS x(n) WHERE n = 'x'",
])
def test_column_list_aliases_rejected(sql):
    r = v(sql)
    assert not r.ok and any("column-list" in e for e in r.errors)


REJECTED_3 = [
    "SELECT count_if(product_name_full ILIKE '%хлоп%') FROM deals",
    "SELECT countif(name LIKE '%x%') FROM participants",
    "SELECT sum(contains(name, 'x')::INT) FROM participants",
    "SELECT count_if(region = 'Atlantis') FROM deals",
    "SELECT sum(region = 'Atlantis') FROM deals",
    "SELECT bool_or(region = 'Atlantis') FROM deals",
    # mixed currency
    "SELECT currency, SUM(amount) FROM deals GROUP BY currency UNION ALL SELECT 'all', SUM(amount) FROM deals",
    "SELECT (SELECT SUM(amount) FROM deals WHERE currency = 'UZS') + (SELECT SUM(amount) FROM deals)",
    "WITH a AS (SELECT currency, SUM(amount) AS s FROM deals GROUP BY currency) SELECT SUM(amount) FROM deals",
    "SELECT SUM(amount) FROM deals WHERE year IN (SELECT year FROM deals WHERE currency = 'UZS')",
    # whole-row / COLUMNS
    "SELECT 1 FROM deals WHERE COLUMNS('.*name.*') ILIKE '%x%'",
    "SELECT 1 FROM deals WHERE deals::VARCHAR LIKE '%x%'",
    "SELECT to_json(deals) FROM deals",
    # empty literal
    "SELECT 1 FROM deals WHERE region = ''",
]


@pytest.mark.parametrize("sql", REJECTED_3)
def test_third_review_rejected(sql):
    assert not v(sql, allowed={"Samarqand viloyati"}).ok


ACCEPTED_3 = [
    "SELECT count_if(region = 'Samarqand viloyati') FROM deals",
    "SELECT SUM(amount) FILTER (WHERE currency = 'UZS') FROM deals",
    "SELECT p.name, d.buyer_inn, SUM(d.amount_uzs) FROM deals d JOIN participants p ON p.inn = d.buyer_inn "
    "GROUP BY p.name, d.buyer_inn ORDER BY 3 DESC",
    "SELECT COUNT(DISTINCT buyer_inn) FROM deals",
    "SELECT SUM(d.amount) FROM deals d WHERE d.currency = 'USD'",
]


@pytest.mark.parametrize("sql", ACCEPTED_3)
def test_third_review_accepted(sql):
    r = v(sql, allowed={"Samarqand viloyati"})
    assert r.ok, r.errors
