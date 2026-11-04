"""Validation for free-form SQL (fallback path when no spec fits)."""
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.db import Database, QueryError

KNOWN_TABLES = {"deals", "contracts", "participants", "brokers", "usd_rates", "products", "regions"}
NAME_COLUMNS = {"name", "name_norm", "product_name", "product_name_full", "warehouse_address"}
MONEY_COLUMNS = {"amount", "start_price", "unit_price", "base_price"}
RESOLVED_COLUMNS = {"product_canonical", "product_family", "region", "seller_inn", "buyer_inn", "inn",
                    "seller_broker", "buyer_broker", "broker_code"}
BLOCKED_FUNCTIONS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
                     "read_blob", "glob", "getenv", "current_setting", "query", "query_table"}
_ID_LITERAL = re.compile(r"\d{9}|BK\d{3}")


@dataclass
class Validation:
    ok: bool
    sql: str
    errors: list[str] = field(default_factory=list)


def _string_literals(node: exp.Expression) -> list[str]:
    return [n.this for n in node.find_all(exp.Literal) if n.is_string]


def validate_sql(sql: str, allowed: set[str], question: str, db: Database | None = None,
                 check_ids: bool = True, row_limit: int = 1000) -> Validation:
    sql = sql.strip().rstrip(";").strip()
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None and not isinstance(s, exp.Semicolon)]
    except sqlglot.errors.ParseError as e:
        return Validation(False, sql, [f"SQL parse error: {e}"])
    if len(statements) != 1:
        return Validation(False, sql, ["exactly one SQL statement is required"])
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        return Validation(False, sql, ["only SELECT / WITH queries are allowed"])

    errors: list[str] = []
    ctes = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    for t in stmt.find_all(exp.Table):
        name = t.name.lower()
        if not name:
            errors.append("table functions are not allowed")
        elif name not in KNOWN_TABLES | ctes:
            errors.append(f"unknown table {t.name!r}; tables: {', '.join(sorted(KNOWN_TABLES))}")
    for f in stmt.find_all(exp.Func):
        fname = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if fname in BLOCKED_FUNCTIONS:
            errors.append(f"function {fname} is not allowed")

    all_columns = {c.name for c in stmt.find_all(exp.Column)}
    for agg in stmt.find_all(exp.AggFunc):
        money = {c.name for c in agg.find_all(exp.Column)} & MONEY_COLUMNS
        if money and "currency" not in all_columns:
            errors.append(f"{', '.join(sorted(money))} is in the contract currency; use amount_uzs / amount_usd "
                          "or group/filter by currency")
            break

    for node in stmt.find_all(exp.EQ, exp.NEQ, exp.Like, exp.ILike, exp.In):
        cols = [c for side in (node.this, node.args.get("expression")) if isinstance(side, exp.Expression)
                for c in side.find_all(exp.Column)]
        literals = _string_literals(node)
        for col in cols:
            if col.name in NAME_COLUMNS and literals:
                errors.append(f"do not filter by {col.name} text; call resolve() and filter by inn / "
                              "broker_code / product_canonical / region")
            if check_ids and col.name in RESOLVED_COLUMNS:
                for value in literals:
                    if value not in allowed and value not in question:
                        errors.append(f"{col.name} = {value!r} was not returned by resolve() in this request")
    if check_ids:
        for value in _string_literals(stmt):
            if _ID_LITERAL.fullmatch(value) and value not in allowed and value not in question:
                errors.append(f"ID {value!r} was not returned by resolve() in this request")

    if stmt.args.get("limit") is None:
        stmt = stmt.limit(row_limit)
    sql = stmt.sql(dialect="duckdb")
    errors = list(dict.fromkeys(errors))
    if not errors and db is not None:
        try:
            db.explain(sql)
        except QueryError as e:
            errors.append(f"database error: {e}")
    return Validation(not errors, sql, errors)
