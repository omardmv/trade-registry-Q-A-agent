import datetime as dt
from decimal import Decimal

import pytest

from app.db import QueryError, to_jsonable


def test_query_returns_columns_and_rows(db):
    res = db.query("SELECT COUNT(*) AS n FROM brokers")
    assert res.columns == ["n"]
    assert res.rows == [(120,)]
    assert res.truncated is False


def test_query_truncates(db):
    res = db.query("SELECT broker_code FROM brokers ORDER BY 1", max_rows=5)
    assert len(res.rows) == 5
    assert res.truncated is True


def test_writes_are_rejected(db):
    with pytest.raises(QueryError):
        db.query("DELETE FROM brokers")


def test_external_access_is_disabled(db):
    with pytest.raises(QueryError):
        db.query("SELECT * FROM read_csv('data/brokers.csv')")


def test_timeout_interrupts(db):
    with pytest.raises(QueryError, match="timed out"):
        db.query("SELECT COUNT(*) FROM deals a, deals b, deals c", timeout_s=0.5)


def test_explain_reports_bad_column(db):
    with pytest.raises(QueryError):
        db.explain("SELECT no_such_column FROM deals")


def test_to_jsonable():
    assert to_jsonable(dt.date(2025, 1, 2)) == "2025-01-02"
    assert to_jsonable(Decimal("1.5")) == 1.5
    assert to_jsonable(dt.time(10, 4, 49)) == "10:04:49"
    assert to_jsonable("x") == "x"
