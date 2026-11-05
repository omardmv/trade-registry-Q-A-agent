"""Generic, language-independent consistency checks and business defaults for a validated plan."""
import re

from app.agent import guard as G
from app.agent.writer import _DATE_RE, _MONTH_RE, _STATUS_WORDS, strip_times_word
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


# "all statuses / including annulled": every state counts, also in rankings, breakdowns and trends
ALL_STATUSES_RE = re.compile(
    r"вс[еих]+\s+(?:статус|состояни)|любо[мйг]\w*\s+(?:статус|состояни)|независимо\s+от\s+(?:статус|состояни)|"
    r"включая\s+аннулир|с\s+учётом\s+аннулир|с\s+учетом\s+аннулир|вместе\s+с\s+аннулир|"
    r"(?:hamma|barcha|har\s+qanday)\s+holat|(?:ҳамма|барча|ҳар\s+қандай)\s+ҳолат|"
    r"bekor\s+qilingan\w*\s+(?:bilan|ham)\b|бекор\s+қилинган\w*\s+(?:билан|ҳам)(?!\w)", re.IGNORECASE)


# «все сделки» / «barcha bitimlar» name every deal, not a status: a grouping keeps both views for them
_ALL_DEALS_WORDS = ("все сделки", "всех сделок", "barcha bitim")


def names_status(question: str, grouped: bool = False) -> bool:
    """The question names a deal status (annulled, paid, executed, ...; app/agent/writer._STATUS_WORDS), in Russian
    or in Uzbek of either script (matched on the normalize_text form too: бекор -> bekor, to‘lan -> tolan)."""
    q, n = question.lower(), normalize_text(question)
    words = [w for w in _STATUS_WORDS if not (grouped and w in _ALL_DEALS_WORDS)]
    return any(w in q or normalize_text(w) in n for w in words)


# Extreme words of a ranking (normalize_text form: Russian transliterated, Uzbek Cyrillic in Latin). "low": the
# question asks for the smallest values (самый дешёвый, наименьший, минимальный, меньше всего, eng arzon, eng kam);
# "high": the largest (самый дорогой, наибольший, больше всего, eng qimmat, eng ko‘p).
_LOW_RE = re.compile(
    r"\b(?:sam\w*\s+(?:deshyov|deshev|nizk|malen|melk|redk)\w*|naimen\w*|(?<!kak )minim\w*|"
    r"(?:menshe|deshevle|nije|reje)\s+vse(?:go|x)|vse(?:go|x)\s+(?:menshe|deshevle|reje)|"
    r"eng\s+(?:arzon|kam(?!ida)|past|kichik|oz)\w*)")
_HIGH_RE = re.compile(
    r"\b(?:sam\w*\s+(?:dorog|visok|bolsh|krupn|chast)\w*|naibol\w*|maksim\w*|"
    r"(?:bolshe|doroje|vishe|chashe)\s+vse(?:go|x)|vse(?:go|x)\s+(?:bolshe|doroje|chashe)|"
    r"eng\s+(?:qimmat|kop|yuqori|baland|katta|yirik)\w*)")


def extreme_order(plan: Plan, question: str) -> str | None:
    """The order a ranked plan must have, or None when nothing decides it. The question's extreme words decide
    when they point one way (самый дешёвый / eng arzon -> asc; больше всего / eng qimmat -> desc); with no
    extreme word (or words of both kinds: «самый высокий минимум») a ranking by min sorts asc and by max desc."""
    if plan.limit is None or not plan.measures or not plan.group_by:
        return None
    q = normalize_text(question)
    low, high = bool(_LOW_RE.search(q)), bool(_HIGH_RE.search(q))
    if low != high:
        return "asc" if low else "desc"
    if low and high:
        return None
    aggs = {m.agg for m in plan.measures}
    if aggs == {"min"}:
        return "asc"
    if aggs == {"max"}:
        return "desc"
    return None


def apply_defaults(plan: Plan, d: Dictionary, question: str) -> Plan:
    """Status view for deals (as the structured form did), the default limit of a large grouping, and the order
    of a ranking by an extreme (extreme_order)."""
    s = d.subject(plan.subject)
    if s.cancelled:
        if plan.group_by and ALL_STATUSES_RE.search(question):
            # the question asks for every state: a "without annulled" filter is not what it asked
            plan = plan.with_(filters=tuple(f for f in plan.filters if not _valid_only(f)))
        if plan.measures and not _scope_free(s, plan) and not names_status(question):
            # counts / totals, also grouped (rankings, breakdowns, trends), show all deals and, separately, without
            # annulled ones unless the question names a status: a status filter it does not ask for is dropped
            plan = plan.with_(filters=tuple(f for f in plan.filters if not _valid_only(f)))
        if _status_filtered(s, plan) or _record_key_filtered(s, plan) or any(g in s.status_fields
                                                                              for g in plan.group_by):
            scope = "all"            # a status filter, one record, or one row per status: every state counts
        elif plan.measures and _scope_free(s, plan):
            scope = "all"
        elif plan.group_by and ALL_STATUSES_RE.search(question):
            scope = "all"            # «все статусы», «barcha holatlar», «включая аннулированные»
        elif plan.group_by and (not plan.measures or names_status(question, grouped=True)):
            scope = "valid"          # record listings, and groupings whose question names a status word
        else:
            scope = "split"          # all deals and, separately, without annulled (rankings rank by the latter)
        plan = plan.with_(scope=scope)
    fields = [s.fields[g] for g in plan.group_by]
    if plan.limit is None and any(f.many or f.type == "record" for f in fields) and not _record_key_filtered(s, plan):
        plan = plan.with_(limit=DEFAULT_LIMIT, default_limit=True)
    want = extreme_order(plan, question)
    if want is not None and plan.order != want:
        plan = plan.with_(order=want)
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
    if _MONTH_RE.search(strip_times_word(question)) and not (_has(plan, s, ("month", "quarter") + date_fields)
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
        if m.agg in ("distinct", "count") and _record_key_filtered(s, plan) and not plan.group_by:
            return (f"the plan filters one record, so {m.agg} is 0 or 1: to show its fields list it: group_by "
                    "[<record>] with the same filter and no measures")
        if m.agg == "distinct" and m.of in plan.group_by:
            return (f"distinct of {m.of} while grouping by {m.of} is always 1: use count, or group by another field")
        if m.agg == "share":
            if m.where and all(_valid_only(w) for w in m.where) and _WITHOUT_CANCELLED_RE.search(question):
                return ("«without annulled» is the scope of the question, not the part: make cancelled = false a "
                        "plain filter, and mark the part the question asks about (e.g. contract_type, product) "
                        "numerator:true")
            if all(w in plan.filters for w in m.where):
                return ("the share numerator equals the filtered scope, so the share is 100%: mark the part's "
                        "condition numerator:true and do not repeat it as a plain filter")
            if (err := degenerate_share_error(s, plan, m)) is not None:
                return err
            grouped = [w.field for w in m.where if w.field in plan.group_by]
            if grouped:
                return (f"the share numerator filters {grouped[0]} while grouping by {grouped[0]}: every group is "
                        f"then 0% or 100%. For the share of each {grouped[0]} in the total, use {{agg: share}} "
                        f"without group_by and one numerator value, or group_by {grouped[0]} with sum/count only")
    if (err := share_party_error(plan, d, question)) is not None:
        return err
    return (period_error(plan, d, question) or period_value_error(plan, d, question)
            or named_day_error(plan, d, question) or grain_word_error(plan, d, question)
            or unasked_grain_error(plan, d, question) or extreme_period_error(plan, d, question)
            or id_kind_error(plan, d, question)
            or not_in_data_error(question) or role_word_error(plan, d, question)
            or currency_error(plan, question) or side_error(plan, question)
            or counted_parties_error(plan, d, question) or elapsed_vs_term_error(plan, d, question))


# ---- a share that is 0% or 100% by construction ----
def _allowed(s: Subject, flt: Filter):
    """(key, set of values the filter lets through, all values of the key or None) for a filter on a categorical
    field, or None when the filter is not a plain value set (numbers, dates, ranges). A flag declared with
    status_values (cancelled, payment_late) is a set of status values: key "status"."""
    f = s.fields.get(flt.field)
    if f is None or flt.op not in ("=", "in", "!="):
        return None
    vals = set(flt.value) if flt.op == "in" else {flt.value}
    if f.type == "bool" and f.status_values and "status" in s.fields and s.fields["status"].values:
        universe = frozenset(s.fields["status"].values)
        true_set = frozenset(f.status_values)
        if not all(isinstance(v, bool) for v in vals):
            return None
        out = frozenset().union(*(true_set if v else universe - true_set for v in vals))
        return "status", (universe - out if flt.op == "!=" else out), universe
    if f.type == "bool":
        universe = frozenset({True, False})
        out = frozenset(vals)
        return f.name, (universe - out if flt.op == "!=" else out), universe
    if f.type not in ("dimension",) or f.either:
        return None
    universe = frozenset(f.values) if f.values else None
    if flt.op == "!=":
        return (f.name, universe - frozenset(vals), universe) if universe is not None else None
    return f.name, frozenset(vals), universe


def _sets(s: Subject, filters) -> dict | None:
    """key -> (allowed set, universe) intersected over the filters; None when a filter cannot be read as a set."""
    out: dict = {}
    for flt in filters:
        a = _allowed(s, flt)
        if a is None:
            return None
        key, allowed, universe = a
        prev = out.get(key)
        out[key] = (allowed if prev is None else prev[0] & allowed, universe)
    return out


def degenerate_share_error(s: Subject, plan: Plan, m) -> str | None:
    """A share whose scope already decides the numerator is 100% (or 0%) whatever the data: «какой процент сделок
    аннулирован из-за неоплаты» planned as scope cancelled = true, numerator payment_late = true (h006) is 100%,
    because in the data a deal is annulled exactly when its payment is late (both are the status «Аннулирован(Не
    оплачен)», fields.yaml status_values). The equal-filter check above misses it: the two filters are on
    different fields. Here every filter on a categorical field is read as a set of values per key (a flag with
    status_values as a set of statuses); the share is 100% when on every key of the numerator the scope's set lies
    inside the numerator's set, and 0% when on some key the two sets do not meet."""
    num = _sets(s, m.where)
    if not num:
        return None
    scope = _sets(s, [f for f in plan.filters if _allowed(s, f) is not None]) or {}
    for key, (n_set, _) in num.items():
        if key in scope and not (scope[key][0] & n_set):
            return _degenerate_msg(plan, m, "0%", key)
    rows = {key: scope[key][0] if key in scope else universe for key, (_, universe) in num.items()}
    if all(rows[key] is not None and rows[key] <= n_set for key, (n_set, _) in num.items()):
        return _degenerate_msg(plan, m, "100%", next(iter(num)))
    return None


def _degenerate_msg(plan: Plan, m, pct: str, key: str) -> str:
    part = ", ".join(f"{w.field} {w.op} {_show_value(w)}" for w in m.where)
    scope = ", ".join(f"{f.field} {f.op} {_show_value(f)}" for f in plan.filters) or "no filter"
    same = (" (in the data cancelled = true, payment_late = true and status «Аннулирован(Не оплачен)» are the same "
            "deals)") if key == "status" else ""
    return (f"the share is {pct} by construction: the scope ({scope}) already decides the numerator ({part}){same}. "
            "Keep only the conditions that define the whole (period, product, party, ...) as plain filters and put "
            "the part the question asks about in the numerator (numerator:true)")


# ---- share of a named party: the party is the part, the product / period is the scope ----
_PARTY_GROUNDS = ("company", "broker")
# «в своих продажах», «его покупок», «o‘z sotuvida», «ўз сотувида»: the party's own deals are the scope
_OWN_SCOPE_RE = re.compile(
    r"(?<!\w)(?:сво\w*|его|её|ее|их|o.z\w*|ўз\w*)\s+(?:\w+\s+)?(?:продаж|покуп|оборот|закуп|sotuv|xarid|savdo|"
    r"сотув|харид|савдо)", re.IGNORECASE)


def share_party_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    """«Доля Sitotom в продажах хлопка», «Sitotom paxta sotuvining necha foizini egallagan»: the named seller / buyer /
    broker is the numerator and the product / period the scope. A share plan that keeps the party as a plain filter
    while the numerator is something else computes the share inside the party's own deals (100% when it sells only
    that product)."""
    s = d.subject(plan.subject)
    shares = [m for m in plan.measures if m.agg == "share"]
    if not shares or _OWN_SCOPE_RE.search(question):
        return None
    parties = [f for f in plan.filters if f.field in s.fields and s.fields[f.field].ground in _PARTY_GROUNDS
               and f.op in ("=", "in")]
    if not parties:
        return None
    for m in shares:
        if any(w.field in s.fields and s.fields[w.field].ground in _PARTY_GROUNDS for w in m.where):
            continue
        p = parties[0]
        part = ", ".join(sorted({w.field for w in m.where})) or "nothing"
        return (f"the question asks for the share of the named {p.field} ({_show_value(p)}): that filter is the part — "
                f"mark it numerator:true and keep the product / period / other conditions as plain filters (the "
                f"scope). As written the numerator is {part} inside that {p.field}'s own deals, which is a different "
                "share (100% when it trades only that)")
    return None


# ---- a calendar day named in the question must be a day in the plan, not its month or year ----
_MONTH_STEMS = [r"январ|yanvar", r"феврал|fevral", r"март|mart", r"апрел|aprel", r"ма[йяе]|may", r"июн|iyun",
                r"июл|iyul", r"август|avgust", r"сентябр|sentyabr|sentabr", r"октябр|oktyabr|oktabr", r"ноябр|noyabr",
                r"декабр|dekabr"]
_MONTH_ALT = "|".join(f"(?P<m{i}>{rx})" for i, rx in enumerate(_MONTH_STEMS, 1))
# "1 января 2025", "1-yanvar 2025", "2025 йил 1 январь", "2025-yil 1-yanvar"
_DAY_MONTH_RE = re.compile(rf"(?<![\d.])(?P<d>[0-3]?\d)\s*-?\s*(?:{_MONTH_ALT})\w*", re.IGNORECASE)
_YEAR_AFTER_RE = re.compile(r"\s*,?\s*(20[12]\d)(?!\d)")                       # "1 января 2025"
_YEAR_BEFORE_RE = re.compile(r"(?<!\d)(20[12]\d)\s*-?\s*(?:йил\w*|yil\w*|г\.|год[уа]?)?\s*$", re.IGNORECASE)  # "2025 йил 1 январь"


def named_days(question: str) -> set:
    """Calendar days named in the question with a year: dd.mm.yyyy, ISO, or a day + month word + a year next to it."""
    import datetime as dt
    out = set()
    question = strip_times_word(question)
    for m in _DATE_RE.finditer(question):
        text = m.group(1)
        try:
            out.add(dt.date.fromisoformat(text) if "-" in text else
                    dt.date(*reversed([int(x) for x in text.split(".")])))
        except ValueError:
            pass
    for m in _DAY_MONTH_RE.finditer(question):
        month = next(i for i in range(1, 13) if m.group(f"m{i}"))
        after = _YEAR_AFTER_RE.match(question, m.end())
        before = _YEAR_BEFORE_RE.search(question[:m.start()])
        year = after.group(1) if after else before.group(1) if before else None
        if year:
            try:
                out.add(dt.date(int(year), month, int(m.group("d"))))
            except ValueError:
                pass
    return out


def named_day_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    s = d.subject(plan.subject)
    if not s.date_field:
        return None
    date_names = {n for n, f in s.fields.items() if f.type == "date"} | {"day"}
    in_plan = {p.date() for p in plan.compare if p.day is not None}
    for f in plan.filters:
        if f.field in date_names and f.op != "!=":
            for v in (f.value if isinstance(f.value, tuple) else (f.value,)):
                in_plan.add(v if not isinstance(v, str) else __import__("datetime").date.fromisoformat(v[:10]))
    for day in sorted(named_days(question)):
        if day not in in_plan:
            shown = day.strftime("%d.%m.%Y")
            if plan.compare:
                return (f"the question names the day {shown}: compare these days, not months — compare periods "
                        f"{{year, month_from, day}} (e.g. {{year: {day.year}, month_from: {day.month}, day: {day.day}}})")
            return (f"the question names the day {shown}: filter {s.date_field} on that date (op = / in / between "
                    "with it as a bound), not its month or year")
    return None


# ---- "по годам" / "yillar bo'yicha": the question asks for one row per period ----
_EACH = r"(?:har\s+(?:bir\s+)?|ҳар\s+(?:бир\s+)?|кажд\w+\s+)"
_SLICE = r"\s+(?:bo.yicha|kesimida|бўйича|кесимида)"
_GRAIN_WORDS = {
    "year": re.compile(r"по\s+годам|в\s+разрезе\s+(?:лет|годов)|погодов|ежегодн|за\s+каждый\s+год|"
                       rf"(?:yil|йил)(?:lar|лар)?(?:i|и)?{_SLICE}|{_EACH}(?:yil|йил|год)", re.IGNORECASE),
    "quarter": re.compile(r"по\s+кварталам|поквартальн|в\s+разрезе\s+кварталов|"
                          rf"(?:chora[kg]|чора[кг])(?:lar|лар)?(?:i|и)?{_SLICE}|{_EACH}(?:chora[kg]|чора[кг]|квартал)", re.IGNORECASE),
    "month": re.compile(r"по\s+месяцам|помесячн|в\s+разрезе\s+месяцев|oyma.oy|ойма.ой|"
                        rf"(?:oy|ой)(?:lar|лар)?(?:i|и)?{_SLICE}|{_EACH}(?:oy|ой|месяц)", re.IGNORECASE),
}


def grain_word_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    s = d.subject(plan.subject)
    if plan.compare or not s.date_field:
        return None
    for grain, rx in _GRAIN_WORDS.items():
        m = rx.search(question)
        if m and grain in s.fields and grain not in plan.group_by:
            if grain == "year" and "quarter" in plan.group_by or grain != "day" and "day" in plan.group_by:
                continue      # a finer grain also gives one row per period
            if grain == "year" and "month" in plan.group_by:
                continue
            return (f"the question asks per {grain} («{m.group(0)}»): add {grain} to group_by (one row per {grain}); "
                    "keep the period as a filter")
    return None


# ---- a time grain the question does not ask for; the extreme period vs the extreme single deal ----
_YEAR_PHRASE_RE = re.compile(r"(?<!\d)20[12]\d\s*-?\s*(?:год\w*|г\.|гг\.|yil\w*|йил\w*)", re.IGNORECASE)
_GRAIN_NOUNS = {
    "year": re.compile(r"(?<!\w)(?:год|года|году|годам|годах|годы|лет|ежегодн\w*|yil\w*|йил\w*)(?!\w)", re.IGNORECASE),
    "quarter": re.compile(r"квартал|chorak|чорак", re.IGNORECASE),
    "month": re.compile(r"месяц|помесячн|(?<!\w)(?:oy|ой)(?:i|и|da|да|lar|лар|ma|ма|ning|нинг|ida|ида)?\w*(?!\w)|"
                        r"oyma|ойма", re.IGNORECASE),
    "day": re.compile(r"(?<!\w)(?:день|дня|дни|дням|днях|дней|ежедневн\w*|сутк\w*|числ\w*|дат\w*|kun\w*|кун\w*|"
                      r"sana\w*|сана\w*)(?!\w)", re.IGNORECASE),
    "hour": re.compile(r"(?<!\w)(?:час\w*|soat\w*|соат\w*|время\s+суток|vaqt\w*|вақт\w*)(?!\w)", re.IGNORECASE),
}
# "when", "which period", trends: the question asks for one row per period or for the period itself
_PERIOD_ASK_RE = re.compile(
    r"когда|в\s+как\w+\s+(?:период|время)|динамик|тренд|как\s+(?:менял|изменял|рос|рост|снижал|падал)|"
    r"по\s+периодам|qachon|қачон|dinamika|динамика|qanday\s+o.zgar|қандай\s+ўзгар|davr(?:lar)?\s+bo.yicha|"
    r"давр(?:лар)?\s+бўйича", re.IGNORECASE)
_TIME_GRAINS = ("year", "quarter", "month", "day", "hour")


def _grain_asked(grain: str, question: str) -> bool:
    if _PERIOD_ASK_RE.search(question) or (grain in _GRAIN_WORDS and _GRAIN_WORDS[grain].search(question)):
        return True
    q = _YEAR_PHRASE_RE.sub(" ", question) if grain == "year" else question
    if _GRAIN_NOUNS[grain].search(q):
        return True
    if grain == "year" and len(set(_YEAR_RE.findall(question))) >= 2:
        return True          # «2024 и 2025», «2022–2026»: the years listed
    if grain == "month" and len(named_months(question)) >= 2:
        return True
    return grain == "day" and len(named_days(question)) >= 2


def unasked_grain_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    """A time grain in group_by needs a question that asks per period, which period or when (or names several
    periods): «харидорлар сони нечта» split by month answers another question."""
    s = d.subject(plan.subject)
    if plan.compare or not s.date_field or not question.strip():
        return None
    for g in plan.group_by:
        if g in _TIME_GRAINS and s.fields[g].type == "grain" and not _grain_asked(g, question):
            return (f"the question does not ask per {g} (no per-period, which-{g} or when words): drop {g} from "
                    "group_by and keep the period as a filter")
    return None


_SINGLE_DEAL_RE = re.compile(r"сделк|контракт|договор|лот(?!\w)|лота|bitim|битим|shartnoma|шартнома|(?<!\w)lot",
                             re.IGNORECASE)


def extreme_period_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    """«В какой день был самый большой оборот» / «қайси кунида энг катта савдо»: the period with the largest total,
    not the period of the largest single deal, unless the question speaks of a deal."""
    s = d.subject(plan.subject)
    if not any(g in _TIME_GRAINS for g in plan.group_by) or _SINGLE_DEAL_RE.search(question):
        return None
    for m in plan.measures:
        f = s.fields.get(m.of or "")
        if m.agg in ("max", "min") and f is not None and f.type == "number" and "sum" in f.aggs:
            return (f"the question asks for the period with the {'largest' if m.agg == 'max' else 'smallest'} total, "
                    f"not the single largest deal: rank by {{agg: sum, of: {m.of}}} (or count) per period instead "
                    f"of {m.agg}")
    return None


# ---- an id is used as the kind of record the question attaches it to (deal vs contract) ----
_ID_KIND_WORDS = {"deal": re.compile(r"^(?:сделк\w*|bitim\w*|битим\w*|deal\w*)$", re.IGNORECASE),
                  "contract": re.compile(r"^(?:контракт\w*|договор\w*|shartnoma\w*|шартнома\w*|contract\w*)$",
                                         re.IGNORECASE)}
_ID_FIELDS = {"deal": "deal", "contract": "contract"}


def id_kind_of(question: str, ident: str) -> str | None:
    """deal | contract: the record word nearest to the id in the question (up to 3 words before, 2 after)."""
    toks = re.findall(r"[^\W_]+(?:[‘’'ʻ`][^\W_]+)*", question)
    out = None
    for i, t in enumerate(toks):
        if t != ident:
            continue
        near = [(abs(j - i), k) for j in range(max(0, i - 3), min(len(toks), i + 3)) if j != i
                for k, rx in _ID_KIND_WORDS.items() if rx.match(toks[j])]
        if near:
            out = min(near)[1]
    return out


def id_kind_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    """«100091-сонли битимнинг харидори»: 100091 is a deal; filtering the contract 100091 answers about another
    record. A missing deal is no data, never the contract with the same number."""
    s = d.subject(plan.subject)
    for f in _plan_filters(plan):
        fd = s.fields.get(f.field)
        if fd is None or fd.ground not in _ID_FIELDS:
            continue
        for v in (f.value if isinstance(f.value, tuple) else (f.value,)):
            said = id_kind_of(question, str(v))
            if said and said != fd.ground:
                where = "subject deals, filter deal" if said == "deal" else "filter contract (subject contracts or deals)"
                return (f"the question calls {v} a {said}, but the plan uses it as a {fd.ground}: use it as a {said} "
                        f"({where} = {v}); if no such {said} exists, call declare_unanswerable — never answer about "
                        f"the {fd.ground} with the same number")
    return None


# ---- the data has no such facts: answering with a nearby number would answer another question ----
_NOT_IN_DATA = {
    "employees": r"сотрудник|работник|численност\w*\s+персонал|персонал[аеу]?\b|xodim|ходим|ishchilar|ишчилар",
    "profit": r"прибыл|рентабельн|маржинальн|foyda(?!lan)|фойда(?!лан)",
    "salary": r"зарплат|заработн|maosh|маош|ish\s+haqi|иш\s+ҳақи",
    "contacts": r"телефон|e-?mail|электронн\w*\s+почт|контакт|telefon|aloqa\s+ma.lumot",
    "management": r"директор|руководител|владел|учредител|собственник|rahbar|раҳбар|direktor|egasi|эгаси|ta.sischi",
    "rating": r"рейтинг|reyting|кредитоспособн",
    "taxes": r"налог|soliq|солиқ",
    "forecast": r"прогноз|спрогноз|будет\s+стоить|bashorat|башорат",
    # only the US dollar rate is in the data
    "other_currencies": r"евро|euro|yevro|рубл|rubl|юан|yuan|тенге|tenge|фунт\w*\s+стерлинг|йен[аы]?\b|"
                        r"иен[аы]?\b|франк|frank|вон[аы]?\s+курс|сом\w*\s+кыргыз",
    # the exchange's own trades only: no country-wide harvest, production, consumption or other exchanges
    "outside_exchange": r"урожа|hosil|ҳосил|yetishtiril|етиштирил|производств\w*\s+в\s+стран|"
                            r"в\s+(?:целом\s+по\s+)?стран[еы]|мамлакатда|mamlakatda|другой\s+бирж|других\s+бирж|"
                            r"boshqa\s+birja|бошқа\s+биржа|экспорт\w*\s+страны|импорт|"
                            # trades off the exchange: «вне биржи», «помимо биржи», «birjadan tashqari», ...
                            r"(?:вне|помимо|кроме|мимо)\s+бирж|за\s+пределами\s+бирж|без\s+участия\s+бирж|внебирж|"
                            r"биржев\w*\s+и\s+внебирж|напрямую|прямы\w*\s+(?:договор|контракт|продаж|поставк)|"
                            r"birja(?:dan)?\s+tashqari|birjadan\s+boshqa\b|birjasiz|birjadan\s+o.tmagan|"
                            r"биржа(?:дан)?\s+ташқари|биржадан\s+бошқа(?!\w)|биржасиз|биржадан\s+ўтмаган|"
                            r"boshqa\s+savdo\s+(?:maydon|platforma|kanal|yo.l)|бошқа\s+савдо\s+(?:майдон|платформа|канал|йўл)|"
                            r"друг\w+\s+(?:торгов\w+\s+)?(?:площадк|канал\w*\s+продаж)|"
                            r"off-?exchange|\botc\b",
}
_NOT_IN_DATA_RE = re.compile("|".join(f"(?P<{k}>{v})" for k, v in _NOT_IN_DATA.items()), re.IGNORECASE)


def not_in_data_error(question: str) -> str | None:
    m = _NOT_IN_DATA_RE.search(question)
    if m is None:
        return None
    return (f"the question asks about {m.lastgroup.replace('_', ' ')} («{m.group(0)}»), which the data does not contain (only deals, "
            "contracts, participants, brokers and USD rates): call declare_unanswerable instead of answering a "
            "nearby number")


# ---- companies: "buyers" / "sellers" is the role, not all companies ----
_ROLE_WORDS = {"Покупатель": re.compile(r"покупател|xaridor|харидор", re.IGNORECASE),
               "Продавец": re.compile(r"продав|sotuvchi|сотувчи", re.IGNORECASE)}


def role_word_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    s = d.subject(plan.subject)
    if "role" not in s.fields:
        return None
    if any(f.field == "role" for f in plan.filters) or "role" in plan.group_by:
        return None
    named = [v for v, rx in _ROLE_WORDS.items() if rx.search(question)]
    if not named:
        return None
    if len(named) == 1:
        return (f"the question is about {named[0]} companies: add the filter role = {named[0]} (the registry lists "
                "buyers and sellers)")
    return "the question names buyers and sellers: group_by role, or filter role for the one asked"


# ---- concept identity: a currency / side the question names must be the one the plan computes ----
_DOLLAR_RE = re.compile(r"доллар|долл\b|dollar|usd|\$", re.IGNORECASE)
_SOM_RE = re.compile(r"(?<!\w)(?:сум(?:ах|ов|ам|ы|е|а)?|so[‘'’`ʻ]?m\w*|сўм\w*|uzs)(?!\w)", re.IGNORECASE)
_RATE_RE = re.compile(r"курс|kurs", re.IGNORECASE)
_UZS_MONEY = {"amount_uzs", "deal_price_per_kg", "unit_price_uzs", "price_per_kg", "bought_uzs_valid",
              "sold_uzs_valid"}
_USD_MONEY = {"amount_usd"}


def _plan_field_names(plan: Plan) -> set[str]:
    """Fields a plan computes or filters on: measure fields and named measures, filters (incl. share numerators),
    group_by."""
    out = {m.of for m in plan.measures if m.of} | {m.agg for m in plan.measures}
    out |= {f.field for f in _plan_filters(plan)} | set(plan.group_by)
    return out


def currency_error(plan: Plan, question: str) -> str | None:
    """The dollar rate only for a question that names the dollar or a bare rate («курс на 15.03.2024»: the data's
    only rate); money in the currency the question names. (Other currencies by name: not_in_data_error.)"""
    if not question.strip():
        return None
    dollar, som = bool(_DOLLAR_RE.search(question)), bool(_SOM_RE.search(question))
    if plan.subject == "usd_rates":
        if not dollar and not _RATE_RE.search(question):
            return ("usd_rates is the US dollar rate only and the question names neither the dollar nor a rate: "
                    "if it asks about something else, call declare_unanswerable")
        return None
    used = _plan_field_names(plan)
    usd_filter = any(f.field == "currency" and f.value in ("USD", ("USD",)) for f in _plan_filters(plan))
    uzs_filter = any(f.field == "currency" and f.value in ("UZS", ("UZS",)) for f in _plan_filters(plan))
    if dollar and not som and used & _UZS_MONEY and not used & _USD_MONEY and not usd_filter:
        return ("the question asks for an amount in US dollars but the plan computes so‘m "
                f"({', '.join(sorted(used & _UZS_MONEY))}): use amount_usd; if the dollar figure is not in the "
                "data, call declare_unanswerable")
    if som and not dollar and used & _USD_MONEY and not used & _UZS_MONEY and not uzs_filter:
        return "the question asks for an amount in so‘m but the plan computes US dollars: use amount_uzs"
    return None


_BUYER_RE = re.compile(r"покупател|xaridor|харидор", re.IGNORECASE)
_SELLER_RE = re.compile(r"продав(?:ец|ц)|sotuvchi|сотувчи", re.IGNORECASE)
_SIDE_FIELDS = {
    "buyer": {"buyer", "buyer_broker", "buyer_region", "buyer_activity", "deals_as_buyer", "deals_as_buyer_broker",
              "bought_uzs_valid"},
    "seller": {"seller", "seller_broker", "seller_region", "seller_activity", "deals_as_seller",
               "deals_as_seller_broker", "sold_uzs_valid"},
}
_SIDE_VALUES = {"buyer": {"buyer", "Покупатель"}, "seller": {"seller", "Продавец"}}


def _plan_sides(plan: Plan) -> set[str]:
    used = _plan_field_names(plan)
    sides = {side for side, names in _SIDE_FIELDS.items() if used & names}
    for f in _plan_filters(plan):
        if f.field in ("broker_side", "role"):
            values = set(f.value) if isinstance(f.value, tuple) else {f.value}
            sides |= {side for side, names in _SIDE_VALUES.items() if values & names}
    return sides


def side_error(plan: Plan, question: str) -> str | None:
    """A question that names only buyers (or only sellers) must not be computed on the other side alone."""
    named = {side for side, rx in (("buyer", _BUYER_RE), ("seller", _SELLER_RE)) if rx.search(question)}
    if len(named) != 1:
        return None
    side = named.pop()
    sides = _plan_sides(plan)
    if not sides or side in sides:
        return None
    other = sides.pop()
    return (f"the question is about the {side} side but the plan uses only {other}-side fields: use the {side} "
            f"fields ({', '.join(sorted(_SIDE_FIELDS[side])[:4])}, ...)")


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
    question = strip_times_word(question)
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
_AVG_PRICE_RE = re.compile(r"средн\w*\s+цен|o.rtacha\s+narx|ўртача\s+нарх", re.IGNORECASE)
_ONE_DEAL_RE = re.compile(r"отдельн|каждой сделк|по сделкам|медиан|взвешен|bitim\s+bo.yicha|har\s+bir", re.IGNORECASE)
_WEIGHTED_RE = re.compile(r"взвешен|средневзвеш|tortilgan|тортилган|weighted", re.IGNORECASE)
# "without annulled deals": the scope of the question, never the part a share measures
_WITHOUT_CANCELLED_RE = re.compile(
    r"без\s+аннулир|аннулированн\w*\s+не\s+(?:учит|счит)|не\s+считая\s+аннулир|кроме\s+аннулир|"
    r"bekor\s+qilingan\w*siz|bekor\s+qilinganlar\s+hisobga\s+olinma|бекор\s+қилинган\w*сиз|"
    r"бекор\s+қилинганлар\s+ҳисобга\s+олинма", re.IGNORECASE)
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
    aggs = {(m.agg, m.of) for m in plan.measures}
    if _WEIGHTED_RE.search(q) and ("price_per_kg", None) not in aggs and any(
            of == "deal_price_per_kg" for _, of in aggs):
        return ("the question asks for the weighted average price (total amount ÷ total volume): use {agg: "
                "price_per_kg}, not the mean of single deal prices")
    if _AVG_PRICE_RE.search(q) and not _ONE_DEAL_RE.search(q) and ("avg", "deal_price_per_kg") in aggs \
            and ("price_per_kg", None) not in aggs:
        return ("the question asks for the average price without saying how: use both measures price_per_kg "
                "(weighted by volume) and {agg: avg, of: deal_price_per_kg} (mean of single deals)")
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
    # a stored percentage (deposit %, price change %) read as a filter or in a record's listed columns is not a share
    sub = d.subject(plan.subject)
    percent = percent or any(f.field.endswith("_pct") for f in plan.filters) or any(
        sub.fields[g].type == "record" and any("_pct" in c for c in (getattr(sub.fields[g], "columns", None) or ()))
        for g in plan.group_by)
    if not (percent or plan.compare):
        return ("the question asks for a share / percentage: use {agg: share} with the part's filters marked "
                "numerator:true (or cancelled_share); if the share cannot be expressed, call declare_unanswerable")
    return None


# ---- "most buyers" / "eng ko'p xaridor": parties are counted once each, deals are not parties ----
_PARTY_COUNT_RE = re.compile(
    r"(?:сколько|больше\s+всего|меньше\s+всего|наибольшее\s+число|число|количество|eng\s+ko.p|eng\s+kam|nechta|"
    r"qancha|энг\s+кўп|энг\s+кам|нечта|қанча)\s+"
    r"(?P<party>покупател\w*|продав\w*|компани\w*|участник\w*|клиент\w*|xaridor\w*|sotuvchi\w*|kompaniya\w*|"
    r"mijoz\w*|харидор\w*|сотувчи\w*|мижоз\w*)", re.IGNORECASE)


def _party_of(word: str) -> str:
    w = word.lower()
    if w.startswith(("покупател", "xaridor", "харидор", "клиент", "mijoz", "мижоз")):
        return "buyer"
    if w.startswith(("продав", "sotuvchi", "сотувчи")):
        return "seller"
    return "participant"


def counted_parties_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    """On deals every row is a deal: "how many / most buyers" needs {agg: distinct, of: buyer}, not a deal count."""
    m = _PARTY_COUNT_RE.search(question)
    if m is None or plan.subject != "deals":
        return None
    party = _party_of(m.group("party"))
    if party in plan.group_by or any(x.agg == "distinct" for x in plan.measures):
        return None   # grouping by the party ranks the parties (a count per party is right); distinct is right
    if any(x.agg == "count" for x in plan.measures):
        of = party if party != "participant" else "buyer or seller"
        return (f"the question counts {party}s («{m.group(0)}»), not deals: use {{agg: distinct, of: {of}}} "
                f"(each {party} once)")
    return None


# ---- elapsed days vs contract term ----
_TERM_RE = re.compile(r"срок|muddat|муддат|по\s+контракту|shartnoma\s+bo.yicha|шартнома\s+бўйича|договорн",
                      re.IGNORECASE)
_ELAPSED_RE = re.compile(r"после\s+оплат|от\s+оплат|занима\w+|фактическ|на\s+деле|"
                         r"to.lovdan\s+keyin|to.lovdan\s+so.ng|haqiqatda|amalda|oladi|тўловдан\s+кейин|"
                         r"тўловдан\s+сўнг|ҳақиқатда|амалда|олади", re.IGNORECASE)
_TERM_TO_ELAPSED = {"delivery_term_days": "days_to_delivery", "payment_term_days": "days_to_payment"}


def elapsed_vs_term_error(plan: Plan, d: Dictionary, question: str) -> str | None:
    """"How many days delivery takes after payment" is the actual time (days_to_delivery), not the contract term
    (delivery_term_days); the term is used only when the question says term / contract."""
    if _TERM_RE.search(question) or not _ELAPSED_RE.search(question):
        return None
    s = d.subject(plan.subject)
    for x in plan.measures:
        actual = _TERM_TO_ELAPSED.get(x.of or "")
        if actual and actual in s.fields:
            return (f"the question asks how many days it actually took, not the contract term: use "
                    f"{{agg: {x.agg}, of: {actual}}} instead of {x.of}")
    return None
