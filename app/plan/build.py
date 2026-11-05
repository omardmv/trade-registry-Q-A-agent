"""One generic SQL builder for validated plans, driven by the field dictionary (app/fields.yaml).

SELECT <group cols>, <measures> FROM <table + joins> WHERE <filters> GROUP BY … ORDER BY … LIMIT …
Business rules kept from the structured form (app/compiler/sql.py): money via amount_uzs / amount_usd; annulled
deals excluded from rankings and shown separately in ungrouped counts and totals; volumes in units of measure
grouped and ranked within unit; a broker on either side counted once per deal; product_variant phrases expanded
to every matching registry spelling with resolve's matcher; deterministic tiebreak by the group keys.
"""
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from app.plan.dictionary import Dictionary, Field, Subject
from app.plan.model import Filter, Measure, Period, Plan, PlanError

VariantMatcher = Callable[[str], list[str]]
_SQL_AGG = {"sum": "SUM", "avg": "AVG", "median": "MEDIAN", "min": "MIN", "max": "MAX"}
UZS_FIELDS = ("amount_uzs", "deal_price_per_kg", "unit_price_uzs")
TIES_CAP = 50                           # a top-1 keeps every tied row, up to this many


@dataclass
class Built:
    sql: str
    splits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    measure_columns: list[str] = field(default_factory=list)


def lit(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def _typed(f: Field, v):
    """deal / contract ids are integers in the database."""
    if f.ground in ("deal", "contract") and isinstance(v, str) and v.isdigit():
        return int(v)
    return v


def _cond(col: str, op: str, value, f: Field) -> str:
    if op == "=":
        return f"{col} = {lit(_typed(f, value))}"
    if op == "in":
        return f"{col} IN ({', '.join(lit(_typed(f, v)) for v in value)})"
    if op == "!=":
        return f"{col} IS DISTINCT FROM {lit(_typed(f, value))}"
    if op == "between":
        return f"{col} BETWEEN {lit(value[0])} AND {lit(value[1])}"
    if op == ">=":
        return f"{col} >= {lit(value)}"
    if op == "<=":
        return f"{col} <= {lit(value)}"
    raise PlanError(f"unsupported op {op}")


class _Ctx:
    def __init__(self, s: Subject, variants: VariantMatcher | None):
        self.s, self.variants = s, variants
        self.spellings: dict[str, int] = {}

    def filter_sql(self, flt: Filter) -> str:
        f = self.s.fields[flt.field]
        if f.filter_sql:
            return "(" + f.filter_sql.replace("{v}", lit(flt.value)) + ")"
        if f.either:
            return "(" + " OR ".join(_cond(col, flt.op, flt.value, f) for col in f.either.values()) + ")"
        if f.ground == "product_variant":
            if self.variants is None:
                raise PlanError("product_variant needs the product spelling matcher")
            names = list(dict.fromkeys(self.variants(str(flt.value))))
            if not names:
                raise PlanError(f"no product spelling matches {flt.value!r}; use product, or call "
                                "declare_unanswerable")
            self.spellings[str(flt.value)] = len(names)
            return _cond(f.sql, "in", tuple(names), f)
        return _cond(f.sql, flt.op, flt.value, f)

    def where(self, filters) -> list[str]:
        return [self.filter_sql(f) for f in filters]


def _fc(conds: list[str]) -> str:
    return f" FILTER (WHERE {' AND '.join(conds)})" if conds else ""


def _scope_free(s: Subject, m: Measure) -> bool:
    return m.agg == "share" or (m.agg in s.measures and s.measures[m.agg].scope_free)


def _per_unit(s: Subject, m: Measure) -> bool:
    if m.agg in s.measures:
        return s.measures[m.agg].per_unit
    return bool(m.of) and s.fields[m.of].per_unit and m.agg != "distinct"


def measure_alias(s: Subject, m: Measure) -> str:
    if m.agg == "count":
        return s.count_col
    if m.agg in s.measures:
        return m.agg
    if m.agg == "share":
        return "share_pct" if m.of is None else f"share_{m.of}_pct"
    return f"{m.agg}_{m.of}"


def _expr(ctx: _Ctx, plan: Plan, m: Measure, conds: list[str]) -> str:
    s = ctx.s
    if m.agg == "count":
        return f"COUNT(*){_fc(conds)}"
    if m.agg in s.measures:
        nm = s.measures[m.agg]
        own = conds if not nm.scope_free else []
        return nm.sql.format(f=_fc(own), w=_fc(own + ([nm.where] if nm.where else [])))
    if m.agg == "share":
        num = " AND ".join(ctx.where(m.where)) or "FALSE"
        if m.of is None:
            return f"ROUND(100.0 * COUNT(*) FILTER (WHERE {num}) / NULLIF(COUNT(*), 0), 2)"
        col = s.fields[m.of].sql
        touches_status = any(w.field in s.status_fields for w in m.where)
        status_rows = any(f.field in s.status_fields for f in plan.filters)
        base = [] if (not s.cancelled or touches_status or status_rows) else [f"NOT {s.cancelled}"]
        return (f"ROUND(100.0 * SUM({col}){_fc([num] + base)} / NULLIF(SUM({col}){_fc(base)}, 0), 2)")
    f = s.fields[m.of]
    if m.agg == "distinct":
        if f.either:
            cols = ", ".join(f.either.values())
            return f"len(list_distinct(flatten(LIST([{cols}]){_fc(conds)})))"
        return f"COUNT(DISTINCT {f.sql}){_fc(conds)}"
    return f"{_SQL_AGG[m.agg]}({f.sql}){_fc(conds)}"


@dataclass
class _Group:
    select: list[str]
    group: list[str]
    order_cols: list[str]       # output aliases, for tiebreak
    time_cols: list[str]        # output aliases of time grains (sorted ascending)
    joins: list[str]
    partition: list[str]        # expressions to partition a ranking by


def _alias_of(sel: str) -> str:
    return sel.rsplit(" AS ", 1)[1].strip()


def _expr_of(sel: str) -> str:
    return sel.rsplit(" AS ", 1)[0].strip()


def _group(s: Subject, plan: Plan, per_unit: bool) -> tuple[_Group, Field | None]:
    g = _Group([], [], [], [], [], [])
    either = None
    for i, name in enumerate(plan.group_by):
        f = s.fields[name]
        sels: list[str] = []
        if f.side_of:                   # seller / buyer side of one filtered broker: one row per side
            either = replace(s.fields[f.side_of], mode="per_side")
            sels.append(f"{s.alias}._side AS {f.col}")
        elif f.type == "record":
            sels = list(f.columns)
            g.joins += list(f.joins)
        elif f.either:
            either = f
            if f.mode == "per_side":
                sels.append("d._side AS role")
            sels.append(f"d._either AS {f.col}")
        else:
            if f.type == "grain" and f.with_year and "year" not in plan.group_by:
                y = s.fields["year"]
                sels.append(f"{y.sql} AS {y.col}")
                g.time_cols.append(y.col)
            sels.append(f"{f.sql} AS {f.col}")
            if f.type == "grain":
                g.time_cols.append(f.col)
        if f.display is not None:
            sels.append(f"{f.display.sql} AS {f.display.col}")
            g.joins.append(f.display.join)
        new = [x for x in sels if x not in g.select]
        g.select += new
        if i == 0 and len(plan.group_by) == 2:
            g.partition += [_expr_of(x) for x in new]
    if either is not None and either.mode == "per_side":
        g.partition.insert(0, f"{s.alias}._side")
    if per_unit and s.unit_field and s.unit_field not in plan.group_by and not any(
            x.endswith(f" AS {s.fields[s.unit_field].col}") for x in g.select):
        uf = s.fields[s.unit_field]
        g.select.append(f"{uf.sql} AS {uf.col}")
        g.partition.append(uf.sql)
    g.group = [_expr_of(x) for x in g.select]
    g.order_cols = [_alias_of(x) for x in g.select]
    return g, either


def _from(s: Subject, ctx: _Ctx, plan: Plan, where: list[str], either: Field | None, joins: list[str]) -> str:
    a = s.alias
    join_sql = " ".join(s.joins[j] for j in dict.fromkeys(joins))
    if either is None:
        src = f"FROM {s.table} {a}"
        parts = [src, join_sql, ("WHERE " + " AND ".join(where)) if where else ""]
        return " ".join(p for p in parts if p)
    inner_where = (" WHERE " + " AND ".join(where)) if where else ""
    branches = []
    for side, col in either.either.items():
        side_col = f", '{side}' AS _side" if either.mode == "per_side" else ""
        branches.append(f"SELECT {a}.*, {col} AS _either{side_col} FROM {s.table} {a}{inner_where}")
    # UNION (not ALL) for mode once: the row carries the deal id, so a deal with the same entity on both sides
    # collapses to one row
    glue = " UNION ALL " if either.mode == "per_side" else " UNION "
    outer = [f"FROM ({glue.join(branches)}) {a}", join_sql]
    restrict = [f for f in plan.filters if f.field == either.name]
    if restrict:
        outer.append("WHERE " + " AND ".join(_cond(f"{a}._either", f.op, f.value, either) for f in restrict))
    return " ".join(p for p in outer if p)


def _status(s: Subject, plan: Plan) -> tuple[list[str], bool]:
    """(WHERE conditions for the status view, valid_via_filter). valid_via_filter: the valid view is applied by
    FILTER clauses because a scope-free measure needs the annulled rows."""
    if not s.cancelled or plan.scope != "valid":
        return [], False
    if any(_scope_free(s, m) for m in plan.measures):
        return [], True
    return [f"NOT {s.cancelled}"], False


def _pairs(ctx: _Ctx, plan: Plan, valid_filter: bool) -> list[tuple[str, str, Measure]]:
    s = ctx.s
    out: list[tuple[str, str, Measure]] = []
    seen: dict[str, int] = {}
    valid = [f"NOT {s.cancelled}"] if s.cancelled else []
    for m in plan.measures:
        alias = measure_alias(s, m)
        seen[alias] = seen.get(alias, 0) + 1
        if seen[alias] > 1:
            alias = f"{alias}_{seen[alias]}"
        if plan.scope == "split" and not _scope_free(s, m):
            out.append((_expr(ctx, plan, m, []), f"{alias}_all", m))
            out.append((_expr(ctx, plan, m, valid), f"{alias}_valid", m))
        elif valid_filter and not _scope_free(s, m):
            out.append((_expr(ctx, plan, m, valid), alias, m))
        else:
            out.append((_expr(ctx, plan, m, []), alias, m))
    return out


def _period_conds(s: Subject, p: Period) -> list[str]:
    if p.day is not None:
        return [f"{s.fields[s.date_field].sql} = DATE '{p.date().isoformat()}'"]
    out = [f"{s.fields['year'].sql} = {int(p.year)}"]
    months = p.months()
    if months is not None:
        out.append(f"{s.fields['month'].sql} BETWEEN {int(months[0])} AND {int(months[1])}")
    return out


def _compare_sql(ctx: _Ctx, plan: Plan, status_where: list[str], pairs, unit_sel: list[str]) -> str:
    s = ctx.s
    branches = []
    for i, p in enumerate(plan.compare, 1):
        where = ctx.where(plan.filters) + _period_conds(s, p) + status_where
        months = p.months()
        mf = "CAST(NULL AS INTEGER)" if months is None else str(int(months[0]))
        mt = "CAST(NULL AS INTEGER)" if months is None else str(int(months[1]))
        day = "CAST(NULL AS INTEGER)" if p.day is None else str(int(p.day))
        cols = [f"{i} AS period_no", f"{int(p.year)} AS year", f"{mf} AS month_from", f"{mt} AS month_to",
                f"{day} AS day"]
        cols += unit_sel + [f"{e} AS {a}" for e, a, _ in pairs]
        branch = f"SELECT {', '.join(cols)} FROM {s.table} {s.alias} WHERE {' AND '.join(where)}"
        if unit_sel:
            branch += " GROUP BY " + ", ".join(_expr_of(u) for u in unit_sel)
        branches.append(f"({branch})")
    unit = [_alias_of(u) for u in unit_sel]
    window = (f"PARTITION BY {', '.join(unit)} " if unit else "") + "ORDER BY period_no"
    aliases = [a for _, a, _ in pairs]
    change = []
    for (_, a, m) in pairs:
        if m.agg in ("share", "cancelled_share") or (m.of or "").endswith("_pct"):
            name = "change_pp" if len(aliases) == 1 else f"{a}_change_pp"
            change.append(f"ROUND({a} - LAG({a}) OVER ({window}), 2) AS {name}")
        else:
            name = "change_pct" if len(aliases) == 1 else f"{a}_change_pct"
            change.append(f"ROUND(100.0 * ({a} / NULLIF(LAG({a}) OVER ({window}), 0) - 1), 2) AS {name}")
    head = ["year", "month_from", "month_to"] + (["day"] if any(p.day is not None for p in plan.compare) else []) \
        + unit + aliases + change
    order = ", ".join(["period_no"] + unit)
    return f"SELECT {', '.join(head)} FROM ({' UNION ALL '.join(branches)}) p ORDER BY {order}"


def _ranked(select: list[str], from_sql: str, g: _Group, rank_expr: str, rank_alias: str, plan: Plan,
            having: str) -> str:
    direction = "ASC" if plan.order == "asc" else "DESC"
    base = f"SELECT {', '.join(select)} {from_sql} GROUP BY {', '.join(g.group)}{having}"
    tie_cols = ", ".join(g.order_cols)
    if g.partition:
        tie = ", ".join(g.group)
        part = ", ".join(g.partition)
        part_alias = ", ".join(_alias_of(x) for x in g.select if _expr_of(x) in g.partition) or part
        side = next((_alias_of(x) for x in g.select if _expr_of(x).endswith("._side")), None)
        if side and side not in part_alias:
            part_alias = f"{side}, " + part_alias
        if plan.limit == 1:             # ties at the top are all kept (every date with the extreme rate, ...)
            return (f"({base} QUALIFY RANK() OVER (PARTITION BY {part} ORDER BY {rank_expr} {direction} NULLS LAST) "
                    f"= 1 ORDER BY {part_alias}, {tie_cols} LIMIT {TIES_CAP})")
        return (f"({base} QUALIFY ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY {rank_expr} {direction} NULLS LAST, "
                f"{tie}) <= {int(plan.limit)} ORDER BY {part_alias}, {rank_alias} {direction} NULLS LAST, {tie_cols})")
    if plan.limit == 1:
        return (f"({base} QUALIFY RANK() OVER (ORDER BY {rank_expr} {direction} NULLS LAST) = 1 "
                f"ORDER BY {tie_cols} LIMIT {TIES_CAP})")
    return f"({base} ORDER BY {rank_alias} {direction} NULLS LAST, {tie_cols} LIMIT {int(plan.limit)})"


def build_sql(plan: Plan, d: Dictionary, variants: VariantMatcher | None = None) -> Built:
    s = d.subject(plan.subject)
    ctx = _Ctx(s, variants)
    notes: list[str] = []
    splits: list[str] = []
    status_where, valid_filter = _status(s, plan)
    if s.cancelled and plan.measures or (s.cancelled and plan.group_by):
        notes.append({"valid": "excluded_cancelled", "all": "all_statuses", "split": "status_split"}[plan.scope])
    if plan.scope == "split":
        splits.append("status_scope")
    per_unit = any(_per_unit(s, m) for m in plan.measures)
    if per_unit and s.unit_field and any(f.field == s.unit_field and f.op == "=" for f in plan.filters):
        per_unit = False                    # one unit: nothing to split
    if per_unit:
        notes.append("volume_by_unit")
    fields_used = {m.of for m in plan.measures if m.of} | {m.agg for m in plan.measures}
    if fields_used & set(UZS_FIELDS) or "price_per_kg" in fields_used:
        notes.append("uzs_converted")
    if "amount_usd" in fields_used:
        notes.append("usd_converted")
    if {"amount_uzs", "amount_usd"} <= {m.of for m in plan.measures}:
        splits.append("currency")
    if "price_per_kg" in fields_used and any(m.of == "deal_price_per_kg" for m in plan.measures):
        splits.append("price_average")
    if plan.order == "asc":
        notes.append("order_asc")
    if plan.default_limit:
        notes.append(f"default_limit_{plan.limit}")

    where = ctx.where(plan.filters) + status_where
    pairs = _pairs(ctx, plan, valid_filter)
    measure_cols = [f"{e} AS {a}" for e, a, _ in pairs]
    measure_aliases = [a for _, a, _ in pairs]

    if plan.compare:
        notes.append("compare_periods")
        unit_sel = []
        if per_unit:
            uf = s.fields[s.unit_field]
            unit_sel = [f"{uf.sql} AS {uf.col}"]
        sql = _compare_sql(ctx, plan, status_where, pairs, unit_sel)
        notes += [f"variant_spellings_{n}" for n in ctx.spellings.values()]
        return Built(sql, splits, notes, measure_aliases)

    g, either = _group(s, plan, per_unit)
    if either is not None:
        side_split = any(s.fields[n].side_of for n in plan.group_by)
        notes.append("broker_side_split" if side_split else "role_split" if either.mode == "per_side"
                     else "broker_both_sides")
        if either.mode == "per_side" and not side_split:
            splits.append("role")
    from_sql = _from(s, ctx, plan, where, either, g.joins)
    notes += [f"variant_spellings_{n}" for n in ctx.spellings.values()]

    if not g.select:
        return Built(f"SELECT {', '.join(measure_cols)} {from_sql}", splits, notes, measure_aliases)
    if not pairs:                                   # record listing
        order = ", ".join(g.order_cols)
        limit = f" LIMIT {int(plan.limit)}" if plan.limit else ""
        return Built(f"SELECT {', '.join(g.select)} {from_sql} ORDER BY {order}{limit}", splits, notes, [])
    having = ""
    if valid_filter and s.cancelled:
        having = f" HAVING COUNT(*) FILTER (WHERE NOT {s.cancelled}) > 0"
    select = g.select + measure_cols
    # the split view (all deals and, separately, without annulled) orders and ranks by the without-annulled value
    ranked_measures = [p for p in pairs if not (plan.scope == "split" and p[1].endswith("_all")
                                                and any(q[2] is p[2] and q[1].endswith("_valid") for q in pairs))]
    if plan.limit is None:
        direction = "ASC" if plan.order == "asc" else "DESC"
        first = ranked_measures[0][1]
        order = g.time_cols + [f"{first} {direction} NULLS LAST"] + [x for x in g.order_cols if x not in g.time_cols]
        sql = f"SELECT {', '.join(select)} {from_sql} GROUP BY {', '.join(g.group)}{having} ORDER BY {', '.join(order)}"
        return Built(sql, splits, notes, measure_aliases)
    notes.append(f"ranked_top_{plan.limit}")
    if plan.limit == 1:
        notes.append("ties_kept")
    if plan.scope == "split":
        notes.append("ranked_valid")
    if len(plan.measures) == 2:
        splits.append("measure")
        parts = []
        for m in plan.measures:
            e, a, _ = next(p for p in ranked_measures if p[2] is m)
            parts.append(_ranked([f"'by_{a}' AS ranking"] + select, from_sql, g, e, a, plan, having))
        return Built(" UNION ALL ".join(parts), splits, notes, measure_aliases)
    e, a, _ = ranked_measures[0]
    return Built(_ranked(select, from_sql, g, e, a, plan, having), splits, notes, measure_aliases)
