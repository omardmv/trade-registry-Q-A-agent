from app.agent.validators import validate_sql


def v(sql, allowed=frozenset(), question="", db=None, **kw):
    def pair(a):
        if isinstance(a, tuple):
            return a
        return ("inn" if a.isdigit() else "broker" if a.startswith("BK") else "region", a)
    return validate_sql(sql, {pair(a) for a in allowed}, question, db, **kw)


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
    r = v("SELECT COUNT(*) FROM deals; -- x")
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
    r = v("SELECT COUNT(*) FROM deals; -- x")
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
    sql = "SELECT COUNT(*) FROM deals WHERE trim(buyer_broker) = 'BK001'"
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
    "SELECT COUNT(*) FROM deals WHERE buyer_inn = '975923536'",
    "SELECT COUNT(*) FROM participants p JOIN deals d ON p.inn = d.buyer_inn",
    "SELECT COUNT(*) FROM deals WHERE trim(region) = 'Samarqand viloyati'",
    "SELECT currency, SUM(amount) FROM deals GROUP BY currency",
    "SELECT SUM(amount) FROM deals WHERE currency = 'UZS'",
    "SELECT name FROM participants ORDER BY name",
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_structural_accepted(sql):
    r = v(sql, allowed={"975923536", "Samarqand viloyati"})
    assert r.ok, r.errors


def test_huge_limit_clamped():
    r = v("SELECT deal_id FROM deals LIMIT 100000000")
    assert r.ok and r.sql.endswith("LIMIT 1000") and "100000000" not in r.sql


def test_round_trip_filter_qualify_cast_explain(db):
    sql = ("SELECT deal_id, COUNT(*) FILTER (WHERE amount_uzs > 0) OVER () AS n, deal_date::DATE AS d "
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
    "SELECT COUNT(*) FROM deals WHERE region = ('Samarqand viloyati')",
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


def test_region_not_grounded_by_question_substring():
    v = validate_sql("SELECT COUNT(*) FROM deals WHERE region = 'Ташкент'", set(), "сделки в Ташкентской области")
    assert not v.ok


def test_inn_token_in_question_accepted_but_not_prefix():
    sql = "SELECT COUNT(*) FROM deals WHERE buyer_inn = '975923536'"
    assert validate_sql(sql, set(), "сделки 975923536 за год").ok
    assert not validate_sql(sql, set(), "сделки 9759235361 за год").ok


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM deals WHERE contract_id = 123456",
    "SELECT COUNT(*) FROM contracts WHERE contract_id = 77777",
    "SELECT COUNT(*) FROM deals WHERE deal_id = 5",
])
def test_contract_deal_ids_must_be_grounded(sql):
    assert not v(sql, question="Сколько сделок?").ok


def test_contract_id_in_question_accepted():
    assert v("SELECT COUNT(*) FROM deals WHERE contract_id = 102267", question="сделки по 102267").ok


def test_region_case_sensitive_exact():
    sql = "SELECT COUNT(*) FROM deals WHERE region = 'TOSHKENT VILOYATI'"
    assert not v(sql, allowed={("region", "Toshkent viloyati")}).ok


R = ("region", "Toshkent viloyati")


@pytest.mark.parametrize("sql,allowed", [
    ("SELECT COUNT(*) FROM deals WHERE lower(region) = 'Toshkent viloyati'", {R}),
    ("SELECT COUNT(*) FROM deals WHERE upper(region) = 'Toshkent viloyati'", {R}),
    ("SELECT COUNT(*) FROM deals WHERE region = upper('Toshkent viloyati')", {R}),
    ("SELECT COUNT(*) FROM deals WHERE region IN (lower('Toshkent viloyati'))", {R}),
    ("SELECT COUNT(*) FROM deals WHERE lower(product_canonical) = 'Карбамид'", {("product", "Карбамид")}),
    ("SELECT COUNT(*) FROM deals WHERE lower(seller_broker) = 'BK001'", {("broker", "BK001")}),
    ("SELECT COUNT(*) FROM deals WHERE seller_broker = lower('BK001')", {("broker", "BK001")}),
    ("SELECT COUNT(*) FROM deals WHERE seller_broker = 'bk001'", {("broker", "BK001")}),
])
def test_case_folding_on_resolved_rejected(sql, allowed):
    assert not v(sql, allowed=allowed).ok


@pytest.mark.parametrize("sql,allowed", [
    ("SELECT COUNT(*) FROM deals WHERE trim(region) = 'Toshkent viloyati'", {R}),
    ("SELECT MAX(deal_id) AS m FROM deals", set()),
    ("SELECT COUNT(*) FROM deals WHERE contract_id IS NOT NULL", set()),
    ("SELECT COUNT(*) FROM deals WHERE contract_id IS NULL", set()),
])
def test_trim_and_id_null_accepted(sql, allowed):
    r = v(sql, allowed=allowed)
    assert r.ok, r.errors


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT 'Ташкент')",
    "WITH r AS (SELECT 'Ташкент' AS region) SELECT COUNT(*) FROM deals d WHERE d.region IN (SELECT region FROM r)",
    "SELECT COUNT(*) FROM deals d JOIN (SELECT 'Ташкент' AS rg) x ON d.region = x.rg",
    "SELECT * FROM (VALUES (1)) t",
])
def test_literal_projection_in_subquery_rejected(sql):
    assert not v(sql).ok


@pytest.mark.parametrize("sql", [
    "SELECT 'all' AS scope, COUNT(*) AS n FROM deals",
    "SELECT 'buyer' AS role, COUNT(*) AS n FROM deals UNION ALL SELECT 'seller', COUNT(*) FROM deals",
])
def test_top_level_labels_accepted(sql):
    r = v(sql)
    assert r.ok, r.errors


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT * FROM unnest(['Ташкент']))",
    "SELECT COUNT(*) FROM deals d, unnest(['Ташкент']) AS u WHERE d.region = u.unnest",
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT * FROM unnest(string_split('Ташкент,X', ',')))",
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT chr(84) || chr(111))",
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT CAST(123 AS VARCHAR))",
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT 123::VARCHAR)",
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT concat('a', 'b'))",
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT {'a': 'Ташкент'}.a)",
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT ['Ташкент'][1])",
    "SELECT COUNT(*) FROM deals WHERE region IN (SELECT MAP(['k'], ['Ташкент'])['k'])",
    "SELECT COUNT(*) FROM deals d JOIN (VALUES ('Ташкент')) x(rg) ON d.region = x.rg",
])
def test_non_table_sources_and_string_building_rejected(sql):
    assert not v(sql).ok


def test_tables_and_ctes_still_joinable():
    sql = ("WITH c AS (SELECT contract_id, contract_form FROM contracts) "
           "SELECT COUNT(*) AS n FROM deals d JOIN c ON d.contract_id = c.contract_id "
           "JOIN (SELECT deal_id FROM deals) s ON s.deal_id = d.deal_id")
    r = v(sql)
    assert r.ok, r.errors


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM deals WHERE contract_id IN (SELECT 123456)",
    "SELECT COUNT(*) FROM deals d JOIN (SELECT 123456 AS c) x ON d.contract_id = x.c",
    "WITH k AS (SELECT 100005 AS c) SELECT COUNT(*) FROM deals d WHERE d.contract_id IN (SELECT c FROM k)",
    "SELECT COUNT(*) FROM deals WHERE deal_id IN (SELECT 100000 + 5)",
    "SELECT COUNT(*) FROM deals WHERE seller_inn IN (SELECT concat(97592353, 6))",
    "SELECT COUNT(*) FROM deals d JOIN (SELECT 123456 AS contract_id) x USING (contract_id)",
])
def test_untraced_id_comparisons_rejected(sql):
    assert not v(sql, question="Сколько сделок?").ok


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) AS n FROM deals d JOIN contracts c ON d.contract_id = c.contract_id",
    "SELECT COUNT(*) AS n FROM deals d JOIN participants p ON p.inn = d.buyer_inn",
    "SELECT COUNT(*) AS n FROM deals WHERE region IN (SELECT region FROM deals WHERE year = 2025)",
    "WITH t AS (SELECT buyer_inn, SUM(amount_uzs) AS total FROM deals GROUP BY buyer_inn) "
    "SELECT * FROM t JOIN participants p ON p.inn = t.buyer_inn",
    "SELECT COUNT(*) AS n FROM deals d JOIN contracts c USING (contract_id)",
])
def test_traced_id_comparisons_accepted(sql):
    r = v(sql)
    assert r.ok, r.errors


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM deals d JOIN (SELECT 100005 AS contract_id) x ON d.contract_id = x.contract_id",
    "WITH k AS (SELECT 100005 AS contract_id) SELECT COUNT(*) FROM deals d "
    "WHERE d.contract_id IN (SELECT contract_id FROM k)",
    "WITH k AS (SELECT 100005 AS contract_id) SELECT COUNT(*) FROM deals d JOIN k ON d.contract_id = k.contract_id",
    "SELECT COUNT(*) FROM deals WHERE contract_id IN "
    "(SELECT contract_id FROM (SELECT 100005 AS contract_id) z)",
    "SELECT COUNT(*) FROM deals d JOIN (SELECT contract_id FROM (SELECT 100005 AS contract_id) z) x "
    "USING (contract_id)",
    "WITH a AS (SELECT 100005 AS c), b AS (SELECT c AS contract_id FROM a) "
    "SELECT COUNT(*) FROM deals d JOIN b ON d.contract_id = b.contract_id",
    "WITH k AS (SELECT concat(97592353, 6) AS seller_inn) SELECT COUNT(*) FROM deals "
    "WHERE seller_inn IN (SELECT seller_inn FROM k)",
    "WITH k AS (SELECT 100000 + 5 AS deal_id) SELECT COUNT(*) FROM deals d JOIN k ON d.deal_id = k.deal_id",
])
def test_rename_to_id_bypass_rejected(sql):
    assert not v(sql, question="Сколько сделок?").ok


def test_vat_payer_subquery_accepted():
    r = v("SELECT COUNT(*) AS n FROM deals WHERE buyer_inn IN (SELECT inn AS i FROM participants WHERE vat_payer)")
    assert r.ok, r.errors


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM deals WHERE contract_id IN "
    "(SELECT contract_id FROM (SELECT * REPLACE (100005 AS contract_id) FROM contracts) z)",
    "SELECT COUNT(*) FROM deals d JOIN (SELECT * REPLACE (100005 AS contract_id) FROM contracts LIMIT 1) x "
    "ON d.contract_id = x.contract_id",
    "SELECT COUNT(*) FROM deals d JOIN (SELECT struct_pack(contract_id := 100005) AS s) x "
    "ON d.contract_id = x.s.contract_id",
    "SELECT * RENAME (deal_id AS contract_id) FROM deals",
])
def test_star_replace_and_struct_smuggling_rejected(sql):
    assert not v(sql, question="Сколько сделок?").ok


def test_plain_star_with_question_id_accepted():
    r = v("SELECT * FROM contracts WHERE contract_id = 102267", question="контракт 102267")
    assert r.ok, r.errors


# --- final review: fabricated rows from literals ---
@pytest.mark.parametrize("sql", [
    "SELECT 'ООО Пахтакор' AS buyer_name, 15000.5 AS volume_t",
    "SELECT 15000.5 AS volume_t FROM deals LIMIT 1",
    "SELECT 'ООО Пахтакор' AS buyer_name, COUNT(*) FROM deals",
    "SELECT CASE WHEN is_cancelled THEN 'Отменён' ELSE 'valid' END AS s, COUNT(*) FROM deals GROUP BY 1",
    "SELECT (SELECT 15000.5) AS x, COUNT(*) FROM deals",
    "SELECT COUNT(*) FROM deals UNION ALL SELECT 42",
    "WITH t AS (SELECT 15000.5 AS x) SELECT x FROM t",
    "SELECT MAX('ООО Пахтакор') FROM deals",
])
def test_literal_fabrication_rejected(sql):
    assert not v(sql).ok


@pytest.mark.parametrize("sql", [
    "SELECT 'buyer' AS role, COUNT(*) FROM deals",
    "SELECT CASE WHEN is_cancelled THEN 'cancelled' ELSE 'valid' END AS s, COUNT(*) FROM deals GROUP BY 1",
    "SELECT 'by_volume_t' AS ranking, SUM(volume_tonnes) FROM deals UNION ALL "
    "SELECT 'by_total_uzs', SUM(amount_uzs) FROM deals",
    "SELECT COUNT(*) FILTER (WHERE currency = 'UZS') FROM deals",
])
def test_label_literals_accepted(sql):
    r = v(sql)
    assert r.ok, r.errors


# --- final review: re-aggregating per-currency sums ---
def test_reaggregated_currency_sums_rejected():
    sql = "SELECT SUM(s) FROM (SELECT currency, SUM(amount) AS s FROM deals GROUP BY currency) t"
    r = v(sql)
    assert not r.ok and any("currency" in e for e in r.errors)


def test_reaggregated_currency_sums_scoped_outer_ok():
    sql = ("SELECT currency, SUM(s) FROM (SELECT currency, SUM(amount) AS s FROM deals GROUP BY currency) t "
           "GROUP BY currency")
    r = v(sql)
    assert r.ok, r.errors


def test_literal_rule_error_has_month_hint():
    r = v("SELECT 'Jan-2025' AS m, COUNT(*) FROM deals")
    assert any("date_trunc('month', deal_date)" in e for e in r.errors)
