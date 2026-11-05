"""Generic validation of a plan against the field dictionary (app/fields.yaml).

Every error is a short fix instruction for the planner and lists the allowed alternatives.
"""
import datetime as dt
import re

from app.plan.dictionary import Dictionary, Field, Subject
from app.plan.model import Filter, Measure, Period, Plan, PlanError

OPS_BY_TYPE = {
    "dimension": ("=", "in", "!="),
    "record": ("=", "in"),
    "number": ("=", "in", "!=", "between", ">=", "<="),
    "date": ("=", "between", ">=", "<="),
    "bool": ("=", "!=", "in"),
    "grain": ("=", "in", "!=", "between", ">=", "<="),
}
GROUPABLE = ("dimension", "grain", "record", "bool")
# grounding kind of the writer's `allowed` pairs (see app/agent/writer.py) per dictionary `ground`
GROUND_KIND = {"company": "inn", "broker": "broker", "product": "product", "product_family": "product_family",
               "product_variant": "product_variant_query", "region": "region", "deal": "deal",
               "contract": "contract"}
_TRUE, _FALSE = ("true", "1", "yes", "да", "ha"), ("false", "0", "no", "нет", "yo‘q", "yoq")


def _fmt(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def _list(names, cap: int = 60) -> str:
    names = list(names)
    more = f" … (+{len(names) - cap})" if len(names) > cap else ""
    return ", ".join(names[:cap]) + more


def field_names(s: Subject) -> list[str]:
    return list(s.fields)


def _field(s: Subject, name: str, where: str) -> Field:
    f = s.fields.get(name)
    if f is None:
        hint = ""
        if name in s.measures or name == "share":
            hint = f"{name} is a measure (agg), not a field; "
        raise PlanError(f"unknown field {name!r} in {where} for subject {s.name}; {hint}"
                        f"allowed fields: {_list(field_names(s))}")
    return f


def _bool(v, name: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in _TRUE + _FALSE:
        return v.strip().lower() in _TRUE
    raise PlanError(f"{name} is true/false, got {v!r}")


def _num(v, name: str) -> float | int:
    if isinstance(v, bool):
        raise PlanError(f"{name} needs a number, got {v!r}")
    if isinstance(v, (int, float)):
        return v
    try:
        x = float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        raise PlanError(f"{name} needs a number, got {v!r}")
    return int(x) if x.is_integer() else x


def _date(v, name: str) -> dt.date:
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        s = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise PlanError(f"{name} needs a date YYYY-MM-DD, got {v!r}")


def _int(v, name: str) -> int:
    x = _num(v, name)
    if not isinstance(x, int):
        raise PlanError(f"{name} needs an integer, got {v!r}")
    return x


def _coerce_one(f: Field, v, catalog: dict | None):
    name = f"filter {f.name}"
    if f.type == "bool":
        return _bool(v, name)
    if f.type == "number":
        return _num(v, name)
    if f.type == "date":
        return _date(v, name)
    if f.type == "grain":
        if f.name == "day":
            return _date(v, name)
        x = _int(v, name)
        lo, hi = f.range or (None, None)
        if f.name != "year" and lo is not None and not lo <= x <= hi:
            raise PlanError(f"{f.name} must be {lo}..{hi}, got {x}")
        return x
    s = str(v).strip()
    if isinstance(v, float) and v.is_integer():
        s = str(int(v))
    if f.ground == "company" and not re.fullmatch(r"\d{9}", s):
        raise PlanError(f"{f.name} takes a 9-digit INN from resolve(kind=company), got {v!r}")
    if f.ground == "broker":
        s = s.upper()
        if not re.fullmatch(r"BK\d{3}", s):
            raise PlanError(f"{f.name} takes a broker code like BK001 from resolve(kind=broker), got {v!r}")
    if f.ground in ("deal", "contract") and not re.fullmatch(r"\d{1,12}", s):
        raise PlanError(f"{f.name} takes a numeric id, got {v!r}")
    if f.ground == "enum" and s not in f.values:
        raise PlanError(f"unknown {f.name} value {s!r}; allowed: {_list(f.values)}")
    if f.ground == "catalog" and catalog:
        known = catalog.get(f.catalog)
        if known is not None and s not in known:
            raise PlanError(f"unknown {f.name} value {s!r}; allowed: {_list(sorted(known))}")
    return s


def _filter(s: Subject, flt: Filter, catalog: dict | None, where: str) -> Filter:
    f = _field(s, flt.field, where)
    if f.side_of:
        raise PlanError(f"{f.name} is only for group_by (with a filter on {f.side_of}), not a filter")
    ops = ("=",) if f.filter_sql else ("=", "in") if f.either else OPS_BY_TYPE[f.type]
    if f.ground == "product_variant":
        ops = ("=",)
    if flt.op not in ops:
        raise PlanError(f"op {flt.op!r} does not fit field {f.name} ({f.type}); allowed ops: {', '.join(ops)}")
    if isinstance(flt.value, tuple):
        value = tuple(_coerce_one(f, v, catalog) for v in flt.value)
        if flt.op == "between" and value[0] > value[1]:
            raise PlanError(f"between on {f.name}: the first value must be <= the second")
        if flt.op == "in":
            value = tuple(dict.fromkeys(value))
    else:
        value = _coerce_one(f, flt.value, catalog)
    return Filter(f.name, flt.op, value)


def _bounds(flt: Filter) -> tuple:
    """(lo, hi) covered by a filter on an ordered field; None = open."""
    v = flt.value
    if flt.op == "=":
        return v, v
    if flt.op == "in":
        return min(v), max(v)
    if flt.op == "between":
        return v
    if flt.op == ">=":
        return v, None
    if flt.op == "<=":
        return None, v
    return None, None


def _outside(what: str, s: Subject) -> PlanError:
    lo, hi = s.data_range
    return PlanError(f"{what} is outside the data ({s.lbl('ru')}: {_fmt(lo)}–{_fmt(hi)}); call declare_unanswerable")


def _check_ranges(s: Subject, plan: Plan) -> None:
    if s.data_range is None:
        return
    lo_d, hi_d = s.data_range
    years = plan.filters_on("year")
    for flt in years:
        if flt.op == "!=":
            continue
        lo, hi = _bounds(flt)
        lo_y, hi_y = lo_d.year, hi_d.year
        if (hi is not None and hi < lo_y) or (lo is not None and lo > hi_y):
            raise _outside(f"year {flt.value if not isinstance(flt.value, tuple) else list(flt.value)}", s)
        if flt.op in ("in",) and any(not lo_y <= y <= hi_y for y in flt.value):
            raise PlanError(f"data covers {lo_y}–{hi_y}: keep only those years in the filter and add an assumption "
                            f"that the period is covered only within it")
        if (lo is not None and lo < lo_y) or (hi is not None and hi > hi_y):
            raise PlanError(f"data covers {_fmt(lo_d)}–{_fmt(hi_d)}: clamp the year filter to "
                            f"[{max(lo or lo_y, lo_y)}, {min(hi or hi_y, hi_y)}] and add an assumption that the "
                            f"period is covered only within it")
    single_year = next((f.value for f in years if f.op == "="), None)
    for flt in plan.filters_on("month"):
        if single_year == hi_d.year and flt.op != "!=":
            lo, hi = _bounds(flt)
            if lo is not None and lo > hi_d.month:
                raise _outside(f"{lo:02d}.{hi_d.year}", s)
            if hi is not None and hi > hi_d.month:
                raise PlanError(f"data ends {_fmt(hi_d)}: end the month filter at {hi_d.month} and add an assumption "
                                f"that the period is covered only up to {_fmt(hi_d)}")
    for flt in plan.filters_on("quarter"):
        if single_year == hi_d.year and flt.op != "!=":
            lo, hi = _bounds(flt)
            q_end = (hi_d.month + 2) // 3
            if lo is not None and lo > q_end:
                raise _outside(f"Q{lo} {hi_d.year}", s)
    date_fields = [s.date_field, "day"] + [n for n, f in s.fields.items() if f.type == "date" and f.filter_sql]
    for name in date_fields:
        for flt in plan.filters_on(name):
            if flt.op == "!=":
                continue
            lo, hi = _bounds(flt)
            lo2, hi2 = lo or lo_d, hi or hi_d
            if hi2 < lo_d or lo2 > hi_d:
                raise _outside(f"{lo or '...'}..{hi or '...'}", s)
            if lo2 < lo_d or hi2 > hi_d:
                raise PlanError(f"data covers {_fmt(lo_d)}–{_fmt(hi_d)}: clamp the {name} filter to that range "
                                "and add an assumption that the period is covered only within it")


def _drop_restated_periods(plan: Plan) -> Plan:
    """With compare, year / quarter / month filters that only restate the compared periods (year=2025 next to
    compare [2025 Q1, 2026 Q1]) are dropped; any other time filter stays and is rejected by _check_compare."""
    if not plan.compare:
        return plan
    years = {p.year for p in plan.compare}
    months = [p.months() for p in plan.compare]

    def restated(f: Filter) -> bool:
        vals = f.value if isinstance(f.value, tuple) else (f.value,)
        if f.op not in ("=", "in", "between") or not all(isinstance(v, int) for v in vals):
            return False
        if f.field == "year":
            return all(v in years for v in vals)
        if f.field in ("month", "quarter") and all(m is not None for m in months):
            span = [(3 * v - 2, 3 * v) if f.field == "quarter" else (v, v) for v in vals]
            if f.op == "between":
                span = [(span[0][0], span[-1][1])]
            return all(m[0] <= a and b <= m[1] for a, b in span for m in months)
        return False

    return plan.with_(filters=tuple(f for f in plan.filters if not restated(f)))


def _check_compare(s: Subject, plan: Plan) -> None:
    if not plan.compare:
        return
    if not s.date_field:
        raise PlanError(f"compare needs a dated subject (deals, contracts, usd_rates), not {s.name}")
    if plan.group_by:
        raise PlanError("compare works only without group_by")
    time_filters = [f for f in plan.filters if f.field in ("year", "quarter", "month", "day", s.date_field)]
    if time_filters:
        raise PlanError("with compare put the periods only in compare, not in filters")
    a, b = plan.compare
    if a == b:
        raise PlanError("compare needs two different periods")
    lo_d, hi_d = s.data_range
    for p in plan.compare:
        if not lo_d.year <= p.year <= hi_d.year:
            raise _outside(f"year {p.year}", s)
        if p.quarter is not None and not 1 <= p.quarter <= 4:
            raise PlanError(f"quarter {p.quarter} must be 1..4")
        m = p.months()
        if m is not None:
            if not (1 <= m[0] <= m[1] <= 12):
                raise PlanError("compare months must be 1..12 with month_from <= month_to")
            if p.year == hi_d.year and m[0] > hi_d.month:
                raise _outside(f"{m[0]:02d}.{p.year}", s)
    lengths = [12 if p.months() is None else p.months()[1] - p.months()[0] + 1 for p in plan.compare]
    if lengths[0] != lengths[1]:
        raise PlanError("compare periods must have the same length (two years, or the same months of two years)")
    if any(p.year == hi_d.year and (p.months() is None or p.months()[1] > hi_d.month) for p in plan.compare):
        raise PlanError(f"data ends {_fmt(hi_d)}, so {hi_d.year} is not complete: compare the same months of both "
                        f"years (month_from 1, month_to {hi_d.month} in both periods) and add an assumption that "
                        f"{hi_d.year} is covered only up to {_fmt(hi_d)}")


def _share_hint(s: Subject, filters) -> str:
    msg = "share needs the part whose share is computed: mark that filter numerator:true"
    names = [f.field for f in filters]
    if "cancelled" in names and "cancelled_share" in s.measures:
        return (msg + "; for the share of annulled deals use {agg: cancelled_share} instead and drop the cancelled "
                "filter")
    others = [n for n in names if n not in ("year", "quarter", "month", "day", s.date_field)]
    if others:
        return msg + f", e.g. {{field: {others[0]}, value: ..., numerator: true}} (the rest stays the whole)"
    return msg


def _measure(s: Subject, m: Measure, catalog: dict | None, plan_filters=()) -> Measure:
    agg, of = m.agg, m.of
    if of in s.measures and agg not in s.measures:     # {"agg": "avg", "of": "price_per_kg"}
        agg, of = of, None
    if agg in s.measures:
        if m.where:
            raise PlanError(f"where is only used with agg share, not {agg}")
        return Measure(agg, None, ())
    named = sorted(s.measures)
    if agg not in ("count", "sum", "avg", "median", "min", "max", "distinct", "share"):
        raise PlanError(f"invalid agg {agg!r} for {s.name}; allowed: count, sum, avg, median, min, max, distinct, "
                        f"share{', ' if named else ''}{', '.join(named)}")
    if agg == "share" and of in ("count", s.count_col, s.name, *(n for n, f in s.fields.items() if f.type == "record")):
        of = None                                       # "share of the count", however it is spelled
    if agg == "share":
        if not m.where:
            raise PlanError(_share_hint(s, plan_filters))
        where = tuple(_filter(s, w, catalog, "share where") for w in m.where)
        if of is not None:
            f = _field(s, of, "share of")
            if f.type != "number" or "sum" not in f.aggs:
                raise PlanError(f"share of {of}: of must be a summable number field "
                                f"({', '.join(n for n, x in s.fields.items() if x.type == 'number' and 'sum' in x.aggs)})"
                                " or omitted (share of the count)")
        return Measure("share", of, where)
    if m.where:
        raise PlanError(f"where is only used with agg share, not {agg}; put conditions into filters")
    if agg == "count":
        if of is not None:
            f = _field(s, of, "measure")
            if f.type in ("dimension", "grain"):
                raise PlanError(f"count counts {s.name} and takes no field; for the number of different {of} use "
                                f"{{agg: distinct, of: {of}}}; to show the {of} of one record, list the record "
                                "(group_by [<record>] with a filter on it, no measures)")
        return Measure("count", None, ())
    if of is None:
        raise PlanError(f"agg {agg} needs of: a field of {s.name}")
    f = _field(s, of, "measure")
    if f.filter_only or agg not in f.aggs:
        fit = [n for n, x in s.fields.items() if agg in x.aggs and not x.filter_only]
        raise PlanError(f"agg {agg} does not fit field {of}; {of} allows: {', '.join(f.aggs) or 'filters only'}; "
                        f"fields allowing {agg}: {_list(fit)}")
    return Measure(agg, of, ())


def validate_plan(plan: Plan, d: Dictionary, catalog: dict | None = None) -> Plan:
    """-> the plan with typed values; raises PlanError with a fix instruction."""
    s = d.subject(plan.subject)
    filters = tuple(_filter(s, f, catalog, "filters") for f in plan.filters)
    measures = tuple(_measure(s, m, catalog, plan.filters) for m in plan.measures)
    for g in plan.group_by:
        f = _field(s, g, "group_by")
        if f.type not in GROUPABLE or f.filter_only:
            fit = [n for n, x in s.fields.items() if x.type in GROUPABLE and not x.filter_only]
            raise PlanError(f"cannot group_by {g} ({f.type}); groupable fields of {s.name}: {_list(fit)}")
    fs = [s.fields[g] for g in plan.group_by]
    if sum(1 for f in fs if f.either) > 1:
        raise PlanError("group_by takes at most one either-side field (participant, broker)")
    if any(f.either for f in fs) and any(f.type == "record" for f in fs):
        raise PlanError("an either-side field cannot be grouped together with a record")
    for f in fs:
        if f.side_of and not any(x.field == f.side_of and x.op in ("=", "in") for x in filters):
            raise PlanError(f"group_by {f.name} splits one {f.side_of} into its seller and buyer side: add a filter "
                            f"{f.side_of} = <code from resolve()>")
        if f.side_of and (any(x.either for x in fs) or any(x.type == "record" for x in fs)):
            raise PlanError(f"group_by {f.name} cannot be combined with {', '.join(x.name for x in fs if x is not f)}")
    if not measures and not any(f.type == "record" for f in fs):
        raise PlanError("measures needs 1-2 items (e.g. {agg: count}); only a record listing "
                        "(group_by deal / contract / company / broker) may have no measures")
    if plan.order == "asc" and not plan.group_by:
        raise PlanError("order applies to rankings only: set group_by (e.g. deal, seller, product) with order")
    if plan.limit is not None and not plan.group_by:
        raise PlanError("limit applies to rankings only: set group_by with limit")
    out = plan.with_(filters=filters, measures=measures)
    for name in ("month", "quarter"):
        if out.filters_on(name) and not out.filters_on("year") and "year" not in out.group_by:
            if s.fields.get(name):
                raise PlanError(f"a {name} filter needs a year filter; for a cross-year range filter "
                                f"{s.date_field} with between")
    out = _drop_restated_periods(out)
    _check_ranges(s, out)
    _check_compare(s, out)
    return out


def grounding_pairs(plan: Plan, d: Dictionary) -> set[tuple[str, str]]:
    """(kind, value) pairs that must be grounded by resolve() or the question (writer's `allowed` kinds)."""
    s = d.subject(plan.subject)
    out: set[tuple[str, str]] = set()
    flts = list(plan.filters) + [w for m in plan.measures for w in m.where]
    for flt in flts:
        f = s.fields[flt.field]
        kind = GROUND_KIND.get(f.ground)
        if kind is None:
            continue
        values = flt.value if isinstance(flt.value, tuple) else (flt.value,)
        out |= {(kind, str(v)) for v in values}
    return out
