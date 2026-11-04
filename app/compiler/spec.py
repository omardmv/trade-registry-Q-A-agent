"""Structured query specs filled by the LLM; validated strictly before compilation."""
import datetime as dt
import re
from dataclasses import dataclass, field
from dataclasses import fields as dc_fields

DATA_START, DATA_END = dt.date(2022, 1, 3), dt.date(2026, 10, 30)
RATES_START, RATES_END = dt.date(2022, 1, 1), dt.date(2026, 10, 31)

MEASURES = ("deal_count", "amount_uzs", "amount_usd", "volume_tonnes", "volume", "avg_amount_uzs",
            "avg_price_uzs_per_kg", "max_amount_uzs", "distinct_buyers", "distinct_sellers",
            "lots_bought", "lots_delivered", "cancelled_pct",
            "distinct_products", "distinct_regions", "distinct_seller_brokers", "distinct_buyer_brokers",
            "distinct_brokers", "share_pct", "median_price_uzs_per_kg", "mean_deal_price_uzs_per_t",
            "min_unit_price_uzs", "max_unit_price_uzs", "avg_price_change_pct", "avg_days_to_payment",
            "avg_days_to_delivery", "avg_delivery_delay_days", "late_delivery_count", "late_payment_count",
            "undelivered_lots", "delivered_volume", "undelivered_volume")
DISTINCT_MEASURES = tuple(m for m in MEASURES if m.startswith("distinct_"))
PERCENT_MEASURES = ("share_pct", "cancelled_pct")
DIMENSIONS = ("buyer", "seller", "participant", "seller_broker", "buyer_broker", "broker", "region",
              "product", "product_family", "month", "year", "contract_type", "contract_form",
              "trade_type", "status", "unit", "contract", "deal",
              "category", "industry", "product_group", "delivery_basis", "packaging", "hs_code",
              "product_variant", "quarter", "hour", "buyer_region", "seller_region", "buyer_activity",
              "seller_activity", "broker_side")
TIME_DIMENSIONS = ("month", "year", "quarter", "hour")
ORDERS = ("desc", "asc")
SHARE_FIELDS = ("contract_form", "contract_type", "trade_type", "status")
SHARE_BASES = ("deals", "amount_uzs", "volume_t")
# Filters whose values are validated against the database's distinct values (see app/catalog.py):
# filter key -> catalog field.
CATALOG_FILTERS = {"categories": "category", "industries": "industry", "product_groups": "product_group",
                   "delivery_bases": "delivery_basis", "packagings": "packaging", "hs_codes": "hs_code",
                   "buyer_activities": "activity", "seller_activities": "activity", "units": "unit"}
STATUSES = ("Новый", "Не подписан", "Заключен", "Ждёт оплату", "Оплачен", "Исполнен",
            "Исполнен(Частично)", "Аннулирован(Не оплачен)")
CONTRACT_TYPES = ("Внутренний", "Экспортный")
CONTRACT_FORMS = ("Спот", "Форвард", "Фьючерс")
TRADE_TYPES = ("Английский аукцион", "Обратный аукцион", "Двусторонний аукцион")
STATUS_SCOPES = ("all", "valid", "split")
REGISTRY_ENTITIES = ("participants", "brokers", "contracts")
ROLES = ("Покупатель", "Продавец")


class SpecError(ValueError):
    pass


@dataclass
class Filters:
    year: int | None = None
    month_from: int | None = None
    month_to: int | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    products: list[str] = field(default_factory=list)
    product_family: str | None = None
    regions: list[str] = field(default_factory=list)
    buyer_inns: list[str] = field(default_factory=list)
    seller_inns: list[str] = field(default_factory=list)
    participant_inns: list[str] = field(default_factory=list)
    seller_brokers: list[str] = field(default_factory=list)
    buyer_brokers: list[str] = field(default_factory=list)
    brokers: list[str] = field(default_factory=list)
    contract_id: int | None = None
    contract_type: str | None = None
    contract_form: str | None = None
    trade_type: str | None = None
    statuses: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    product_groups: list[str] = field(default_factory=list)
    delivery_bases: list[str] = field(default_factory=list)
    packagings: list[str] = field(default_factory=list)
    hs_codes: list[str] = field(default_factory=list)
    product_variants: list[str] = field(default_factory=list)
    buyer_regions: list[str] = field(default_factory=list)
    seller_regions: list[str] = field(default_factory=list)
    buyer_activities: list[str] = field(default_factory=list)
    seller_activities: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ShareOf:
    """Numerator condition of share_pct: deals whose `field` is one of `values`."""
    field: str
    values: tuple[str, ...]
    basis: str = "deals"   # deals: share of deal count; amount_uzs / volume_t: share of money / tonnes


@dataclass(frozen=True)
class Period:
    year: int
    month_from: int | None = None
    month_to: int | None = None


@dataclass
class DealsAggregate:
    measures: list[str]
    filters: Filters = field(default_factory=Filters)
    group_by: str | None = None
    top: int | None = None
    status_scope: str = "split"
    order: str = "desc"
    share_of: ShareOf | None = None
    compare_periods: list[Period] = field(default_factory=list)


@dataclass
class DealLookup:
    deal_id: int


@dataclass
class ContractLookup:
    contract_id: int


@dataclass
class ParticipantLookup:
    inn: str


@dataclass
class BrokerLookup:
    broker_code: str


@dataclass
class UsdRate:
    date: dt.date | None = None
    year: int | None = None
    month: int | None = None
    extreme: str | None = None


@dataclass
class RegistryCount:
    entity: str
    role: str | None = None
    vat_payer: bool | None = None
    region: str | None = None
    registered_year: int | None = None
    activity: str | None = None
    products: list[str] = field(default_factory=list)
    contract_form: str | None = None
    seller_inn: str | None = None
    active_on: dt.date | None = None


Spec = DealsAggregate | DealLookup | ContractLookup | ParticipantLookup | BrokerLookup | UsdRate | RegistryCount


def _opt_int(d: dict, key: str) -> int | None:
    v = d.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise SpecError(f"{key} must be an integer, got {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            pass
    raise SpecError(f"{key} must be an integer, got {v!r}")


def _opt_bool(d: dict, key: str) -> bool | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    raise SpecError(f"{key} must be true or false, got {v!r}")


def _opt_date(d: dict, key: str) -> dt.date | None:
    v = d.get(key)
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        raise SpecError(f"{key} must be YYYY-MM-DD, got {v!r}")


def _str_list(d: dict, key: str) -> list[str]:
    v = d.get(key)
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        raise SpecError(f"{key} must be a list of strings, got {v!r}")
    return [str(x).strip() for x in v if str(x).strip()]


def _enum(value, allowed, name):
    if value not in allowed:
        raise SpecError(f"invalid {name} {value!r}; allowed: {', '.join(allowed)}")
    return value


def _opt_enum(d: dict, key: str, allowed) -> str | None:
    v = d.get(key)
    return None if v in (None, "") else _enum(v, allowed, key)


def _outside(what: str) -> SpecError:
    return SpecError(f"{what} is outside the data (deals 03.01.2022–30.10.2026, USD rates "
                     f"01.01.2022–31.10.2026); call declare_unanswerable")


def _fmt(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def _opt_str(d: dict, key: str) -> str | None:
    v = d.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise SpecError(f"{key} must be a string, got {v!r}")
    return v


def _check_year(year: int | None) -> None:
    if year is not None and not 2022 <= year <= 2026:
        raise _outside(f"year {year}")


FILTER_KEYS = tuple(f.name for f in dc_fields(Filters))
ALLOWED_KEYS = {
    "deals_aggregate": ("kind", "measures", "filters", "group_by", "top", "status_scope", "order", "share_of",
                        "compare_periods"),
    "deal_lookup": ("kind", "deal_id"),
    "contract_lookup": ("kind", "contract_id"),
    "participant_lookup": ("kind", "inn"),
    "broker_lookup": ("kind", "broker_code"),
    "usd_rate": ("kind", "date", "year", "month", "extreme"),
    "registry_count": ("kind", "entity", "role", "vat_payer", "region", "registered_year", "activity",
                       "products", "contract_form", "seller_inn", "active_on"),
}
KINDS = tuple(ALLOWED_KEYS)
USD_EXTREMES = ("min", "max")
PERIOD_KEYS = ("year", "month_from", "month_to")
_MISPLACED = ("date", "year", "month", "month_from", "month_to", "date_from", "date_to") + FILTER_KEYS


def _empty(v) -> bool:
    return v is None or (isinstance(v, (str, list, tuple, dict)) and len(v) == 0)


def _reject_unknown(d: dict, allowed: tuple[str, ...], where: str, kind: str | None = None) -> None:
    for key in d:
        if key in allowed or _empty(d[key]):
            continue
        hint = ""
        if kind == "deals_aggregate" and key in _MISPLACED:
            hint = f"put {key} inside filters"
            if key == "date":
                hint += " (for a single day use filters.date_from and filters.date_to with the same date)"
            hint += "; "
        raise SpecError(f"unknown key {key!r} in {where}; {hint}allowed keys: {', '.join(allowed)}")


def _parse_filters(d: dict) -> Filters:
    if not isinstance(d, dict):
        raise SpecError(f"filters must be an object, got {d!r}")
    _reject_unknown(d, FILTER_KEYS, "filters")
    f = Filters(
        year=_opt_int(d, "year"), month_from=_opt_int(d, "month_from"), month_to=_opt_int(d, "month_to"),
        date_from=_opt_date(d, "date_from"), date_to=_opt_date(d, "date_to"),
        products=_str_list(d, "products"), product_family=_opt_str(d, "product_family"),
        regions=_str_list(d, "regions"),
        buyer_inns=_str_list(d, "buyer_inns"), seller_inns=_str_list(d, "seller_inns"),
        participant_inns=_str_list(d, "participant_inns"),
        seller_brokers=[b.upper() for b in _str_list(d, "seller_brokers")],
        buyer_brokers=[b.upper() for b in _str_list(d, "buyer_brokers")],
        brokers=[b.upper() for b in _str_list(d, "brokers")],
        contract_id=_opt_int(d, "contract_id"),
        contract_type=_opt_enum(d, "contract_type", CONTRACT_TYPES),
        contract_form=_opt_enum(d, "contract_form", CONTRACT_FORMS),
        trade_type=_opt_enum(d, "trade_type", TRADE_TYPES),
        statuses=[_enum(s, STATUSES, "status") for s in _str_list(d, "statuses")],
        categories=_str_list(d, "categories"), industries=_str_list(d, "industries"),
        product_groups=_str_list(d, "product_groups"), delivery_bases=_str_list(d, "delivery_bases"),
        packagings=_str_list(d, "packagings"), hs_codes=_str_list(d, "hs_codes"),
        product_variants=_str_list(d, "product_variants"),
        buyer_regions=_str_list(d, "buyer_regions"), seller_regions=_str_list(d, "seller_regions"),
        buyer_activities=_str_list(d, "buyer_activities"), seller_activities=_str_list(d, "seller_activities"),
        units=_str_list(d, "units"),
    )
    for code in f.hs_codes:
        if not re.fullmatch(r"\d{4,10}", code):
            raise SpecError(f"hs_code must be digits, got {code!r}")
    _check_year(f.year)
    has_month = f.month_from is not None or f.month_to is not None
    has_dates = f.date_from is not None or f.date_to is not None
    if (f.year is not None or has_month) and has_dates:
        raise SpecError("use either year/month or date_from/date_to, not both")
    if has_month and f.year is None:
        raise SpecError("a month filter needs a year; for a cross-year range use date_from/date_to")
    if f.date_from is not None and f.date_to is not None and f.date_from > f.date_to:
        raise SpecError("date_from must be <= date_to")
    if f.month_from is None and f.month_to is not None:
        f.month_from = f.month_to
    if f.month_to is None and f.month_from is not None:
        f.month_to = f.month_from
    for m in (f.month_from, f.month_to):
        if m is not None and not 1 <= m <= 12:
            raise SpecError(f"month {m} must be 1..12")
    if f.month_from is not None and f.month_from > f.month_to:
        raise SpecError("month_from must be <= month_to")
    if f.year == 2026 and f.month_from is not None and f.month_from > 10:
        raise _outside(f"{f.month_from:02d}.2026")
    if f.year == 2026 and f.month_to is not None and f.month_to > 10:
        raise SpecError("data ends 30.10.2026: set month_to to 10 and add an assumption that the "
                        "period is covered only up to 30.10.2026")
    if has_dates:
        lo = f.date_from or DATA_START  # a missing bound means the data bound
        hi = f.date_to or DATA_END
        if hi < DATA_START or lo > DATA_END:
            raise _outside(f"{f.date_from or '...'}..{f.date_to or '...'}")
        if lo < DATA_START or hi > DATA_END:
            raise SpecError(f"data covers {_fmt(DATA_START)}–{_fmt(DATA_END)}: clamp date_from/date_to "
                            "to that range and add an assumption that the period is covered only "
                            "within it")
    for inn in f.buyer_inns + f.seller_inns + f.participant_inns:
        if not re.fullmatch(r"\d{9}", inn):
            raise SpecError(f"INN must be 9 digits, got {inn!r}")
    for b in f.seller_brokers + f.buyer_brokers + f.brokers:
        if not re.fullmatch(r"BK\d{3}", b):
            raise SpecError(f"broker code must look like BK001, got {b!r}")
    return f


def _parse_share_of(v) -> ShareOf:
    if not isinstance(v, dict):
        raise SpecError(f"share_of must be an object {{field, value}} or {{field, values}}, got {v!r}")
    _reject_unknown(v, ("field", "value", "values", "basis"), "share_of")
    fld = v.get("field")
    if fld == "statuses":
        fld = "status"
    _enum(fld, SHARE_FIELDS, "share_of.field")
    values = _str_list(v, "values") + _str_list(v, "value")
    if not values:
        raise SpecError("share_of needs value (or values)")
    allowed = {"contract_form": CONTRACT_FORMS, "contract_type": CONTRACT_TYPES, "trade_type": TRADE_TYPES,
               "status": STATUSES}[fld]
    for x in values:
        _enum(x, allowed, f"share_of value for {fld}")
    basis = v.get("basis") or "deals"
    _enum(basis, SHARE_BASES, "share_of.basis")
    return ShareOf(fld, tuple(dict.fromkeys(values)), basis)


def _parse_period(v) -> Period:
    if not isinstance(v, dict):
        raise SpecError(f"each compare_periods item must be an object {{year[, month_from, month_to]}}, got {v!r}")
    _reject_unknown(v, PERIOD_KEYS, "compare_periods item")
    if _opt_int(v, "year") is None:
        raise SpecError("each compare_periods item needs a year")
    f = _parse_filters({k: v[k] for k in PERIOD_KEYS if k in v})
    return Period(f.year, f.month_from, f.month_to)


def _parse_aggregate(d: dict) -> DealsAggregate:
    measures = _str_list(d, "measures")
    if not measures:
        raise SpecError("at least one measure is required")
    if len(measures) > 2:
        raise SpecError("at most 2 measures per query")
    for m in measures:
        _enum(m, MEASURES, "measure")
    group_by = d.get("group_by") or None
    if group_by is not None:
        _enum(group_by, DIMENSIONS, "group_by")
    top = _opt_int(d, "top")
    if group_by and top is None and group_by not in TIME_DIMENSIONS + ("broker_side",):
        top = 3
    if top is not None and not 1 <= top <= 20:
        raise SpecError("top must be 1..20")
    scope = d.get("status_scope") or "split"
    _enum(scope, STATUS_SCOPES, "status_scope")
    order = d.get("order") or "desc"
    _enum(order, ORDERS, "order")
    if order == "asc" and group_by is None:
        raise SpecError("order applies to rankings only: set group_by (e.g. deal, seller, product) with order")
    share_raw = d.get("share_of")
    share_of = None if _empty(share_raw) else _parse_share_of(share_raw)
    if "share_pct" in measures and share_of is None:
        raise SpecError("share_pct needs share_of {field, value}: the condition whose share is computed, "
                        f"field one of {', '.join(SHARE_FIELDS)}")
    if share_of is not None and "share_pct" not in measures:
        raise SpecError("share_of is only used with the share_pct measure")
    filters = d.get("filters")
    f = _parse_filters({} if filters is None else filters)
    periods_raw = d.get("compare_periods")
    periods: list[Period] = []
    if not _empty(periods_raw):
        if not isinstance(periods_raw, (list, tuple)) or len(periods_raw) != 2:
            raise SpecError("compare_periods must be a list of exactly two periods")
        periods = [_parse_period(p) for p in periods_raw]
        if group_by is not None:
            raise SpecError("compare_periods works only without group_by")
        if any(x is not None for x in (f.year, f.month_from, f.date_from, f.date_to)):
            raise SpecError("with compare_periods put the periods only in compare_periods, not in filters")
        if periods[0] == periods[1]:
            raise SpecError("compare_periods needs two different periods")
        lengths = [12 if p.month_from is None else p.month_to - p.month_from + 1 for p in periods]
        if lengths[0] != lengths[1]:
            raise SpecError("compare_periods must have the same length (e.g. two years, or the same months of "
                            "two years)")
    if group_by == "broker_side":
        if not f.brokers or f.seller_brokers or f.buyer_brokers:
            raise SpecError("group_by=broker_side needs filters.brokers (the broker codes to split by side) and "
                            "no seller_brokers / buyer_brokers")
        if top is not None or order != "desc":
            raise SpecError("group_by=broker_side is not a ranking: do not set top or order")
    return DealsAggregate(measures, f, group_by, top, scope, order, share_of, periods)


def parse_spec(d: dict) -> Spec:
    if not isinstance(d, dict):
        raise SpecError(f"spec must be an object, got {type(d).__name__}")
    kind = d.get("kind")
    if kind in ALLOWED_KEYS:
        _reject_unknown(d, ALLOWED_KEYS[kind], f"{kind} spec", kind)
    if kind == "deals_aggregate":
        return _parse_aggregate(d)
    if kind == "deal_lookup":
        deal_id = _opt_int(d, "deal_id")
        if deal_id is None:
            raise SpecError("deal_id is required")
        return DealLookup(deal_id)
    if kind == "contract_lookup":
        contract_id = _opt_int(d, "contract_id")
        if contract_id is None:
            raise SpecError("contract_id is required")
        return ContractLookup(contract_id)
    if kind == "participant_lookup":
        inn = str(d.get("inn") or "").strip()
        if not re.fullmatch(r"\d{9}", inn):
            raise SpecError(f"inn must be 9 digits, got {d.get('inn')!r}")
        return ParticipantLookup(inn)
    if kind == "broker_lookup":
        code = str(d.get("broker_code") or "").strip().upper()
        if not re.fullmatch(r"BK\d{3}", code):
            raise SpecError(f"broker_code must look like BK001, got {d.get('broker_code')!r}")
        return BrokerLookup(code)
    if kind == "usd_rate":
        spec = UsdRate(_opt_date(d, "date"), _opt_int(d, "year"), _opt_int(d, "month"),
                       _opt_enum(d, "extreme", USD_EXTREMES))
        if spec.date is not None and not RATES_START <= spec.date <= RATES_END:
            raise _outside(spec.date.isoformat())
        if spec.date is not None and (spec.year is not None or spec.month is not None):
            raise SpecError("use either date or year/month")
        if spec.date is not None and spec.extreme is not None:
            raise SpecError("extreme (min/max) needs year or year+month, not a date")
        if spec.month is not None and spec.year is None:
            raise SpecError("a month needs a year")
        if spec.month is not None and not 1 <= spec.month <= 12:
            raise SpecError(f"month {spec.month} must be 1..12")
        _check_year(spec.year)
        if spec.year == 2026 and spec.month is not None and spec.month > 10:
            raise _outside(f"{spec.month:02d}.2026")
        if spec.date is None and spec.year is None:
            raise SpecError("usd_rate needs date or year")
        return spec
    if kind == "registry_count":
        entity = _enum(d.get("entity"), REGISTRY_ENTITIES, "entity")
        role = _opt_enum(d, "role", ROLES)
        vat = _opt_bool(d, "vat_payer")
        year = _opt_int(d, "registered_year")
        _check_year(year)
        activity = _opt_str(d, "activity")
        products = _str_list(d, "products")
        form = _opt_enum(d, "contract_form", CONTRACT_FORMS)
        seller_inn = _opt_str(d, "seller_inn")
        active_on = _opt_date(d, "active_on")
        if active_on is not None and not DATA_START <= active_on <= DATA_END:
            raise _outside(active_on.isoformat())
        if entity != "participants":
            for name, val in (("role", role), ("vat_payer", vat), ("activity", activity)):
                if val is not None:
                    raise SpecError(f"{name} is only allowed when entity is participants, not {entity}")
        if entity != "contracts":
            for name, val in (("registered_year", year), ("products", products or None),
                              ("contract_form", form), ("seller_inn", seller_inn), ("active_on", active_on)):
                if val is not None:
                    raise SpecError(f"{name} is only allowed when entity is contracts, not {entity}")
        if seller_inn is not None and not re.fullmatch(r"\d{9}", seller_inn):
            raise SpecError(f"INN must be 9 digits, got {seller_inn!r}")
        return RegistryCount(entity, role, vat, _opt_str(d, "region"), year, activity, products, form,
                             seller_inn, active_on)
    raise SpecError(f"unknown kind {kind!r}; use {', '.join(KINDS)}")


def check_catalog(spec: Spec, catalog: dict[str, set[str]] | None) -> None:
    """Reject enum-like values (category, industry, activity, unit, ...) that the database does not contain."""
    if not catalog:
        return
    pairs: list[tuple[str, str, str]] = []  # (spec key, catalog field, value)
    if isinstance(spec, DealsAggregate):
        for key, fld in CATALOG_FILTERS.items():
            pairs += [(key, fld, v) for v in getattr(spec.filters, key)]
    elif isinstance(spec, RegistryCount) and spec.activity is not None:
        pairs.append(("activity", "activity", spec.activity))
    for key, fld, value in pairs:
        known = catalog.get(fld)
        if known is not None and value not in known:
            raise SpecError(f"unknown {key} value {value!r}; allowed: {', '.join(sorted(known))}")


def entity_values(spec: Spec) -> set[tuple[str, str]]:
    """(kind, value) pairs that must be grounded; kinds: product, product_family, product_variant, region, inn,
    broker, contract, deal."""
    out: set[tuple[str, str]] = set()
    if isinstance(spec, DealsAggregate):
        f = spec.filters
        out |= {("product", v) for v in f.products}
        out |= {("product_variant", v) for v in f.product_variants}
        out |= {("region", v) for v in f.regions + f.buyer_regions + f.seller_regions}
        out |= {("inn", v) for v in f.buyer_inns + f.seller_inns + f.participant_inns}
        out |= {("broker", v) for v in f.seller_brokers + f.buyer_brokers + f.brokers}
        if f.product_family:
            out.add(("product_family", f.product_family))
        if f.contract_id is not None:
            out.add(("contract", str(f.contract_id)))
    elif isinstance(spec, RegistryCount):
        if spec.region:
            out.add(("region", spec.region))
        out |= {("product", v) for v in spec.products}
        if spec.seller_inn:
            out.add(("inn", spec.seller_inn))
    elif isinstance(spec, ContractLookup):
        out.add(("contract", str(spec.contract_id)))
    elif isinstance(spec, DealLookup):
        out.add(("deal", str(spec.deal_id)))
    elif isinstance(spec, ParticipantLookup):
        out.add(("inn", spec.inn))
    elif isinstance(spec, BrokerLookup):
        out.add(("broker", spec.broker_code))
    return out
