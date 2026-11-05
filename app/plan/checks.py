"""Generic, language-independent consistency checks and business defaults for a validated plan."""
import re

from app.agent.writer import _DATE_RE, _MONTH_RE, _STATUS_WORDS
from app.plan.dictionary import Dictionary, Subject
from app.plan.model import Plan

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
    return period_error(plan, d, question)
