"""Structured query specs filled by the LLM; validated strictly before compilation."""
import datetime as dt
import re
from dataclasses import dataclass, field

DATA_START, DATA_END = dt.date(2022, 1, 3), dt.date(2026, 10, 30)
RATES_START, RATES_END = dt.date(2022, 1, 1), dt.date(2026, 10, 31)

MEASURES = ("deal_count", "amount_uzs", "amount_usd", "volume_tonnes", "volume", "avg_amount_uzs",
            "avg_price_uzs_per_kg", "max_amount_uzs", "distinct_buyers", "distinct_sellers",
            "lots_bought", "lots_delivered", "cancelled_pct")
DIMENSIONS = ("buyer", "seller", "participant", "seller_broker", "buyer_broker", "broker", "region",
              "product", "product_family", "month", "year", "contract_type", "contract_form",
              "trade_type", "status", "unit", "contract")
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


@dataclass
class DealsAggregate:
    measures: list[str]
    filters: Filters = field(default_factory=Filters)
    group_by: str | None = None
    top: int | None = None
    status_scope: str = "split"


@dataclass
class DealLookup:
    deal_id: int


@dataclass
class ContractLookup:
    contract_id: int


@dataclass
class UsdRate:
    date: dt.date | None = None
    year: int | None = None
    month: int | None = None


@dataclass
class RegistryCount:
    entity: str
    role: str | None = None
    vat_payer: bool | None = None
    region: str | None = None
    registered_year: int | None = None


Spec = DealsAggregate | DealLookup | ContractLookup | UsdRate | RegistryCount


def _opt_int(d: dict, key: str) -> int | None:
    v = d.get(key)
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise SpecError(f"{key} must be an integer, got {v!r}")


def _opt_date(d: dict, key: str) -> dt.date | None:
    v = d.get(key)
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        raise SpecError(f"{key} must be YYYY-MM-DD, got {v!r}")


def _str_list(d: dict, key: str) -> list[str]:
    v = d.get(key) or []
    if isinstance(v, str):
        v = [v]
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


def _check_year(year: int | None) -> None:
    if year is not None and not 2022 <= year <= 2026:
        raise _outside(f"year {year}")


def _parse_filters(d: dict) -> Filters:
    f = Filters(
        year=_opt_int(d, "year"), month_from=_opt_int(d, "month_from"), month_to=_opt_int(d, "month_to"),
        date_from=_opt_date(d, "date_from"), date_to=_opt_date(d, "date_to"),
        products=_str_list(d, "products"), product_family=d.get("product_family") or None,
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
    )
    _check_year(f.year)
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
    for d_ in (f.date_from, f.date_to):
        if d_ is not None and not DATA_START <= d_ <= DATA_END:
            raise _outside(d_.isoformat())
    for inn in f.buyer_inns + f.seller_inns + f.participant_inns:
        if not re.fullmatch(r"\d{9}", inn):
            raise SpecError(f"INN must be 9 digits, got {inn!r}")
    for b in f.seller_brokers + f.buyer_brokers + f.brokers:
        if not re.fullmatch(r"BK\d{3}", b):
            raise SpecError(f"broker code must look like BK001, got {b!r}")
    return f


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
    if group_by and top is None and group_by not in ("month", "year"):
        top = 3
    if top is not None and not 1 <= top <= 20:
        raise SpecError("top must be 1..20")
    scope = d.get("status_scope") or "split"
    _enum(scope, STATUS_SCOPES, "status_scope")
    return DealsAggregate(measures, _parse_filters(d.get("filters") or {}), group_by, top, scope)


def parse_spec(d: dict) -> Spec:
    kind = d.get("kind")
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
    if kind == "usd_rate":
        spec = UsdRate(_opt_date(d, "date"), _opt_int(d, "year"), _opt_int(d, "month"))
        if spec.date is not None and not RATES_START <= spec.date <= RATES_END:
            raise _outside(spec.date.isoformat())
        _check_year(spec.year)
        if spec.year == 2026 and spec.month is not None and spec.month > 10:
            raise _outside(f"{spec.month:02d}.2026")
        if spec.date is None and spec.year is None:
            raise SpecError("usd_rate needs date or year")
        return spec
    if kind == "registry_count":
        entity = _enum(d.get("entity"), REGISTRY_ENTITIES, "entity")
        role = _opt_enum(d, "role", ROLES)
        vat = d.get("vat_payer")
        year = _opt_int(d, "registered_year")
        _check_year(year)
        return RegistryCount(entity, role, None if vat is None else bool(vat), d.get("region") or None, year)
    raise SpecError(f"unknown kind {kind!r}; use deals_aggregate, deal_lookup, contract_lookup, "
                    "usd_rate or registry_count")


def entity_values(spec: Spec) -> set[str]:
    """Values that must have been returned by resolve() in this request."""
    if isinstance(spec, DealsAggregate):
        f = spec.filters
        values = set(f.products + f.regions + f.buyer_inns + f.seller_inns + f.participant_inns
                     + f.seller_brokers + f.buyer_brokers + f.brokers)
        if f.product_family:
            values.add(f.product_family)
        return values
    if isinstance(spec, RegistryCount) and spec.region:
        return {spec.region}
    return set()
