"""Missing-condition guard: reject a spec that silently drops a condition named in the question.

When the form cannot express a condition, the planner tends to drop it and answer a different question with real
numbers ("how many transport companies" -> every company). This guard notices known catalog values (categories,
activities, contract forms, ...) and a few wording cues (least / distinct / share) in the question and returns a
fix message when the spec does not use them. Matching is on normalize_text tokens with a short-ending tolerance
for Russian/Uzbek inflection ("форвардных" ~ "Форвард").
"""
import re
from dataclasses import dataclass, fields

from app.compiler.spec import (CONTRACT_FORMS, DISTINCT_MEASURES, TIME_DIMENSIONS, DealsAggregate, RegistryCount,
                               Spec)
from app.text import normalize_name, normalize_text

# catalog field -> (spec keys that constrain it, dimensions that split by it)
FIELD_TARGETS = {
    "category": (("categories",), ("category",)),
    "industry": (("industries",), ("industry",)),
    "product_group": (("product_groups",), ("product_group",)),
    "delivery_basis": (("delivery_bases",), ("delivery_basis",)),
    "packaging": (("packagings",), ("packaging",)),
    "activity": (("buyer_activities", "seller_activities", "activity"), ("buyer_activity", "seller_activity")),
    "status": (("statuses",), ("status",)),
    "contract_form": (("contract_form",), ("contract_form",)),
    "contract_type": (("contract_type",), ("contract_type",)),
    "trade_type": (("trade_type",), ("trade_type",)),
    "unit": (("units",), ("unit",)),
}
# Values that are also ordinary words in questions ("сделок заключено", "новых", "производитель цемента").
_GENERIC = {("status", "Новый"), ("status", "Заключен"), ("status", "Оплачен"), ("activity", "Производитель")}
# Status names are past participles of everyday verbs: exact tokens only. Unit words only take endings
# ("литр" ~ "литрах").
_EXACT_FIELDS = {"status"}
_SUFFIX_FIELDS = {"unit"}
# Measures whose output is per unit of measure / per kg: a unit word in the question names the output unit.
_UNIT_MEASURES = {"volume", "volume_tonnes", "delivered_volume", "undelivered_volume", "min_unit_price_uzs",
                  "max_unit_price_uzs", "avg_price_uzs_per_kg", "mean_deal_price_uzs_per_t",
                  "median_price_uzs_per_kg", "lots_bought", "lots_delivered", "undelivered_lots"}

_LEAST_RE = re.compile(r"наименьш|меньше всего|дешевле всего|сам(?:ый|ая|ое|ую|ой|ых|ые|ом)\s+деш[её]в|минимальн"
                       r"|eng\s+kam|eng\s+arzon|eng\s+past", re.IGNORECASE)
_DISTINCT_RE = re.compile(r"сколько\s+(?:\w+\s+)?(?:разных|различных|уникальных)|nechta\s+(?:turli|xil)"
                          r"|nechta\s+\w+\s+xil", re.IGNORECASE)
_SHARE_RE = re.compile(r"(?<!\w)(?:дол[яиюей]|процент\w*|foiz\w*|ulush\w*|фоиз\w*|улуш\w*)(?!\w)|%",
                       re.IGNORECASE)
_PERCENT_MEASURES = {"share_pct", "cancelled_pct", "avg_price_change_pct"}
_NEGATION_RE = re.compile(r"(?<!\w)(?:кроме|без|не|bundan\s+tashqari|tashqari|emas|siz)(?!\w)", re.IGNORECASE)
_EACH_RE = re.compile(r"по\s+кажд\w*|по\s+всем|har\s+bir", re.IGNORECASE)


@dataclass(frozen=True)
class _Value:
    field: str
    value: str
    tokens: tuple[str, ...]


def build_index(catalog: dict[str, set[str]]) -> list[_Value]:
    out = []
    for fld in FIELD_TARGETS:
        for value in sorted(catalog.get(fld, ())):
            if (fld, value) in _GENERIC:
                continue
            toks = tuple(normalize_text(value).split())
            if toks:
                out.append(_Value(fld, value, toks))
    return out


def _tok_match(g: str, w: str, exact: bool, suffix_only: bool = False) -> bool:
    if g == w:
        return True
    if suffix_only:
        return w.startswith(g) and len(w) - len(g) <= 3
    if exact or len(g) < 5 or not g.isalpha() or not w.isalpha() or abs(len(g) - len(w)) > 4:
        return False
    need = max(5, len(g) - 3)
    return len(w) >= need and w[:need] == g[:need]


def _strip_names(tokens: list[str], names) -> list[str]:
    """Blank out resolved entity names ("Mo'jotat Klaster" MChJ is a company, not the activity Кластер)."""
    out = list(tokens)
    for name in names or ():
        nt = normalize_name(name).split()
        k = len(nt)
        if not k:
            continue
        for i in range(len(out) - k + 1):
            if out[i:i + k] == nt:
                out[i:i + k] = [""] * k
    return out


def mentioned_values(question: str, index: list[_Value], ignore_names=()) -> list[_Value]:
    """Catalog values named in the question; a value inside a longer matched value is dropped (метр / куб.метр).
    Words that belong to a resolved company/broker name are ignored."""
    q = _strip_names(normalize_text(question).split(), ignore_names)
    spans = []
    for v in index:
        k = len(v.tokens)
        exact, suffix = v.field in _EXACT_FIELDS, v.field in _SUFFIX_FIELDS
        for i in range(len(q) - k + 1):
            if all(_tok_match(g, w, exact, suffix) for g, w in zip(v.tokens, q[i:i + k])):
                spans.append((i, i + k, v))
                break
    out = []
    for a, b, v in spans:
        inside = any(a2 <= a and b <= b2 and (b2 - a2) > (b - a) for a2, b2, _ in spans)
        if not inside:
            out.append(v)
    return out


def _spec_strings(spec: Spec) -> dict[str, set[str]]:
    """spec key -> normalized string values set on it (filters, share_of, registry fields)."""
    out: dict[str, set[str]] = {}

    def add(key, value):
        if value is None or value == [] or isinstance(value, bool):
            return
        vals = value if isinstance(value, (list, tuple)) else [value]
        out.setdefault(key, set()).update(normalize_text(str(x)) for x in vals)

    if isinstance(spec, DealsAggregate):
        for f in fields(spec.filters):
            add(f.name, getattr(spec.filters, f.name))
        if spec.share_of is not None:
            add(spec.share_of.field if spec.share_of.field != "status" else "statuses", list(spec.share_of.values))
    elif isinstance(spec, RegistryCount):
        for f in fields(spec):
            add(f.name, getattr(spec, f.name))
    return out


def _covered(v: _Value, spec: Spec, strings: dict[str, set[str]]) -> bool:
    keys, dims = FIELD_TARGETS[v.field]
    if isinstance(spec, DealsAggregate):
        if spec.group_by in dims:
            return True
        if v.field == "unit" and any(m in _UNIT_MEASURES for m in spec.measures):
            return True
    # a filter on the same field covers the value (also its complement, e.g. "кроме экспорта" -> Внутренний)
    return any(strings.get(k) for k in keys)


def missing_condition_error(question: str, spec: Spec, index: list[_Value], ignore_names=()) -> str | None:
    if not isinstance(spec, (DealsAggregate, RegistryCount)):
        return None
    strings = _spec_strings(spec)
    found = mentioned_values(question, index, ignore_names)
    by_field: dict[str, set[str]] = {}
    for v in found:
        by_field.setdefault(v.field, set()).add(v.value)
    for v in found:
        if v.field == "contract_form" and by_field["contract_form"] >= set(CONTRACT_FORMS):
            continue  # "all forms (spot, forward, futures)": no contract_form restriction
        if _covered(v, spec, strings):
            continue
        keys, dims = FIELD_TARGETS[v.field]
        where = " or ".join(("filters." if isinstance(spec, DealsAggregate) and k != "activity" else "") + k
                            for k in keys if isinstance(spec, DealsAggregate) or k == "activity")
        dim_hint = f" (or group_by={' / '.join(dims)})" if isinstance(spec, DealsAggregate) else ""
        if isinstance(spec, RegistryCount) and v.field not in ("activity", "contract_form"):
            where = "a deals_aggregate spec with filters." + keys[0]
            dim_hint = ""
        elif isinstance(spec, RegistryCount) and v.field == "contract_form":
            where = "contract_form"
        return (f"the question names «{v.value}» ({v.field}) but the spec does not use it: add it with "
                f"{where}{dim_hint}. If the form cannot express this condition, call declare_unanswerable — "
                "never drop a condition")
    if not isinstance(spec, DealsAggregate):
        return None
    q = question.replace("ё", "е").replace("Ё", "Е")
    ranked = spec.group_by not in (None, "broker_side") and not (spec.group_by in TIME_DIMENSIONS and spec.top is None)
    if ranked and spec.order != "asc" and _LEAST_RE.search(q):
        return ("the question asks for the least / cheapest / fewest: set order=\"asc\" so the ranking starts "
                "from the smallest value")
    if _DISTINCT_RE.search(q) and not any(m in DISTINCT_MEASURES for m in spec.measures):
        return ("the question asks how many distinct items: use a distinct_* measure "
                f"({', '.join(DISTINCT_MEASURES)}) without group_by")
    if _SHARE_RE.search(q) and not (any(m in _PERCENT_MEASURES for m in spec.measures) or spec.compare_periods):
        return ("the question asks for a share / percentage: use share_pct with share_of {field, value} "
                "(or cancelled_pct for the cancelled share); if the share is of something share_of cannot "
                "express, call declare_unanswerable")
    return None


# catalog field -> columns whose use in free SQL shows the condition is applied
_SQL_COLUMNS = {
    "category": {"category"}, "industry": {"industry"}, "product_group": {"product_group"},
    "delivery_basis": {"delivery_basis"}, "packaging": {"packaging"},
    "activity": {"activity", "buyer_activity", "seller_activity"}, "status": {"status", "is_cancelled"},
    "contract_form": {"contract_form"}, "contract_type": {"contract_type"}, "trade_type": {"trade_type"},
    "unit": {"unit", "volume", "volume_tonnes"},
}


def sql_missing_condition_error(question: str, sql: str, index: list[_Value], ignore_names=()) -> str | None:
    """The same checks for free SQL: a named catalog value must appear as a literal (or its column must be grouped
    / used), least wording needs an ascending order or MIN, distinct wording COUNT(DISTINCT), share wording a ratio."""
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return None  # the SQL validator reports parse errors
    literals = {normalize_text(str(lit.this)) for lit in tree.find_all(exp.Literal) if lit.is_string}
    columns = {c.name.lower() for c in tree.find_all(exp.Column)}
    grouped = {c.name.lower() for g in tree.find_all(exp.Group) for c in g.find_all(exp.Column)}
    negated = bool(_NEGATION_RE.search(question))
    for v in mentioned_values(question, index, ignore_names):
        cols = _SQL_COLUMNS[v.field]
        if normalize_text(v.value) in literals or cols & grouped or (negated and cols & columns):
            continue
        if v.field == "unit" and cols & columns:
            continue
        return (f"the question names «{v.value}» ({v.field}) but the SQL does not filter or group by it; "
                "add the condition, or call declare_unanswerable — never drop a condition")
    q = question.replace("ё", "е").replace("Ё", "Е")
    if _LEAST_RE.search(q) and not _EACH_RE.search(q) and tree.find(exp.Limit) is not None:
        orders = list(tree.find_all(exp.Ordered))
        if orders and not any(not o.args.get("desc") for o in orders) and tree.find(exp.Min) is None:
            return "the question asks for the least / cheapest / fewest: order the ranking ascending"
    if _DISTINCT_RE.search(q) and not any(isinstance(c.this, exp.Distinct) for c in tree.find_all(exp.Count)):
        return "the question asks how many distinct items: use COUNT(DISTINCT ...)"
    if _SHARE_RE.search(q) and tree.find(exp.Div) is None:
        return "the question asks for a share / percentage: compute it as a ratio column in SQL"
    return None
