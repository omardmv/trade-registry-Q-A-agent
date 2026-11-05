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
# Values that are also ordinary words in questions ("сделок заключено", "новых", "производитель цемента"): they
# count only when quoted, next to a context word (статус / вид деятельности ...) or, for Производитель, in the plural
# ("производители", "производителей" = companies of that type).
_CONTEXT_ONLY = {("status", "Новый"), ("status", "Заключен"), ("status", "Оплачен"), ("activity", "Производитель")}
_CONTEXT_RE = {"status": re.compile(r"статус|состояни|holat|ҳолат", re.IGNORECASE),
               "activity": re.compile(r"деятельност|faoliyat|фаолият|тип\w* компани|turi", re.IGNORECASE)}
# plural (company-type) forms: whole tokens, or token prefixes for Uzbek plural + case endings
# ("ishlab chiqaruvchilar", "ishlab chiqaruvchilardan")
_PLURAL_FORMS = {("activity", "Производитель"): {"proizvoditeli", "proizvoditeley"}}
_COUNT_WORDS = {"nechta", "qancha"}   # normalize_text transliterates Cyrillic нечта / қанча
_PLURAL_PREFIXES = {("activity", "Производитель"): ("chiqaruvchilar",)}
# Status names are past participles of everyday verbs: exact tokens only. Unit words only take endings
# ("литр" ~ "литрах").
_EXACT_FIELDS = {"status"}
_SUFFIX_FIELDS = {"unit"}
_SINGLE_VALUED = {"contract_form", "contract_type", "trade_type"}
# Measures whose output is per unit of measure / per kg: a unit word in the question names the output unit.
_UNIT_MEASURES = {"volume", "volume_tonnes", "delivered_volume", "undelivered_volume", "min_unit_price_uzs",
                  "max_unit_price_uzs", "avg_price_uzs_per_kg", "mean_deal_price_uzs_per_t",
                  "median_price_uzs_per_kg", "lots_bought", "lots_delivered", "undelivered_lots"}
# A product family with the same name as a category / industry ("Нефтепродукты") covers it unless the question
# explicitly asks about the category / industry.
_CATEGORY_WORD_RE = re.compile(r"категори|отрасл|toifa|тоифа|soha|соҳа|tarmog|тармоғ", re.IGNORECASE)
_QUOTED_RE = re.compile(r"[«\"“']([^«»\"“”']+)[»\"”']")

_LEAST_RE = re.compile(r"наименьш|меньше всего|дешевле всего|сам(?:ый|ая|ое|ую|ой|ых|ые|ом)\s+деш[её]в|минимальн"
                       r"|eng\s+kam|eng\s+arzon|eng\s+past", re.IGNORECASE)
_DISTINCT_RE = re.compile(r"сколько\s+(?:\w+\s+)?(?:разных|различных|уникальных)|nechta\s+(?:turli|xil)"
                          r"|nechta\s+\w+\s+xil", re.IGNORECASE)
_SHARE_RE = re.compile(r"(?<!\w)(?:дол[яиюей]|процент\w*|foiz\w*|ulush\w*|фоиз\w*|улуш\w*)(?!\w)|%",
                       re.IGNORECASE)
_PERCENT_MEASURES = {"share_pct", "cancelled_pct", "avg_price_change_pct"}
_SHARE_BASIS_RE = {"amount_uzs": re.compile(r"оборот|сумм|summa|qiymat|pul(?!\w)", re.IGNORECASE),
                   "volume_t": re.compile(r"объ[её]м|тонн|hajm|tonna", re.IGNORECASE)}
# basis word attached to the share word ("процент ... по сумме", "foiz ... summa bo'yicha")
_SHARE_WORD = r"(?:дол[яиюей]|процент\w*|foiz\w*|ulush\w*|фоиз\w*|улуш\w*)"
_BASIS_PHRASE = {"amount_uzs": r"(?:по\s+(?:сумм\w*|оборот\w*)|summa\s+bo.yicha|qiymat\s+bo.yicha)",
                 "volume_t": r"(?:по\s+объ[её]м\w*|hajm\s+bo.yicha)"}
_ATTACHED_BASIS_RE = {b: re.compile(rf"(?<!\w){_SHARE_WORD}(?:\W+\w+){{0,6}}?\W+{ph}", re.IGNORECASE)
                      for b, ph in _BASIS_PHRASE.items()}
_GRADE_RE = re.compile(
    r"\b\d+[- ]?(?:сорт\w*|класс\w*)\b|\b(?:I|II|III|IV|первый|второй|третий|высш\w*)\s+сорт\w*"
    r"|\b\d+[- ]?nav\w*|(?<!\w)(?:АИ-?\s?\d+|М00К|М0К|ПЦ\s?\d+|ЦЕМ\s?[IV]+)(?!\w)",
    re.IGNORECASE)
_NEGATION_RE = re.compile(r"(?<!\w)(?:кроме|без|не|bundan\s+tashqari|tashqari|emas)(?!\w)", re.IGNORECASE)
_EACH_RE = re.compile(r"по\s+кажд\w*|по\s+всем|har\s+bir", re.IGNORECASE)
_MIN_MAX_MEASURES = {"min_unit_price_uzs", "max_unit_price_uzs"}
_PRODUCT_FIELDS = ("category", "industry", "product_group")
# a money / volume measure next to cancelled_pct answers "what share ... and for how much" (not a money share)
_BASIS_MEASURES = {"amount_uzs": {"amount_uzs", "amount_usd"}, "volume_t": {"volume_tonnes", "volume"}}


@dataclass(frozen=True)
class _Value:
    field: str
    value: str
    tokens: tuple[str, ...]


def load_synonyms(path=None) -> dict[tuple[str, str], list[str]]:
    """(field, value) -> Uzbek / Russian synonyms from data_dictionary/catalog_synonyms.csv."""
    import csv
    from app import config
    path = path or config.DICT_DIR / "catalog_synonyms.csv"
    out: dict[tuple[str, str], list[str]] = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[(row["field"], row["value"])] = [t.strip() for t in row["synonyms"].split(";") if t.strip()]
    return out


def build_index(catalog: dict[str, set[str]], synonyms: dict | None = None) -> list[_Value]:
    if synonyms is None:
        synonyms = load_synonyms()
    out = []
    for fld in FIELD_TARGETS:
        for value in sorted(catalog.get(fld, ())):
            for text in [value, *synonyms.get((fld, value), [])]:
                toks = tuple(normalize_text(text).split())
                if toks and _Value(fld, value, toks) not in out:
                    out.append(_Value(fld, value, toks))
    return out


def _tok_match(g: str, w: str, exact: bool, suffix_only: bool = False) -> bool:
    if g == w:
        return True
    if exact or not g.isalpha() or not w.isalpha():
        return False
    if suffix_only:
        return w.startswith(g) and len(w) - len(g) <= 3
    if len(g) < 5:  # short words take a suffix: спот ~ спотовых
        return len(g) >= 4 and w.startswith(g) and len(w) - len(g) <= 5
    if abs(len(g) - len(w)) > 4:
        return False
    need = max(5, len(g) - 3)
    return len(w) >= need and w[:need] == g[:need]


def _strip_names(tokens: list[str], names, keep: set[str] = frozenset()) -> list[str]:
    """Blank out resolved entity names ("Mo'jotat Klaster" MChJ is a company, not the activity Кластер), tolerating
    inflection; names equal to a catalog value (product family «Нефтепродукты») are kept for the coverage check."""
    out = list(tokens)
    for name in names or ():
        nt = normalize_name(name).split()
        k = len(nt)
        if not k or " ".join(nt) in keep:
            continue
        for i in range(len(out) - k + 1):
            if all(_tok_match(g, w, False) for g, w in zip(nt, out[i:i + k])):
                out[i:i + k] = [""] * k
    return out


def _context_ok(v: _Value, question: str, q_tokens: list[str], span: tuple[int, int]) -> bool:
    if (v.field, v.value) not in _CONTEXT_ONLY:
        return True
    if any(normalize_text(m) == " ".join(v.tokens) for m in _QUOTED_RE.findall(question)):
        return True
    rx = _CONTEXT_RE.get(v.field)
    if rx is not None and rx.search(question):
        return True
    plural = _PLURAL_FORMS.get((v.field, v.value), set())
    prefixes = _PLURAL_PREFIXES.get((v.field, v.value), ())
    if any(t in plural or t.startswith(prefixes) for t in q_tokens[span[0]:span[1]] if t):
        return True
    # singular right after a count word: "nechta ishlab chiqaruvchi ..." = how many companies of this type
    return (v.field, v.value) in _PLURAL_FORMS and any(
        t in _COUNT_WORDS for t in q_tokens[max(0, span[0] - 2):span[0]])


def mentioned_spans(question: str, index: list[_Value], ignore_names=()) -> list[tuple[int, int, _Value]]:
    """(start, end, value) of catalog values named in the question, as token spans of normalize_text(question);
    a value inside a longer matched value is dropped (метр / куб.метр). Words that belong to a resolved
    company/broker name are ignored."""
    keep = {" ".join(v.tokens) for v in index}
    q = _strip_names(normalize_text(question).split(), ignore_names, keep)
    spans = []
    for v in index:
        k = len(v.tokens)
        exact, suffix = v.field in _EXACT_FIELDS, v.field in _SUFFIX_FIELDS
        for i in range(len(q) - k + 1):
            if all(_tok_match(g, w, exact, suffix) for g, w in zip(v.tokens, q[i:i + k])) \
                    and _context_ok(v, question, q, (i, i + k)):
                spans.append((i, i + k, v))
                break
    out = []
    for a, b, v in spans:
        inside = any(a2 <= a and b <= b2 and (b2 - a2) > (b - a) for a2, b2, _ in spans)
        if not inside and all(o.value != v.value or o.field != v.field for _, _, o in out):
            out.append((a, b, v))
    return out


def mentioned_values(question: str, index: list[_Value], ignore_names=()) -> list[_Value]:
    """Catalog values named in the question (see mentioned_spans)."""
    return [v for _, _, v in mentioned_spans(question, index, ignore_names)]


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


def _products_within(v: _Value, spec: Spec, question: str, product_attrs) -> bool:
    """A products-only filter whose every product belongs to the named category / industry / group (or, without
    the word категория / отрасль, to a family of that name) already expresses the condition."""
    products = spec.filters.products if isinstance(spec, DealsAggregate) else \
        spec.products if isinstance(spec, RegistryCount) else []
    if not products or not product_attrs or v.field not in _PRODUCT_FIELDS:
        return False
    fields_ok = [v.field] + (["product_family"] if v.field in ("category", "industry")
                             and not _CATEGORY_WORD_RE.search(question) else [])
    return all(any(product_attrs.get(p, {}).get(f) == {v.value} for f in fields_ok) for p in products)


def _covered(v: _Value, spec: Spec, strings: dict[str, set[str]], question: str, named: set[str],
             product_attrs=None) -> bool:
    keys, dims = FIELD_TARGETS[v.field]
    value = normalize_text(v.value)
    if _products_within(v, spec, question, product_attrs):
        return True
    if isinstance(spec, DealsAggregate):
        if spec.group_by in dims:
            return True
        if v.field == "unit" and (any(m in _UNIT_MEASURES for m in spec.measures) or spec.share_of is not None):
            return True  # the unit names the output (or the share basis, checked below)
        if v.field in ("category", "industry") and not _CATEGORY_WORD_RE.search(question) and (
                value in strings.get("product_family", set()) or spec.group_by == "product_family"):
            return True
    set_values = set().union(*(strings.get(k, set()) for k in keys))
    if value in set_values:
        return True
    if set_values and _NEGATION_RE.search(question):
        return True  # complement, e.g. "кроме экспорта" -> Внутренний
    # two values of a single-valued field named ("(спот) ... в форме «Форвард»"): one of them is a valid reading
    return v.field in _SINGLE_VALUED and bool(set_values & named)


def missing_condition_error(question: str, spec: Spec, index: list[_Value], ignore_names=(),
                            product_attrs=None) -> str | None:
    """product_attrs: product_canonical -> {category, industry, product_group, product_family: values}
    (app.catalog.load_product_attrs); lets a products filter cover a category it lies within."""
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
        named = {normalize_text(x) for x in by_field[v.field]}
        if _covered(v, spec, strings, question, named, product_attrs):
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
    q = question.replace("ё", "е").replace("Ё", "Е")
    if isinstance(spec, RegistryCount):
        if _SHARE_RE.search(q):
            return ("registry_count returns counts only, not a share / percentage: call declare_unanswerable")
        return None
    if _GRADE_RE.search(question) and spec.filters.products and not spec.filters.product_variant_query:
        return ("the question names a grade / mark of the product: put the product phrase with the grade / mark "
                "into filters.product_variant_query (e.g. «хлопок-сырец 1 сорт»; resolve(kind=product_variant) "
                "first) instead of filters.products")
    ranked = spec.group_by not in (None, "broker_side") and not (spec.group_by in TIME_DIMENSIONS and spec.top is None)
    least_ok = _EACH_RE.search(q) or any(m in _MIN_MAX_MEASURES for m in spec.measures)
    if ranked and spec.order != "asc" and not least_ok and _LEAST_RE.search(q):
        return ("the question asks for the least / cheapest / fewest: set order=\"asc\" so the ranking starts "
                "from the smallest value")
    if _DISTINCT_RE.search(q) and not any(m in DISTINCT_MEASURES for m in spec.measures):
        return ("the question asks how many distinct items: use a distinct_* measure "
                f"({', '.join(DISTINCT_MEASURES)}) without group_by")
    if _SHARE_RE.search(q) and spec.share_of is not None and spec.share_of.basis == "deals":
        for basis, rx in _SHARE_BASIS_RE.items():
            if rx.search(q):
                return (f"the question asks for a share of money / volume, not of the deal count: set "
                        f"share_of.basis=\"{basis}\"")
    if _SHARE_RE.search(q) and "cancelled_pct" in spec.measures:
        for basis, rx in _SHARE_BASIS_RE.items():
            if rx.search(q) and (_ATTACHED_BASIS_RE[basis].search(q)
                                 or not _BASIS_MEASURES[basis] & set(spec.measures)):
                return ("the question asks for a share of money / volume, but cancelled_pct is a share of the deal "
                        "count: use share_pct with share_of {field: \"status\", value: \"Аннулирован(Не оплачен)\", "
                        f"basis: \"{basis}\"}} (basis amount_uzs for money, volume_t for tonnes)")
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
    if _LEAST_RE.search(q) and not _EACH_RE.search(q) and tree.find(exp.Limit) is not None \
            and tree.find(exp.Max) is None:
        orders = list(tree.find_all(exp.Ordered))
        if orders and not any(not o.args.get("desc") for o in orders) and tree.find(exp.Min) is None:
            return "the question asks for the least / cheapest / fewest: order the ranking ascending"
    if _DISTINCT_RE.search(q) and not any(isinstance(c.this, exp.Distinct) for c in tree.find_all(exp.Count)) \
            and not any(sel.args.get("distinct") for sel in tree.find_all(exp.Select)):
        return "the question asks how many distinct items: use COUNT(DISTINCT ...)"
    if _SHARE_RE.search(q) and tree.find(exp.Div) is None:
        return "the question asks for a share / percentage: compute it as a ratio column in SQL"
    return None
