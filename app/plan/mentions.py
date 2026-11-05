"""Mention check of a plan, after "Mention Extraction and Linking for SQL Query Generation" (EMNLP 2020).

The planner copies the phrases of the question it used into plan.mentions: [{text, used_as}], where used_as names
the plan element the phrase sets (filter:<field>, group_by:<field>, measure:<i>, order, limit, subject, compare).
Code then checks, language-independently:
  a) each mention occurs in the question (normalize_text: case, whitespace, punctuation, Cyrillic/Latin script);
  b) each used_as points to an element that exists in the plan;
  c) every filter value and every group_by of the plan is linked to a mention (a resolved value through the
     resolve() call of the mentioned phrase; a period filter through a date/year phrase), and the numbers of a
     mention agree with the element it sets (years of a period phrase, the limit of "топ-3", a threshold);
  d) every condition-like phrase of the question — numbers, years, dates, ids, catalog values — is in a mention.
Any failure is a fix instruction for the planner, like the other plan checks.
"""
import re
from dataclasses import dataclass

from app.agent.guard import _tok_match, mentioned_spans
from app.plan.checks import ALL_STATUSES_RE, years_covered
from app.plan.dictionary import Dictionary, Subject
from app.plan.model import Filter, Mention, Plan
from app.plan.validate import GROUND_KIND
from app.text import normalize_text

TARGETS = ("filter", "group_by", "measure", "order", "limit", "subject", "compare")
_ID_RE = re.compile(r"\d{9}|bk\d{3}|\d{6}")
_YEAR_RE = re.compile(r"20\d\d")
_NUM_RE = re.compile(r"\d+(?:[  ]\d{3})*(?:[.,]\d+)?")
# catalog fields of the guard index -> plan fields that express them
_CATALOG_FIELDS = {"activity": ("buyer_activity", "seller_activity", "activity"), "status": ("status", "cancelled")}
_PRODUCT_GROUNDS = ("product", "product_family", "product_variant")


@dataclass(frozen=True)
class Resolution:
    kind: str                   # resolve() kind
    text: str                   # the phrase passed to resolve()
    values: frozenset[str]      # ids / values it returned


def _words(text: str) -> tuple[list[str], list[str]]:
    """normalize_text tokens of text and, per token, the original word it comes from."""
    toks, origin = [], []
    for word in text.split():
        for t in normalize_text(word).split():
            toks.append(t)
            origin.append(word.strip("«»\"“”'.,;:?!()"))
    return toks, origin


def _find(sub: list[str], toks: list[str]) -> list[tuple[int, int]]:
    k = len(sub)
    return [(i, i + k) for i in range(len(toks) - k + 1) if toks[i:i + k] == sub]


def _overlap(a: str, b: str) -> bool:
    """Two phrases share a word (inflection-tolerant)."""
    ta, tb = normalize_text(a).split(), normalize_text(b).split()
    return any(_tok_match(x, y, False) or _tok_match(y, x, False) or _stem(x, y) for x in ta for y in tb)


def _stem(x: str, y: str) -> bool:
    """Same word in another case form (хлопок / хлопка, pshenitsa / pshenitsi): a common prefix of at least 4
    letters covering 60% of the shorter word."""
    if not (x.isalpha() and y.isalpha()) or min(len(x), len(y)) < 4:
        return False
    n = 0
    for cx, cy in zip(x, y):
        if cx != cy:
            break
        n += 1
    return n >= max(4, -(-3 * min(len(x), len(y)) // 5))


def _period_fields(s: Subject) -> set[str]:
    return {n for n, f in s.fields.items() if f.type == "date" or n in ("year", "quarter", "month", "day")}


def _num(text: str) -> float:
    return float(text.replace(" ", "").replace(" ", "").replace(",", "."))


def _values(flt: Filter) -> tuple:
    return flt.value if isinstance(flt.value, tuple) else (flt.value,)


def _targets(plan: Plan, raw_filters: set[str]) -> list[str]:
    fields = sorted({f.field for f in plan.filters} | {w.field for m in plan.measures for w in m.where} | raw_filters)
    out = [f"filter:{f}" for f in fields] + [f"group_by:{g}" for g in plan.group_by]
    out += [f"measure:{i}" for i in range(len(plan.measures))] + ["subject"]
    if plan.group_by:
        out.append("order")
    if plan.limit is not None:
        out.append("limit")
    if plan.compare:
        out.append("compare")
    return out


def record_columns(s: Subject, plan: Plan) -> set[str]:
    """Output columns of a record listing (group_by a record, no measures), and the fields shown in them."""
    out: set[str] = set()
    if plan.measures:
        return out
    for g in plan.group_by:
        f = s.fields.get(g)
        if f is not None and f.type == "record":
            out |= {c.rsplit(" AS ", 1)[-1].strip() for c in f.columns}
    if out:
        out |= {n for n, f in s.fields.items() if f.col in out or (f.display is not None and f.display.col in out)}
    return out


def _target_ok(used: str, plan: Plan, raw_filters: set[str], listed: set[str] = frozenset()) -> bool:
    kind, _, arg = used.partition(":")
    if listed and kind in ("group_by", "measure") and (arg in listed or (kind == "measure" and
                                                                                    (arg.isdigit() or not arg))):
        return True     # a field shown by the record listing («кто купил», «ИНН», «на какую сумму»)
    if kind == "measure":
        if arg.isdigit():
            return int(arg) < len(plan.measures)
        return any(arg in (m.agg, m.of) for m in plan.measures)
    if kind == "subject":
        return arg in ("", plan.subject)
    if kind in ("order", "limit", "compare") and arg:
        return False
    return used in _targets(plan, raw_filters)


def _fix(text: str) -> str:
    return text + "; the plan itself may be right: fix only the mentions and call run_plan again"


MAX_PROBLEMS = 5


def mention_error(plan: Plan, d: Dictionary, question: str, index, resolutions=(), names=(),
                  raw_filters: frozenset[str] = frozenset()) -> str | None:
    """None, or one fix instruction listing every mention problem (so the planner fixes them in one round).
    plan: validated plan after defaults; raw_filters: filter fields of the plan as the planner sent it (defaults
    may drop a status filter that a mention points to)."""
    problems = list(dict.fromkeys(_problems(_relink(plan, raw_filters), d, question, index, resolutions, names,
                                            raw_filters)))
    if not problems:
        return None
    if len(problems) == 1:
        return _fix(problems[0])
    shown = problems[:MAX_PROBLEMS]
    return _fix("mentions: " + " ".join(f"{i}) {p}." for i, p in enumerate(shown, 1)))


_PERIOD_NAMES = {"year", "quarter", "month", "day", "deal_date", "registered_date", "rate_date"}
_USED_ALIASES = {"filters": "filter", "measures": "measure", "groupby": "group_by", "group": "group_by",
                 "grouping": "group_by", "limits": "limit", "orders": "order", "sort": "order"}


def _relink(plan: Plan, raw_filters) -> Plan:
    """Tolerant reading of used_as: plural / alias spellings, and filter:<f> vs group_by:<f> when the plan has the
    field in the other role only."""
    filtered = {f.field for f in plan.filters} | {w.field for m in plan.measures for w in m.where} | set(raw_filters)
    out = []
    for m in plan.mentions:
        kind, sep, arg = m.used_as.partition(":")
        kind = _USED_ALIASES.get(kind, kind)
        if plan.compare and kind in ("filter", "group_by") and arg in _PERIOD_NAMES and arg not in filtered:
            kind, sep, arg = "compare", "", ""          # a period phrase of a compare plan sets the compare
        elif kind == "filter" and arg and arg not in filtered and arg in plan.group_by:
            kind = "group_by"
        elif kind == "group_by" and arg and arg not in plan.group_by and arg in filtered:
            kind = "filter"
        out.append(Mention(m.text, f"{kind}{sep}{arg}"))
    return plan.with_(mentions=tuple(out))


def _problems(plan: Plan, d: Dictionary, question: str, index, resolutions, names, raw_filters):
    s = d.subject(plan.subject)
    q, origin = _words(question)
    periods = _period_fields(s)
    spans: list[tuple[int, int, Mention]] = []

    # a) each mention is a phrase of the question
    for m in plan.mentions:
        mt = normalize_text(m.text).split()
        found = _find(mt, q) if mt else []
        if not found:
            yield (f"mention «{m.text}» is not in the question: copy the phrase exactly as the question writes it")
        spans += [(a, b, m) for a, b in found]
    # b) each mention points to an element of the plan
    listed = record_columns(s, plan)
    for m in plan.mentions:
        if m.used_as in ("filter:status", "filter:cancelled") and ALL_STATUSES_RE.search(m.text):
            continue        # «все статусы»: the status view (every state), not a filter
        if m.used_as.partition(":")[0] not in TARGETS or not _target_ok(m.used_as, plan, raw_filters, listed):
            yield (f"mention «{m.text}» used_as {m.used_as!r} points to nothing in the plan; this plan has: "
                        f"{', '.join(_targets(plan, raw_filters))}")

    def used(prefix_or_name: str) -> list[Mention]:
        return [m for m in plan.mentions if m.used_as == prefix_or_name]

    period_mentions = [m for m in plan.mentions if m.used_as == "compare"
                       or (m.used_as.startswith("filter:") and m.used_as[7:] in periods)]
    by_resolve: dict[str, list[Resolution]] = {}
    for r in resolutions:
        for v in r.values:
            by_resolve.setdefault(str(v), []).append(r)

    def linked(flt: Filter, value, ms: list[Mention]) -> bool:
        f = s.fields[flt.field]
        if GROUND_KIND.get(f.ground) is None:
            return True
        vt = normalize_text(str(value))
        for m in ms:
            mt = normalize_text(m.text)
            if vt and (f" {vt} " in f" {mt} " or _overlap(str(value), m.text)):
                return True
            if any(_overlap(r.text, m.text) for r in by_resolve.get(str(value), ())):
                return True
        return False

    # c) every filter and group_by is linked to a mention
    share_filters = [(i, w) for i, m in enumerate(plan.measures) for w in m.where]
    for i, flt in [(None, f) for f in plan.filters] + share_filters:
        if flt.field in periods:
            if not period_mentions:
                yield (f"the {flt.field} filter is not linked to the question: add a mention "
                       f"{{text: <the date/year phrase>, used_as: filter:{flt.field}}}")
            continue
        ms = used(f"filter:{flt.field}") + (used(f"measure:{i}") if i is not None else [])
        if not ms:
            yield (f"filter {flt.field} = {_show(flt)} is not linked to the question: add a mention "
                        f"{{text: <the question phrase>, used_as: filter:{flt.field}}} (a phrase may be listed again with "
                        "another used_as), or drop the filter if the question does not ask for it")
            continue
        for v in _values(flt):
            if not linked(flt, v, ms):
                yield (f"filter {flt.field} = {v} is not linked to the mention "
                            f"«{'», «'.join(m.text for m in ms)}»: resolve() that phrase and use what it returns, or "
                            "point the mention at the phrase that names this value")
    for g in plan.group_by:
        if not used(f"group_by:{g}"):
            yield (f"group_by {g} is not linked to the question: add a mention {{text: <the phrase that asks "
                        f"for it>, used_as: group_by:{g}}} (a phrase may be listed again with another used_as), or "
                        "drop it from group_by")

    # c) numbers of a mention agree with the element it sets
    covered = years_covered(plan, s)
    for m in period_mentions:
        for y in (int(x) for x in _YEAR_RE.findall(normalize_text(m.text))):
            if covered is None or not covered(y):
                where = "compare" if m.used_as == "compare" else "the period filters"
                yield (f"mention «{m.text}» names the year {y} but {where} of the plan do not include {y}: "
                            "set the period exactly as the phrase says (a range is between [from, to])")
    for m in plan.mentions:
        nums = [_num(x) for x in _NUM_RE.findall(m.text)]
        if m.used_as == "limit" and nums and plan.limit not in nums:
            yield (f"mention «{m.text}» asks for {int(nums[0]) if nums[0].is_integer() else nums[0]} rows but "
                        f"limit is {plan.limit}: set limit to that number")
        name = m.used_as[7:] if m.used_as.startswith("filter:") else ""
        if name and name in s.fields and s.fields[name].type == "number" and nums:
            vals = {float(v) for f in plan.filters if f.field == name for v in _values(f)}
            missing = [n for n in nums if n not in vals]
            if missing:
                yield (f"mention «{m.text}» names {missing[0]:g} but the {name} filter is "
                            f"{sorted(vals) or 'missing'}: use the number of the question")

    # d) condition-like phrases of the question are in some mention
    def covering(a: int, b: int) -> list[Mention]:
        return [m for a2, b2, m in spans if a2 < b and a < b2]

    for i, t in enumerate(q):
        if not any(ch.isdigit() for ch in t):
            continue
        ms = covering(i, i + 1)
        word = origin[i]
        if _ID_RE.fullmatch(t):
            if not any(m.used_as.startswith("filter:") for m in ms):
                yield (f"the question names the id «{word}» but no mention uses it: add a mention "
                            f"{{text: «{word}», used_as: filter:<field>}} and filter it")
        elif _YEAR_RE.fullmatch(t):
            ok = [m for m in ms if m in period_mentions or (m.used_as.startswith("filter:")
                                                            and s.fields.get(m.used_as[7:]) is not None
                                                            and GROUND_KIND.get(s.fields[m.used_as[7:]].ground))]
            if not ok:
                yield (f"the question names the year {t} («{word}») but no mention uses it: add a mention "
                            f"{{text: <the year phrase>, used_as: filter:year}} (or compare) and set the period")
        elif not ms:
            yield (f"the question phrase «{word}» is not covered by any mention: add it to mentions with the "
                        "plan element it sets (filter:<field>, limit, ...)")
    for a, b, v in mentioned_spans(question, index, names):
        phrase = " ".join(dict.fromkeys(origin[a:b]))
        ms = covering(a, b)
        if v.field == "unit":
            if not ms:
                yield (f"the question phrase «{phrase}» (unit {v.value}) is not covered by any mention: add it "
                            "with the plan element it sets (a measure, or filter:unit)")
            continue
        fields = _CATALOG_FIELDS.get(v.field, (v.field,))
        fields = tuple(n for n in fields if n in s.fields)
        good = []
        for m in ms:
            kind, _, arg = m.used_as.partition(":")
            if kind in ("filter", "group_by") and (arg in fields or (arg in s.fields
                                                                      and s.fields[arg].ground in _PRODUCT_GROUNDS)):
                good.append(m)
            elif kind == "measure" and v.field == "status":    # cancelled_share, late_payments, ...
                good.append(m)
        if not good:
            where = ", ".join(f"filter:{n}" for n in fields) or f"a field of another subject ({v.field})"
            yield (f"the question phrase «{phrase}» names the {v.field} value «{v.value}», but no mention "
                        f"links it to a filter: filter it ({where}) with a mention {{text: «{phrase}», used_as: "
                        f"filter:<field>}}")


def _show(flt: Filter) -> str:
    v = flt.value
    return "[" + ", ".join(map(str, v)) + "]" if isinstance(v, tuple) else str(v)
