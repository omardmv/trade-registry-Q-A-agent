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
                    "seller_broker", "buyer_broker", "broker_code", "contract_id", "deal_id"}
BLOCKED_FUNCTIONS = {"glob", "getenv", "current_setting", "query", "query_table", "sniff_csv"}
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in
                        ("Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Copy")
                        if hasattr(exp, n))
_WHOLE_ROW_ERR = "whole-row and COLUMNS() expressions are not allowed"
_WRAPPERS = (exp.Lower, exp.Upper, exp.Trim, exp.Paren)
_ID_ONLY_COLUMNS = {"contract_id", "deal_id"}
_CASE_FOLD = (exp.Lower, exp.Upper)
_ID_LITERAL = re.compile(r"\d{9}|BK\d{3}")


_NAME_KINDS = {"product", "product_family", "region"}
_ID_SHAPE = {"inn": r"\d{9}", "broker": r"BK\d{3}", "contract": r"\d{6}", "deal": r"\d{6}"}


def is_grounded(value: str, kind: str, allowed: set[tuple[str, str]], question: str) -> bool:
    """allowed holds (kind, value) pairs from resolve(). Names need the exact pair; ids may also appear as
    whole tokens of the right shape in the question."""
    value = str(value)
    if value.strip() == "":
        return False
    if (kind, value) in allowed:
        return True
    if kind in _NAME_KINDS:
        return False
    shape = _ID_SHAPE[kind]
    if not re.fullmatch(shape, value, re.I):
        return False
    return re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", question, re.I) is not None


_COLUMN_KIND = {"product_canonical": "product", "product_family": "product_family", "region": "region",
                "seller_inn": "inn", "buyer_inn": "inn", "inn": "inn",
                "seller_broker": "broker", "buyer_broker": "broker", "broker_code": "broker",
                "contract_id": "contract", "deal_id": "deal"}


@dataclass
class Validation:
    ok: bool
    sql: str
    errors: list[str] = field(default_factory=list)


def validate_sql(sql: str, allowed: set[tuple[str, str]], question: str, db: Database | None = None,
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
    if any(ta.args.get("columns") for ta in stmt.find_all(exp.TableAlias)):
        errors.append("column-list aliases are not allowed; alias columns inside the SELECT instead")
    if stmt.find(exp.Lateral):
        errors.append("LATERAL is not allowed")
    for f in stmt.find_all(exp.Func):
        fname = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if fname.startswith("read_") or fname.endswith("_scan") or fname in BLOCKED_FUNCTIONS:
            errors.append(f"function {fname} is not allowed")

    aliases = _alias_bases(stmt)

    def bases(col: exp.Column) -> set[str]:
        return aliases.get(_n(col), set()) | {_n(col)}

    if stmt.find(exp.Columns) or any(
            isinstance(f, exp.Anonymous) and f.name.lower() == "columns" for f in stmt.find_all(exp.Func)):
        errors.append(_WHOLE_ROW_ERR)
    table_names = KNOWN_TABLES | ctes | {t.alias_or_name.lower() for t in stmt.find_all(exp.Table)}

    def unresolved(value: str, kind: str) -> bool:
        return not is_grounded(value, kind, allowed, question)

    for col in stmt.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        if not col.table and _n(col) in table_names:
            errors.append(_WHOLE_ROW_ERR)
            continue
        bare = _is_bare_item(col)
        hit = bases(col) & NAME_COLUMNS
        if hit and not bare and not _is_join_equality(col):
            errors.append(f"do not filter by {sorted(hit)[0]} text; call resolve() and filter by inn / "
                          "broker_code / product_canonical / region")
            continue
        if bare or _is_count_arg(col):
            continue
        via_alias = sorted((bases(col) - {_n(col)}) & RESOLVED_COLUMNS)
        if via_alias:
            errors.append(f"filter on {via_alias[0]} directly with = / IN, not via an alias")
            continue
        if _n(col) not in RESOLVED_COLUMNS:
            continue
        if _case_folded_comparison(col):
            errors.append(f"compare {_n(col)} directly with the resolved value, without lower()/upper()")
            continue
        if _n(col) in _ID_ONLY_COLUMNS and _id_column_free_use(col):
            continue
        ok, literals = _check_comparison(col)
        if not ok:
            errors.append(f"compare {_n(col)} only with = / IN to values from resolve()")
        elif check_ids:
            for value in literals:
                if unresolved(value, _COLUMN_KIND[_n(col)]):
                    errors.append(f"{_n(col)} = {value!r} was not returned by resolve() in this request")
    if check_ids:
        for lit in stmt.find_all(exp.Literal):
            if isinstance(lit.parent, (exp.Limit, exp.Offset, exp.Fetch)):
                continue
            if _ID_LITERAL.fullmatch(str(lit.this)) and unresolved(
                    str(lit.this), "broker" if str(lit.this).startswith("BK") else "inn"):
                errors.append(f"ID {str(lit.this)!r} was not returned by resolve() in this request")

    money: set[str] = set()
    for agg in stmt.find_all(exp.AggFunc):
        hit = set().union(*(bases(c) for c in agg.find_all(exp.Column))) & MONEY_COLUMNS if agg.find(exp.Column) \
            else set()
        if hit and not _agg_has_currency_scope(agg, aliases):
            money |= hit
    if money:
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


def _is_bare_item(col: exp.Column) -> bool:
    """Bare column used as a SELECT item (optionally aliased), GROUP BY item or ORDER BY item."""
    node: exp.Expression = col
    if isinstance(node.parent, exp.Alias) and node.parent.this is node:
        node = node.parent
    p = node.parent
    if isinstance(p, exp.Select):
        return node.arg_key == "expressions"
    if isinstance(p, exp.Group):
        return node.arg_key == "expressions"
    return isinstance(p, exp.Ordered) and isinstance(p.parent, exp.Order)


def _is_count_arg(col: exp.Column) -> bool:
    p = col.parent
    if isinstance(p, exp.Distinct) and isinstance(p.parent, exp.Count):
        return True
    return isinstance(p, exp.Count)


def _id_column_free_use(col: exp.Column) -> bool:
    """contract_id / deal_id used as IS [NOT] NULL operand or as a MIN/MAX/COUNT argument."""
    p = col.parent
    if isinstance(p, exp.Not):
        p = p.parent
    if isinstance(p, exp.Is) and isinstance(p.args.get("expression"), exp.Null):
        return True
    return isinstance(col.parent, (exp.Min, exp.Max, exp.Count))


def _is_join_equality(col: exp.Column) -> bool:
    p = col.parent
    if not (isinstance(p, exp.EQ) and isinstance(p.this, exp.Column)
            and isinstance(p.args.get("expression"), exp.Column)):
        return False
    node: exp.Expression = p
    while isinstance(node.parent, (exp.And, exp.Paren)):
        node = node.parent
    return isinstance(node.parent, exp.Join) and node.parent.args.get("on") is node


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, _WRAPPERS) and isinstance(node.this, exp.Expression):
        node = node.this
    return node


def _folds_case(node: exp.Expression) -> bool:
    """True if a wrapper chain starting at node passes through lower()/upper()."""
    while isinstance(node, _WRAPPERS) and isinstance(node.this, exp.Expression):
        if isinstance(node, _CASE_FOLD):
            return True
        node = node.this
    return False


def _case_folded_comparison(col: exp.Column) -> bool:
    top: exp.Expression = col
    while isinstance(top.parent, _WRAPPERS):
        if isinstance(top.parent, _CASE_FOLD):
            return True
        top = top.parent
    parent = top.parent
    if isinstance(parent, (exp.EQ, exp.NEQ)):
        other = parent.args["expression"] if parent.this is top else parent.this
        return _folds_case(other)
    if isinstance(parent, exp.In) and parent.this is top:
        return any(_folds_case(i) for i in parent.args.get("expressions") or [])
    return False


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


def _is_currency_filter(e: exp.Expression | None) -> bool:
    """Top-level AND conjunct `currency = 'UZS'|'USD'` in a WHERE expression."""
    if e is None:
        return False
    for c in _conjuncts(e):
        if not isinstance(c, exp.EQ):
            continue
        sides = [_unwrap(x) for x in (c.this, c.args.get("expression"))]
        if any(isinstance(x, exp.Column) and x.name.lower() == "currency" for x in sides) and any(
                isinstance(x, exp.Literal) and x.is_string and x.this.upper() in {"UZS", "USD"} for x in sides):
            return True
    return False


def _agg_has_currency_scope(agg: exp.Expression, aliases: dict[str, set[str]]) -> bool:
    if isinstance(agg.parent, exp.Filter) and _is_currency_filter(agg.parent.expression.this):
        return True
    select = agg.find_ancestor(exp.Select)
    if select is None:
        return False

    def is_currency(c: exp.Column) -> bool:
        n = c.name.lower()
        return n == "currency" or "currency" in aliases.get(n, set())

    where = select.args.get("where")
    if where is not None and _is_currency_filter(where.this):
        return True
    group = select.args.get("group")
    if group is None:
        return False
    for e in group.expressions:
        if any(is_currency(c) for c in e.find_all(exp.Column)):
            return True
        if isinstance(e, exp.Literal) and not e.is_string and e.this.isdigit():
            idx = int(e.this) - 1
            if 0 <= idx < len(select.expressions) and any(
                    is_currency(c) for c in select.expressions[idx].find_all(exp.Column)):
                return True
    if group.args.get("all"):
        return any(isinstance(_strip_alias(x), exp.Column) and is_currency(_strip_alias(x))
                   for x in select.expressions)
    return False


def _strip_alias(e: exp.Expression) -> exp.Expression:
    return e.this if isinstance(e, exp.Alias) else e
