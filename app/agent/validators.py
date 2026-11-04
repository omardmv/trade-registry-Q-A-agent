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
BLOCKED_FUNCTIONS = {"glob", "getenv", "current_setting", "query", "query_table", "sniff_csv"}
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in
                        ("Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Copy")
                        if hasattr(exp, n))
_WRAPPERS = (exp.Lower, exp.Upper, exp.Trim)
_ID_LITERAL = re.compile(r"\d{9}|BK\d{3}")


@dataclass
class Validation:
    ok: bool
    sql: str
    errors: list[str] = field(default_factory=list)


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
    if stmt.find(*FORBIDDEN_NODES):
        errors.append("data-modifying or DDL statements are not allowed")

    ctes = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    for t in stmt.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            errors.append("table functions are not allowed")
            continue
        if t.args.get("db") or t.args.get("catalog"):
            errors.append("schema/catalog-qualified tables are not allowed")
            continue
        name = t.name.lower()
        if name not in KNOWN_TABLES | ctes:
            errors.append(f"unknown table {t.name!r}; tables: {', '.join(sorted(KNOWN_TABLES))}")
    if stmt.find(exp.Lateral):
        errors.append("LATERAL is not allowed")
    for f in stmt.find_all(exp.Func):
        fname = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if fname.startswith("read_") or fname.endswith("_scan") or fname in BLOCKED_FUNCTIONS:
            errors.append(f"function {fname} is not allowed")

    roots = _filter_roots(stmt)
    for root in roots:
        for col in root.find_all(exp.Column):
            if col.name in NAME_COLUMNS:
                errors.append(f"do not filter by {col.name} text; call resolve() and filter by inn / "
                              "broker_code / product_canonical / region")
    known = {a.casefold() for a in allowed}
    q = question.casefold()

    def unresolved(value: str) -> bool:
        return value.casefold() not in known and value.casefold() not in q

    for col in stmt.find_all(exp.Column):
        if col.name not in RESOLVED_COLUMNS or not _in_filter(col):
            continue
        ok, literals = _check_comparison(col)
        if not ok:
            errors.append(f"compare {col.name} only with = / IN to values from resolve()")
        elif check_ids:
            for value in literals:
                if unresolved(value):
                    errors.append(f"{col.name} = {value!r} was not returned by resolve() in this request")
    if check_ids:
        for lit in stmt.find_all(exp.Literal):
            if isinstance(lit.parent, (exp.Limit, exp.Offset, exp.Fetch)):
                continue
            if _ID_LITERAL.fullmatch(str(lit.this)) and unresolved(str(lit.this)):
                errors.append(f"ID {str(lit.this)!r} was not returned by resolve() in this request")

    all_aggs = [a for a in stmt.find_all(exp.AggFunc)
                if {c.name for c in a.find_all(exp.Column)} & MONEY_COLUMNS]
    if all_aggs and not _has_currency_scope(stmt, roots):
        money = {c.name for a in all_aggs for c in a.find_all(exp.Column)} & MONEY_COLUMNS
        errors.append(f"{', '.join(sorted(money))} is in the contract currency; use amount_uzs / amount_usd "
                      "or group/filter by currency")

    limit = stmt.args.get("limit")
    if not _limit_ok(limit, row_limit):
        stmt = stmt.limit(row_limit)
    sql = stmt.sql(dialect="duckdb")
    errors = list(dict.fromkeys(errors))
    if not errors and db is not None:
        try:
            db.explain(sql)
        except QueryError as e:
            errors.append(f"database error: {e}")
    return Validation(not errors, sql, errors)


def _limit_ok(limit: exp.Expression | None, row_limit: int) -> bool:
    if limit is None:
        return False
    e = limit.args.get("expression")
    return isinstance(e, exp.Literal) and not e.is_string and e.this.isdigit() and int(e.this) <= row_limit


def _filter_roots(stmt: exp.Expression) -> list[exp.Expression]:
    roots: list[exp.Expression] = list(stmt.find_all(exp.Where, exp.Having, exp.Qualify))
    roots += [j.args["on"] for j in stmt.find_all(exp.Join) if j.args.get("on") is not None]
    return roots


def _in_filter(col: exp.Column) -> bool:
    node: exp.Expression = col
    while node.parent is not None:
        p = node.parent
        if isinstance(p, (exp.Where, exp.Having, exp.Qualify)):
            return True
        if isinstance(p, exp.Join) and p.args.get("on") is node:
            return True
        if isinstance(p, exp.Select):
            return False
        node = p
    return False


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, _WRAPPERS) and isinstance(node.this, exp.Expression):
        node = node.this
    return node


def _check_comparison(col: exp.Column) -> tuple[bool, list[str]]:
    """(allowed, literals compared with col). Only = / <> / IN against literals or columns are allowed."""
    top: exp.Expression = col
    while isinstance(top.parent, _WRAPPERS):
        top = top.parent
    parent = top.parent
    if isinstance(parent, (exp.EQ, exp.NEQ)):
        other = parent.args["expression"] if parent.this is top else parent.this
        other = _unwrap(other)
        if isinstance(other, exp.Literal):
            return True, [str(other.this)]
        if isinstance(other, exp.Column):
            return True, []
        return False, []
    if isinstance(parent, exp.In) and parent.this is top:
        if parent.args.get("query") is not None:
            return True, []
        items = parent.args.get("expressions") or []
        if items and all(isinstance(i, exp.Literal) for i in items):
            return True, [str(i.this) for i in items]
    return False, []


def _has_currency_scope(stmt: exp.Expression, roots: list[exp.Expression]) -> bool:
    for g in stmt.find_all(exp.Group):
        if any(c.name == "currency" for c in g.find_all(exp.Column)):
            return True
    for root in roots:
        for eq in root.find_all(exp.EQ):
            sides = (eq.this, eq.args.get("expression"))
            cols = [x for x in sides if isinstance(x, exp.Column) and x.name == "currency"]
            lits = [x for x in sides if isinstance(x, exp.Literal) and x.is_string
                    and x.this.upper() in {"UZS", "USD"}]
            if cols and lits:
                return True
    return False
