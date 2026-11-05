"""Generic, language-independent consistency checks and business defaults for a validated plan."""
import re

from app.agent import guard as G
from app.agent.writer import _DATE_RE, _MONTH_RE, _STATUS_WORDS
from app.plan.dictionary import Dictionary, Subject
from app.plan.model import Filter, Plan
from app.text import normalize_text

DEFAULT_LIMIT = 10
_YEAR_RE = re.compile(r"(?<![\d.])(20[12]\d)(?![\d])")


def _scope_free(s: Subject, plan: Plan) -> bool:
    return all(m.agg == "share" or (m.agg in s.measures and s.measures[m.agg].scope_free) for m in plan.measures)


def _status_filtered(s: Subject, plan: Plan) -> bool:
    return any(f.field in s.status_fields for f in plan.filters)


def _record_key_filtered(s: Subject, plan: Plan) -> bool:
    return any(s.fields[f.field].type == "record" and f.op in ("=", "in") for f in plan.filters)


def _valid_only(f) -> bool:
    """A filter meaning just "without annulled deals"."""
    return ((f.field == "cancelled" and f.op == "=" and f.value is False)
            or (f.field == "cancelled" and f.op == "!=" and f.value is True)
            or (f.field == "status" and f.op == "!=" and f.value == "Аннулирован(Не оплачен)"))


def apply_defaults(plan: Plan, d: Dictionary, question: str) -> Plan:
    """Status view for deals (as the structured form did) and the default limit of a large grouping."""
    s = d.subject(plan.subject)
    if s.cancelled:
        q = question.lower()
        ungrouped = not plan.group_by
        if ungrouped and plan.measures and not _scope_free(s, plan) and not any(w in q for w in _STATUS_WORDS):
            # ungrouped counts / totals show all deals and, separately, without annulled ones unless the
            # question names a status: a status filter the question does not ask for is dropped
            plan = plan.with_(filters=tuple(f for f in plan.filters if not _valid_only(f)))
        if _status_filtered(s, plan) or _record_key_filtered(s, plan):
            scope = "all"
        elif plan.measures and _scope_free(s, plan):
            scope = "all"
        elif plan.group_by:
            scope = "valid"          # rankings, breakdowns and record listings: deals that took place
        else:
            scope = "split"
        plan = plan.with_(scope=scope)
    fields = [s.fields[g] for g in plan.group_by]
    if plan.limit is None and any(f.many or f.type == "record" for f in fields) and not _record_key_filtered(s, plan):
        plan = plan.with_(limit=DEFAULT_LIMIT, default_limit=True)
    return plan


def _has(plan: Plan, s: Subject, names: tuple[str, ...]) -> bool:
    return any(f.field in names for f in plan.filters) or any(g in names for g in plan.group_by)


def period_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    """A date, month/quarter or year named in the question must appear in the plan."""
    s = d.subject(plan.subject)
    date_fields = tuple(n for n, f in s.fields.items() if f.type == "date") + ("day",)
    m = _DATE_RE.search(question)
    if m and not _has(plan, s, date_fields):
        if not s.date_field:
            return f"the question names the date {m.group(1)} but {s.name} has no dates; use a dated subject"
        return (f"the question mentions the date {m.group(1)}; add a filter on {s.date_field} "
                f"(op = or between)")
    if _MONTH_RE.search(question) and not (_has(plan, s, ("month", "quarter") + date_fields)
                                           or any(p.months() is not None for p in plan.compare)):
        return ("the question mentions a month/quarter; add it (filters month or quarter with year, or "
                "compare periods with month_from/month_to or quarter)")
    years = {int(y) for y in _YEAR_RE.findall(question)}
    if years and not (_has(plan, s, ("year",) + date_fields) or plan.compare):
        if not s.date_field:
            return (f"the question names the year {min(years)} but {s.name} has no dates; for activity in a year "
                    "use subject deals (e.g. distinct buyer / seller / broker)")
        return f"the question mentions the year {min(years)}; add a year filter (op = or between) or compare"
    return None


def consistency_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    s = d.subject(plan.subject)
    for g in plan.group_by:
        single = [f for f in plan.filters if f.field == g and f.op == "="]
        if single and (plan.limit is not None or s.fields[g].type == "grain") and s.fields[g].type != "record":
            return (f"group_by {g} while {g} is filtered to one value ({single[0].value}) gives one row: drop {g} "
                    "from group_by, or filter several values with op in")
    records = [g for g in plan.group_by if s.fields[g].type == "record"]
    for m in plan.measures:
        if records and m.agg in ("avg", "median", "count", "distinct", "share"):
            return (f"group_by {records[0]} lists single records, so {m.agg} is computed per record: for one value "
                    f"over all {s.name} drop group_by; to find a record use sum/min/max of the field with order "
                    "and limit")
        if m.agg == "distinct" and m.of in plan.group_by:
            return (f"distinct of {m.of} while grouping by {m.of} is always 1: use count, or group by another field")
        if m.agg == "share":
            if all(w in plan.filters for w in m.where):
                return ("the share numerator equals the filtered scope, so the share is 100%: mark the part's "
                        "condition numerator:true and do not repeat it as a plain filter")
            grouped = [w.field for w in m.where if w.field in plan.group_by]
            if grouped:
                return (f"the share numerator filters {grouped[0]} while grouping by {grouped[0]}: every group is "
                        f"then 0% or 100%. For the share of each {grouped[0]} in the total, use {{agg: share}} "
                        f"without group_by and one numerator value, or group_by {grouped[0]} with sum/count only")
    return period_error(plan, d, question) or period_value_error(plan, d, question)


# ---- period values: a year / month named in the question must be inside the plan's period ----
_MONTHS = [(re.compile(rx, re.IGNORECASE), n) for n, rx in enumerate([
    r"январ|yanvar", r"феврал|fevral", r"(?<!\w)(?:март|mart)", r"апрел|aprel", r"(?<!\w)(?:ма[йяе]|may)(?!\w)",
    r"июн|iyun", r"июл|iyul", r"август|avgust", r"сентябр|sentyabr", r"октябр|oktyabr", r"ноябр|noyabr",
    r"декабр|dekabr"], start=1)]


def years_covered(plan: Plan, s: Subject):
    """-> predicate year -> bool: is the year inside the plan's period (year / date filters or compare periods);
    a range clamped to the data tolerates years outside the data on the clamped side. None: no period in the plan."""
    lo_d, hi_d = (s.data_range[0].year, s.data_range[1].year) if s.data_range else (None, None)
    checks = []
    for flt in plan.filters:
        f = s.fields.get(flt.field)
        if f is None or flt.op == "!=" or not (flt.field == "year" or f.type == "date"
                                                or (flt.field == "day" and f.type == "grain")):
            continue
        vals = [v if isinstance(v, int) else v.year for v in (flt.value if isinstance(flt.value, tuple)
                                                               else (flt.value,))]
        if flt.op in ("=", "in"):
            checks.append(lambda y, vals=vals: y in vals)
        elif flt.op == "between":
            checks.append(lambda y, a=vals[0], b=vals[1]: a <= y <= b or (y < a and a == lo_d) or (y > b and b == hi_d))
        elif flt.op == ">=":
            checks.append(lambda y, a=vals[0]: y >= a or a == lo_d)
        elif flt.op == "<=":
            checks.append(lambda y, b=vals[0]: y <= b or b == hi_d)
    if plan.compare:
        years = {p.year for p in plan.compare}
        checks.append(lambda y: y in years)
    if not checks:
        return None
    return lambda y: any(c(y) for c in checks)


def named_months(question: str) -> set[int]:
    return {n for rx, n in _MONTHS if rx.search(question)}


def period_value_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    s = d.subject(plan.subject)
    years = sorted({int(y) for y in _YEAR_RE.findall(question)})
    covered = years_covered(plan, s)
    if covered is not None:
        for y in years:
            if not covered(y):
                return (f"the question names the year {y} but the plan's period does not include it: set the year "
                        "filter (a range is op between [from, to]) or compare periods exactly as the question says")
    months = named_months(question)
    month_filters = [f for f in plan.filters if f.field == "month" and f.op != "!="]
    if months and month_filters:
        for mo in sorted(months):
            if not any(_in_filter(mo, f) for f in month_filters):
                return (f"the question names month {mo} but the month filter is {_show_value(month_filters[0])}: "
                        "set it exactly as the question says")
    compare_months = [p.months() for p in plan.compare if p.months() is not None]
    if months and compare_months:
        for mo in sorted(months):
            if not any(a <= mo <= b for a, b in compare_months):
                return f"the question names month {mo} but no compare period includes it: fix month_from / month_to"
    return None


def _in_filter(v: int, f: Filter) -> bool:
    vals = f.value if isinstance(f.value, tuple) else (f.value,)
    if f.op in ("=", "in"):
        return v in vals
    if f.op == "between":
        return vals[0] <= v <= vals[1]
    if f.op == ">=":
        return v >= vals[0]
    if f.op == "<=":
        return v <= vals[0]
    return True


def _show_value(f: Filter) -> str:
    return f"{f.op} {list(f.value) if isinstance(f.value, tuple) else f.value}"


# ---- missing-condition guard on plans (the legacy guard's detectors, mapped to fields.yaml names) ----
_CANCELLED = "Аннулирован(Не оплачен)"
_UNIT_FIELDS = {"tonnes", "volume", "lots", "delivered_lots", "unit_price_uzs", "deal_price_per_kg", "base_price",
                "lot"}


def _catalog_of(s: Subject, name: str) -> str:
    f = s.fields.get(name)
    if f is None:
        return ""
    return f.catalog or (name if f.ground == "enum" else "")


def _plan_filters(plan: Plan) -> list[Filter]:
    return list(plan.filters) + [w for m in plan.measures for w in m.where]


def _plan_strings(s: Subject, plan: Plan) -> dict[str, set[str]]:
    """guard catalog field -> normalized values the plan filters on (a cancelled filter names the annulled status)."""
    out: dict[str, set[str]] = {}
    for f in _plan_filters(plan):
        vals = f.value if isinstance(f.value, tuple) else (f.value,)
        if f.field == "cancelled":
            out.setdefault("status", set()).add(normalize_text(_CANCELLED))
            continue
        cat = _catalog_of(s, f.field) or f.field
        out.setdefault(cat, set()).update(normalize_text(str(v)) for v in vals)
    return out


def _plan_covers(v, s: Subject, plan: Plan, strings, question: str, named: set[str], product_attrs) -> bool:
    value = normalize_text(v.value)
    if any(_catalog_of(s, g) == v.field for g in plan.group_by):
        return True
    if v.field == "unit" and any(m.agg == "share" or m.of in _UNIT_FIELDS
                                 or (m.agg in s.measures and (s.measures[m.agg].per_unit or m.agg == "price_per_kg"))
                                 for m in plan.measures):
        return True        # the unit names the output
    if v.field == "status" and value == normalize_text(_CANCELLED) \
            and any(m.agg == "cancelled_share" for m in plan.measures):
        return True
    products = [str(x) for f in plan.filters if f.field == "product"
                for x in (f.value if isinstance(f.value, tuple) else (f.value,))]
    if products and product_attrs and v.field in G._PRODUCT_FIELDS:
        fields_ok = [v.field] + (["product_family"] if v.field in ("category", "industry")
                                 and not G._CATEGORY_WORD_RE.search(question) else [])
        if all(any(product_attrs.get(p, {}).get(f) == {v.value} for f in fields_ok) for p in products):
            return True
    if v.field in ("category", "industry") and not G._CATEGORY_WORD_RE.search(question) and (
            value in strings.get("product_family", set()) or "product_family" in plan.group_by):
        return True
    set_values = strings.get(v.field, set())
    if value in set_values:
        return True
    if set_values and G._NEGATION_RE.search(question):
        return True        # complement, e.g. "кроме экспорта" -> Внутренний
    return v.field in G._SINGLE_VALUED and bool(set_values & named)


def plan_missing_condition_error(question: str, plan: Plan, d: Dictionary, index, ignore_names=(),
                                 product_attrs=None) -> str | None:
    """The legacy missing-condition guard on a plan: a condition named in the question (catalog / enum value, grade
    or mark, least, distinct, share and its basis) must be in the plan. -> fix instruction or None."""
    s = d.subject(plan.subject)
    strings = _plan_strings(s, plan)
    found = G.mentioned_values(question, index, ignore_names)
    by_field: dict[str, set[str]] = {}
    for v in found:
        by_field.setdefault(v.field, set()).add(v.value)
    for v in found:
        if v.field == "contract_form" and by_field["contract_form"] >= set(G.CONTRACT_FORMS):
            continue       # all forms named: no restriction
        named = {normalize_text(x) for x in by_field[v.field]}
        if _plan_covers(v, s, plan, strings, question, named, product_attrs):
            continue
        fields = [n for n in s.fields if _catalog_of(s, n) == v.field] + (
            ["cancelled"] if v.field == "status" and "cancelled" in s.fields else [])
        where = (f"a filter on {' or '.join(fields)} (or group_by it)" if fields
                 else f"another subject (deals) that has the {v.field} field")
        return (f"the question names «{v.value}» ({v.field}) but the plan does not use it: add {where}. If the "
                "plan cannot express this condition, call declare_unanswerable — never drop a condition")
    q = question.replace("ё", "е").replace("Ё", "Е")
    fields = {f.field for f in _plan_filters(plan)}
    if G._GRADE_RE.search(question) and "product_variant" in s.fields and "product" in fields \
            and "product_variant" not in fields:
        return ("the question names a grade / mark of the product: resolve(kind=product_variant) the product phrase "
                "with the grade / mark (e.g. «хлопок-сырец 1 сорт») and filter product_variant = <that text> "
                "instead of product")
    grains = [g for g in plan.group_by if s.fields[g].type == "grain"]
    ranked = bool(plan.group_by) and not (len(grains) == len(plan.group_by) and plan.limit is None)
    least_ok = G._EACH_RE.search(q) or any(m.agg == "min" for m in plan.measures)
    if ranked and plan.order != "asc" and not least_ok and G._LEAST_RE.search(q):
        return "the question asks for the least / cheapest / fewest: set order asc so the ranking starts from the smallest"
    if G._DISTINCT_RE.search(q) and not any(m.agg == "distinct" for m in plan.measures):
        return ("the question asks how many different items: use {agg: distinct, of: <field>} without grouping by "
                "that field")
    if not G._SHARE_RE.search(q):
        return None
    shares = [m for m in plan.measures if m.agg == "share"]
    for m in shares:
        if m.of is None:
            for basis, rx in G._SHARE_BASIS_RE.items():
                if rx.search(q):
                    of = "amount_uzs" if basis == "amount_uzs" else "tonnes"
                    return (f"the question asks for a share of money / volume, not of the count: set the share "
                            f"measure's of to {of}")
    if any(m.agg == "cancelled_share" for m in plan.measures):
        names = {m.of for m in plan.measures}
        for basis, rx in G._SHARE_BASIS_RE.items():
            money = {"amount_uzs", "amount_usd"} if basis == "amount_uzs" else {"tonnes", "volume"}
            if rx.search(q) and (G._ATTACHED_BASIS_RE[basis].search(q) or not money & names):
                of = "amount_uzs" if basis == "amount_uzs" else "tonnes"
                return ("the question asks for a share of money / volume, but cancelled_share is a share of the "
                        f"count: use {{agg: share, of: {of}}} with the filter cancelled = true marked numerator:true")
    percent = shares or any(m.agg == "cancelled_share" or (m.of or "").endswith("_pct") for m in plan.measures)
    if not (percent or plan.compare):
        return ("the question asks for a share / percentage: use {agg: share} with the part's filters marked "
                "numerator:true (or cancelled_share); if the share cannot be expressed, call declare_unanswerable")
    return None
