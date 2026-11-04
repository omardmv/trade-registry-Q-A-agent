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
_WRAPPERS = (exp.Lower, exp.Upper, exp.Trim, exp.Paren)
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
    aliases = _alias_bases(stmt)

    def bases(col: exp.Column) -> set[str]:
        return aliases.get(_n(col), set()) | {_n(col)}

    for root in roots:
        for col in root.find_all(exp.Column):
            hit = bases(col) & NAME_COLUMNS
            if hit:
                errors.append(f"do not filter by {sorted(hit)[0]} text; call resolve() and filter by inn / "
                              "broker_code / product_canonical / region")
    known = {a.casefold() for a in allowed}
    q = question.casefold()

    def unresolved(value: str) -> bool:
        return value.casefold() not in known and value.casefold() not in q

    for col in stmt.find_all(exp.Column):
        if not _in_filter(col):
            continue
        via_alias = sorted((bases(col) - {_n(col)}) & RESOLVED_COLUMNS)
        if via_alias:
            errors.append(f"filter on {via_alias[0]} directly with = / IN, not via an alias")
            continue
        if _n(col) not in RESOLVED_COLUMNS:
            continue
        ok, literals = _check_comparison(col)
        if not ok:
            errors.append(f"compare {_n(col)} only with = / IN to values from resolve()")
        elif check_ids:
            for value in literals:
                if unresolved(value):
                    errors.append(f"{_n(col)} = {value!r} was not returned by resolve() in this request")
    if check_ids:
        for lit in stmt.find_all(exp.Literal):
            if isinstance(lit.parent, (exp.Limit, exp.Offset, exp.Fetch)):
                continue
            if _ID_LITERAL.fullmatch(str(lit.this)) and unresolved(str(lit.this)):
                errors.append(f"ID {str(lit.this)!r} was not returned by resolve() in this request")

    money = set()
    for a in stmt.find_all(exp.AggFunc):
        for c in a.find_all(exp.Column):
            money |= bases(c) & MONEY_COLUMNS
    if money and not _has_currency_scope(stmt, aliases):
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


def _n(col: exp.Column) -> str:
    return col.name.lower()


def _alias_bases(stmt: exp.Expression) -> dict[str, set[str]]:
    """alias (lowercase) -> lowercase base column names it is computed from (transitively)."""
    direct: dict[str, set[str]] = {}
    for al in stmt.find_all(exp.Alias):
        expr = al.this
        if expr is None or expr.find(exp.AggFunc) is not None:
            continue
        cols = {c.name.lower() for c in expr.find_all(exp.Column)}
        if cols:
            direct.setdefault(al.alias.lower(), set()).update(cols)
    result = {k: set(v) for k, v in direct.items()}
    changed = True
    while changed:
        changed = False
        for k, vals in result.items():
            extra = set().union(*(direct.get(x, set()) for x in vals)) - vals
            if extra:
                vals |= extra
                changed = True
    return result


def _filter_roots(stmt: exp.Expression) -> list[exp.Expression]:
    roots: list[exp.Expression] = list(stmt.find_all(exp.Where, exp.Having, exp.Qualify))
    roots += [j.args["on"] for j in stmt.find_all(exp.Join) if j.args.get("on") is not None]
    roots += [i.this for i in stmt.find_all(exp.If) if i.this is not None]
    roots += [c.this for c in stmt.find_all(exp.Case) if c.this is not None]
    return roots


def _in_filter(col: exp.Column) -> bool:
    node: exp.Expression = col
    while node.parent is not None:
        p = node.parent
        if isinstance(p, (exp.Where, exp.Having, exp.Qualify)):
            return True
        if isinstance(p, exp.Join) and p.args.get("on") is node:
            return True
        if isinstance(p, (exp.If, exp.Case)) and p.this is node:
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
        items = [_unwrap(i) for i in (parent.args.get("expressions") or [])]
        if items and all(isinstance(i, exp.Literal) for i in items):
            return True, [str(i.this) for i in items]
    return False, []


def _conjuncts(e: exp.Expression):
    while isinstance(e, exp.Paren):
        e = e.this
    if isinstance(e, exp.And):
        yield from _conjuncts(e.this)
        yield from _conjuncts(e.args["expression"])
    else:
        yield e


def _has_currency_scope(stmt: exp.Expression, aliases: dict[str, set[str]]) -> bool:
    def is_currency(name: str) -> bool:
        return name == "currency" or "currency" in aliases.get(name, set())

    for g in stmt.find_all(exp.Group):
        select = g.parent if isinstance(g.parent, exp.Select) else None
        for e in g.expressions:
            if any(is_currency(c.name.lower()) for c in e.find_all(exp.Column)):
                return True
            if isinstance(e, exp.Literal) and not e.is_string and select is not None and e.this.isdigit():
                idx = int(e.this) - 1
                if 0 <= idx < len(select.expressions) and any(
                        is_currency(c.name.lower()) for c in select.expressions[idx].find_all(exp.Column)):
                    return True
        if g.args.get("all") and select is not None and any(
                isinstance(_strip_alias(x), exp.Column) and is_currency(_strip_alias(x).name.lower())
                for x in select.expressions):
            return True
    for where in stmt.find_all(exp.Where):
        for c in _conjuncts(where.this):
            if not isinstance(c, exp.EQ):
                continue
            sides = [_unwrap(x) for x in (c.this, c.args.get("expression"))]
            if any(isinstance(x, exp.Column) and x.name.lower() == "currency" for x in sides) and any(
                    isinstance(x, exp.Literal) and x.is_string and x.this.upper() in {"UZS", "USD"}
                    for x in sides):
                return True
    return False


def _strip_alias(e: exp.Expression) -> exp.Expression:
    return e.this if isinstance(e, exp.Alias) else e
