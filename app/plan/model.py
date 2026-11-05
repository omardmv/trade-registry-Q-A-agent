"""Plan dataclasses and structural parsing of the planner's run_plan(plan) argument.

Shape checks only (keys, types, counts); everything that needs the field dictionary is in validate.py.
"""
from dataclasses import dataclass, field, replace
from typing import Any

SUBJECTS = ("deals", "contracts", "companies", "brokers", "usd_rates")
AGGS = ("count", "sum", "avg", "median", "min", "max", "distinct", "share")
OPS = ("=", "in", "!=", "between", ">=", "<=")
ORDERS = ("desc", "asc")
PERIOD_KEYS = ("year", "quarter", "month_from", "month_to")
PLAN_KEYS = ("subject", "measures", "filters", "numerator", "group_by", "order", "limit", "compare", "mentions")
MAX_LIMIT = 50


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: Any                      # scalar, or tuple for in / between


@dataclass(frozen=True)
class Measure:
    agg: str                        # count | sum | ... | share | a named measure of the subject
    of: str | None = None
    where: tuple[Filter, ...] = ()  # share: numerator condition


@dataclass(frozen=True)
class Mention:
    text: str                       # a phrase copied from the question
    used_as: str                    # filter:<field> | group_by:<field> | measure:<i> | order | limit | subject | compare


@dataclass(frozen=True)
class Period:
    year: int
    quarter: int | None = None
    month_from: int | None = None
    month_to: int | None = None

    def months(self) -> tuple[int, int] | None:
        if self.quarter is not None:
            return 3 * self.quarter - 2, 3 * self.quarter
        if self.month_from is not None:
            return self.month_from, self.month_to if self.month_to is not None else self.month_from
        return None


@dataclass(frozen=True)
class Plan:
    subject: str
    measures: tuple[Measure, ...] = ()
    filters: tuple[Filter, ...] = ()
    group_by: tuple[str, ...] = ()
    order: str = "desc"
    limit: int | None = None
    compare: tuple[Period, ...] = ()
    default_limit: bool = False     # set by checks.apply_defaults when the limit was not asked for
    scope: str = ""                 # deals status view chosen by checks.apply_defaults: all | valid | split
    mentions: tuple[Mention, ...] = ()

    def with_(self, **kw) -> "Plan":
        return replace(self, **kw)

    def filters_on(self, name: str) -> list[Filter]:
        return [f for f in self.filters if f.field == name]


def _empty(v) -> bool:
    return v is None or (isinstance(v, (str, list, tuple, dict)) and len(v) == 0)


def _int(v, what: str) -> int:
    if isinstance(v, bool):
        raise PlanError(f"{what} must be an integer, got {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    raise PlanError(f"{what} must be an integer, got {v!r}")


def _opt_int(d: dict, key: str, what: str) -> int | None:
    v = d.get(key)
    return None if _empty(v) else _int(v, what)


def parse_filter(d, where: str = "filters") -> Filter:
    if not isinstance(d, dict):
        raise PlanError(f"each item of {where} must be an object {{field, op, value}}, got {d!r}")
    unknown = [k for k in d if k not in ("field", "op", "value", "values", "numerator") and not _empty(d[k])]
    if unknown:
        raise PlanError(f"unknown key {unknown[0]!r} in {where} item; allowed: field, op, value (values for in/between), "
                        "numerator")
    name = str(d.get("field") or "").strip()
    if not name:
        raise PlanError(f"{where} item needs a field")
    op = str(d.get("op") or "=").strip().lower()
    if op == "==":
        op = "="
    if op not in OPS:
        raise PlanError(f"invalid op {op!r} in {where}; allowed: {', '.join(OPS)}")
    value, values = d.get("value"), d.get("values")
    if not _empty(values):
        values = list(values) if isinstance(values, (list, tuple)) else [values]
        if not _empty(value) and not isinstance(value, (list, tuple)) and value not in values:
            values = [value] + values        # {value: a, values: [b]} -> [a, b]
        value = values
    if isinstance(value, list):
        value = tuple(value)
    if op in ("in", "between") and not isinstance(value, tuple):
        if isinstance(value, str) and "," in value and op == "between":
            value = tuple(x.strip() for x in value.split(","))
        else:
            value = (value,)
    if op not in ("in", "between") and isinstance(value, tuple):
        if len(value) == 1:
            value = value[0]
        elif op == "=":
            op = "in"
        else:
            raise PlanError(f"op {op!r} takes one value; use in for a list")
    if op == "in" and len(value) == 1:
        op, value = "=", value[0]
    if op == "between" and len(value) != 2:
        raise PlanError(f"between needs exactly two values [from, to], got {list(value)!r}")
    if value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, tuple) and not value):
        raise PlanError(f"filter on {name} needs a value")
    return Filter(name, op, value)


def parse_measure(d) -> Measure:
    if isinstance(d, str):
        d = {"agg": d}
    if not isinstance(d, dict):
        raise PlanError(f"each measure must be an object {{agg, of}}, got {d!r}")
    unknown = [k for k in d if k not in ("agg", "of", "where") and not _empty(d[k])]
    if unknown:
        raise PlanError(f"unknown key {unknown[0]!r} in measure; allowed: agg, of, where (share only)")
    agg = str(d.get("agg") or "").strip().lower()
    if not agg:
        raise PlanError("each measure needs agg")
    of = d.get("of")
    of = None if _empty(of) else str(of).strip()
    where_raw = d.get("where") or []
    if isinstance(where_raw, dict):
        where_raw = [where_raw]
    where = tuple(parse_filter(w, "measure.where") for w in where_raw)
    return Measure(agg, of, where)


def parse_period(d) -> Period:
    if not isinstance(d, dict):
        raise PlanError(f"each compare item must be an object {{year, quarter | month_from, month_to}}, got {d!r}")
    unknown = [k for k in d if k not in PERIOD_KEYS and not _empty(d[k])]
    if unknown:
        raise PlanError(f"unknown key {unknown[0]!r} in compare item; allowed: {', '.join(PERIOD_KEYS)}")
    year = _opt_int(d, "year", "compare.year")
    if year is None:
        raise PlanError("each compare item needs a year")
    p = Period(year, _opt_int(d, "quarter", "compare.quarter"), _opt_int(d, "month_from", "compare.month_from"),
               _opt_int(d, "month_to", "compare.month_to"))
    if p.quarter is not None and (p.month_from is not None or p.month_to is not None):
        raise PlanError("a compare item has either quarter or month_from/month_to")
    if p.month_from is None and p.month_to is not None:
        p = replace(p, month_from=p.month_to)
    if p.month_from is not None and p.month_to is None:
        p = replace(p, month_to=p.month_from)
    return p


def parse_mention(d) -> Mention:
    if not isinstance(d, dict):
        raise PlanError(f"each mention must be an object {{text, used_as}}, got {d!r}")
    unknown = [k for k in d if k not in ("text", "used_as") and not _empty(d[k])]
    if unknown:
        raise PlanError(f"unknown key {unknown[0]!r} in mention; allowed: text, used_as")
    text = str(d.get("text") or "").strip()
    used = "".join(str(d.get("used_as") or "").split()).lower()
    if not text or not used:
        raise PlanError("each mention needs text (a phrase copied from the question) and used_as")
    return Mention(text, used)


def parse_plan(d) -> Plan:
    if not isinstance(d, dict):
        raise PlanError(f"plan must be an object, got {type(d).__name__}")
    unknown = [k for k in d if k not in PLAN_KEYS and not _empty(d[k])]
    if unknown:
        raise PlanError(f"unknown key {unknown[0]!r} in plan; allowed: {', '.join(PLAN_KEYS)}")
    subject = str(d.get("subject") or "").strip()
    if subject not in SUBJECTS:
        raise PlanError(f"invalid subject {subject!r}; allowed: {', '.join(SUBJECTS)}")
    measures_raw = d.get("measures") or []
    if isinstance(measures_raw, (dict, str)):
        measures_raw = [measures_raw]
    measures = tuple(parse_measure(m) for m in measures_raw)
    if len(measures) > 2:
        raise PlanError("at most 2 measures per plan; to show the fields of one record (buyer, seller, product, "
                        "amount, status ...) list it: group_by [deal | contract | company | broker] with a filter on "
                        "it and no measures")
    filters_raw = d.get("filters") or []
    if isinstance(filters_raw, dict):
        filters_raw = [filters_raw]
    for f in filters_raw:
        if isinstance(f, dict) and f.get("numerator") not in (None, False, True, "", "true", "false"):
            raise PlanError("filter numerator is true or false")
    # numerator:true on a plan without a share is a plain filter (the condition still applies)
    has_share = any(m.agg == "share" for m in measures)
    is_num = [has_share and isinstance(f, dict) and f.get("numerator") in (True, "true") for f in filters_raw]
    filters = tuple(parse_filter(f) for f, n in zip(filters_raw, is_num) if not n)
    num_raw = d.get("numerator") or []
    if isinstance(num_raw, dict):
        num_raw = [num_raw]
    numerator = tuple(parse_filter(f, "numerator") for f, n in zip(filters_raw, is_num) if n)
    numerator += tuple(parse_filter(f, "numerator") for f in num_raw)
    if numerator:
        if not any(m.agg == "share" for m in measures):
            raise PlanError("numerator is only used with the share measure ({agg: share})")
        # the plan-level numerator is the where of every share measure that has none of its own
        measures = tuple(Measure(m.agg, m.of, numerator) if m.agg == "share" and not m.where else m for m in measures)
    group_raw = d.get("group_by") or []
    if isinstance(group_raw, str):
        group_raw = [group_raw]
    group_by = tuple(str(g).strip() for g in group_raw if str(g).strip())
    if len(group_by) > 2:
        raise PlanError("group_by takes at most 2 fields")
    if len(set(group_by)) != len(group_by):
        raise PlanError("group_by lists a field twice")
    order = str(d.get("order") or "desc").strip().lower()
    if order not in ORDERS:
        raise PlanError(f"invalid order {order!r}; allowed: desc, asc")
    limit = _opt_int(d, "limit", "limit") or None      # 0 = no limit
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        raise PlanError(f"limit must be 1..{MAX_LIMIT}")
    compare_raw = d.get("compare") or []
    compare = tuple(parse_period(p) for p in compare_raw)
    if compare and len(compare) != 2:
        raise PlanError("compare must list exactly two periods")
    mentions_raw = d.get("mentions") or []
    if isinstance(mentions_raw, dict):
        mentions_raw = [mentions_raw]
    mentions = tuple(parse_mention(m) for m in mentions_raw)
    return Plan(subject, measures, filters, group_by, order, limit, compare, mentions=mentions)
