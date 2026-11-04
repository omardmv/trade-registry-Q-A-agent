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
