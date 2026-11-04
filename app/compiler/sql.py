"""Compile validated specs into SQL from fixed templates. All SQL here is reviewed and unit-tested."""
from dataclasses import dataclass, field

from app.compiler.spec import (ContractLookup, DealLookup, DealsAggregate, Filters, RegistryCount, Spec,
                               UsdRate)

_VALID = " FILTER (WHERE NOT d.is_cancelled)"
_MASS = " FILTER (WHERE d.volume_tonnes IS NOT NULL)"
_MASS_VALID = " FILTER (WHERE d.volume_tonnes IS NOT NULL AND NOT d.is_cancelled)"

# measure -> (expression template, alias). {f} = optional status FILTER clause; {n} = FILTER for money that
# must only count mass deals (volume_tonnes known), combined with the status filter.
MEASURE_SQL = {
    "deal_count": ("COUNT(*){f}", "deals"),
    "amount_uzs": ("SUM(d.amount_uzs){f}", "total_uzs"),
    "amount_usd": ("SUM(d.amount_usd){f}", "total_usd"),
    "volume_tonnes": ("SUM(d.volume_tonnes){f}", "volume_t"),
    "volume": ("SUM(d.volume){f}", "volume"),
    "avg_amount_uzs": ("AVG(d.amount_uzs){f}", "avg_amount_uzs"),
    "avg_price_uzs_per_kg": ("SUM(d.amount_uzs){n} / NULLIF(SUM(d.volume_tonnes){f} * 1000, 0)",
                             "avg_price_uzs_per_kg"),
    "max_amount_uzs": ("MAX(d.amount_uzs){f}", "max_amount_uzs"),
    "distinct_buyers": ("COUNT(DISTINCT d.buyer_inn){f}", "buyers"),
    "distinct_sellers": ("COUNT(DISTINCT d.seller_inn){f}", "sellers"),
    "lots_bought": ("SUM(d.quantity){f}", "lots_bought"),
    "lots_delivered": ("SUM(d.delivered_lots){f}", "lots_delivered"),
    "cancelled_pct": ("ROUND(100.0 * COUNT(*) FILTER (WHERE d.is_cancelled) / COUNT(*), 2)", "cancelled_pct"),
}


@dataclass(frozen=True)
class Dim:
    select: tuple[str, ...]   # "expr AS alias"
    group: tuple[str, ...]
    join: str = ""
    order_asc: bool = False   # time series: order by key ascending


DIMS = {
    "buyer": Dim(("d.buyer_inn AS buyer_inn", "pb.name AS buyer_name"), ("d.buyer_inn", "pb.name"),
                 "LEFT JOIN participants pb ON pb.inn = d.buyer_inn"),
    "seller": Dim(("d.seller_inn AS seller_inn", "ps.name AS seller_name"), ("d.seller_inn", "ps.name"),
                  "LEFT JOIN participants ps ON ps.inn = d.seller_inn"),
    "seller_broker": Dim(("d.seller_broker AS seller_broker", "bs.name AS seller_broker_name"),
                         ("d.seller_broker", "bs.name"), "LEFT JOIN brokers bs ON bs.broker_code = d.seller_broker"),
    "buyer_broker": Dim(("d.buyer_broker AS buyer_broker", "bb.name AS buyer_broker_name"),
                        ("d.buyer_broker", "bb.name"), "LEFT JOIN brokers bb ON bb.broker_code = d.buyer_broker"),
    "broker": Dim(("d.broker_code AS broker_code", "bk.name AS broker_name"), ("d.broker_code", "bk.name"),
                  "LEFT JOIN brokers bk ON bk.broker_code = d.broker_code"),
    "region": Dim(("d.region AS region",), ("d.region",)),
    "product": Dim(("d.product_canonical AS product",), ("d.product_canonical",)),
    "product_family": Dim(("d.product_family AS product_family",), ("d.product_family",)),
    "month": Dim(("d.year AS year", "d.month AS month"), ("d.year", "d.month"), order_asc=True),
    "year": Dim(("d.year AS year",), ("d.year",), order_asc=True),
    "contract_type": Dim(("d.contract_type AS contract_type",), ("d.contract_type",)),
    "contract_form": Dim(("d.contract_form AS contract_form",), ("d.contract_form",)),
    "trade_type": Dim(("d.trade_type AS trade_type",), ("d.trade_type",)),
    "status": Dim(("d.status AS status",), ("d.status",)),
    "unit": Dim(("d.unit AS unit",), ("d.unit",)),
    "contract": Dim(("d.contract_id AS contract_id",), ("d.contract_id",)),
}
_UNIT = Dim(("d.unit AS unit",), ("d.unit",))


@dataclass
class Compiled:
    sql: str
    splits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(lit(v) for v in values)})"


def where_clauses(f: Filters, a: str = "d") -> list[str]:
    w = []
    if f.year is not None:
        w.append(f"{a}.year = {int(f.year)}")
    if f.month_from is not None:
        w.append(f"{a}.month BETWEEN {int(f.month_from)} AND {int(f.month_to)}")
    if f.date_from is not None:
        w.append(f"{a}.deal_date >= DATE {lit(f.date_from.isoformat())}")
    if f.date_to is not None:
        w.append(f"{a}.deal_date <= DATE {lit(f.date_to.isoformat())}")
    if f.products:
        w.append(_in(f"{a}.product_canonical", f.products))
    if f.product_family:
        w.append(f"{a}.product_family = {lit(f.product_family)}")
    if f.regions:
        w.append(_in(f"{a}.region", f.regions))
    if f.buyer_inns:
        w.append(_in(f"{a}.buyer_inn", f.buyer_inns))
    if f.seller_inns:
        w.append(_in(f"{a}.seller_inn", f.seller_inns))
    if f.participant_inns:
        w.append(f"({_in(f'{a}.buyer_inn', f.participant_inns)} OR {_in(f'{a}.seller_inn', f.participant_inns)})")
    if f.seller_brokers:
        w.append(_in(f"{a}.seller_broker", f.seller_brokers))
    if f.buyer_brokers:
        w.append(_in(f"{a}.buyer_broker", f.buyer_brokers))
    if f.brokers:
        w.append(f"({_in(f'{a}.seller_broker', f.brokers)} OR {_in(f'{a}.buyer_broker', f.brokers)})")
    if f.contract_id is not None:
        w.append(f"{a}.contract_id = {int(f.contract_id)}")
    if f.contract_type:
        w.append(f"{a}.contract_type = {lit(f.contract_type)}")
    if f.contract_form:
        w.append(f"{a}.contract_form = {lit(f.contract_form)}")
    if f.trade_type:
        w.append(f"{a}.trade_type = {lit(f.trade_type)}")
    if f.statuses:
        w.append(_in(f"{a}.status", f.statuses))
    return w


def _expr(measure: str, valid: bool) -> str:
    expr = MEASURE_SQL[measure][0]
    if measure == "cancelled_pct":
        return expr
    return expr.format(f=_VALID if valid else "", n=_MASS_VALID if valid else _MASS)


def _measure_cols(measures: list[str], split: bool, valid_filter: bool = False) -> list[str]:
    cols = []
    for m in measures:
        alias = MEASURE_SQL[m][1]
        if valid_filter:         # valid scope without a WHERE: cancelled_pct needs the cancelled rows
            cols.append(f"{_expr(m, m != 'cancelled_pct')} AS {alias}")
        elif split and m != "cancelled_pct":
            cols.append(f"{_expr(m, False)} AS {alias}_all")
            cols.append(f"{_expr(m, True)} AS {alias}_valid")
        else:
            cols.append(f"{_expr(m, False)} AS {alias}")
    return cols


def _currency_notes(measures: list[str]) -> list[str]:
    notes = []
    if any(m in ("amount_uzs", "avg_amount_uzs", "avg_price_uzs_per_kg", "max_amount_uzs") for m in measures):
        notes.append("uzs_converted")
    if "amount_usd" in measures:
        notes.append("usd_converted")
    return notes


def _compile_aggregate(s: DealsAggregate) -> Compiled:
    notes = _currency_notes(s.measures)
    splits = []
    if "amount_uzs" in s.measures and "amount_usd" in s.measures:
        splits.append("currency")
    where = where_clauses(s.filters)
    grouped = s.group_by is not None
    scope = s.status_scope
    if grouped and scope == "split":
        scope = "valid"          # rankings are computed on deals that took place
    if s.filters.statuses:
        scope = "all"            # explicit status filter overrides the default
    if scope == "valid" and s.measures == ["cancelled_pct"]:
        scope = "all"            # nothing else to restrict; the share needs every deal
    valid_filter = scope == "valid" and "cancelled_pct" in s.measures
    if scope == "valid":
        if not valid_filter:
            where.append("NOT d.is_cancelled")
        notes.append("excluded_cancelled")
    elif scope == "all":
        notes.append("all_statuses")
    else:
        notes.append("status_split")
        splits.append("status_scope")
    split_cols = scope == "split"
    dims: list[Dim] = []
    if grouped and s.group_by != "participant":
        dims.append(DIMS[s.group_by])
    if "volume" in s.measures:
        dims.append(_UNIT)
        notes.append("volume_by_unit")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    measure_cols = _measure_cols(s.measures, split_cols, valid_filter)

    per_unit = grouped and "volume" in s.measures
    if s.group_by == "participant":
        splits.append("role")
        notes.append("role_split")
        unit_sel = list(_UNIT.select) if dims else []
        unit_grp = list(_UNIT.group) if dims else []
        two = len(s.measures) == 2
        if two:
            splits.append("measure")
        parts = []
        for role, dim in (("buyer", "buyer_inn"), ("seller", "seller_inn")):
            alias = "pb" if role == "buyer" else "ps"
            for m in s.measures if two else s.measures[:1]:
                rank = _rank_alias(m, split_cols)
                head = [f"'{role}' AS role"] + ([f"'by_{rank}' AS ranking"] if two else [])
                parts.append(_ranked_select(
                    head + [f"d.{dim} AS inn", f"{alias}.name AS name"] + unit_sel + measure_cols,
                    f"FROM deals d LEFT JOIN participants {alias} ON {alias}.inn = d.{dim}{where_sql}",
                    [f"d.{dim}", f"{alias}.name"] + unit_grp, s, m, split_cols, per_unit, valid_filter))
        notes.append(f"ranked_top_{s.top}")
        return Compiled(" UNION ALL ".join(parts), splits, notes)

    if not dims:
        return Compiled(f"SELECT {', '.join(measure_cols)} FROM deals d{where_sql}", splits, notes)

    source = "FROM deals d"
    if s.group_by == "broker":
        notes.append("broker_both_sides")
        inner = where_clauses(s.filters, "x") + (["NOT x.is_cancelled"] if scope == "valid" and not valid_filter else [])
        inner_where = (" WHERE " + " AND ".join(inner)) if inner else ""
        # UNION (not ALL): x.* carries deal_id, so a deal with the same broker on both sides collapses to one row
        source = ("FROM (SELECT x.*, x.seller_broker AS broker_code FROM deals x" + inner_where +
                  " UNION SELECT x.*, x.buyer_broker AS broker_code FROM deals x" + inner_where + ") d")
        where_sql = ""
    joins = " ".join(dim.join for dim in dims if dim.join)
    select = [c for dim in dims for c in dim.select]
    group = [g for dim in dims for g in dim.group]
    from_sql = " ".join(p for p in (source, joins, where_sql.strip()) if p)

    if dims[0].order_asc and s.top is None:
        sql = (f"SELECT {', '.join(select + measure_cols)} {from_sql} GROUP BY {', '.join(group)} "
               f"ORDER BY {', '.join(group)}")
        return Compiled(sql, splits, notes)

    if not grouped:          # volume-by-unit only
        sql = f"SELECT {', '.join(select + measure_cols)} {from_sql} GROUP BY {', '.join(group)} ORDER BY 1"
        return Compiled(sql, splits, notes)

    notes.append(f"ranked_top_{s.top}")
    if len(s.measures) == 2:
        splits.append("measure")
        parts = []
        for m in s.measures:
            rank = _rank_alias(m, split_cols)
            parts.append(_ranked_select([f"'by_{rank}' AS ranking"] + select + measure_cols, from_sql, group, s,
                                        m, split_cols, per_unit, valid_filter))
        return Compiled(" UNION ALL ".join(parts), splits, notes)
    sql = _ranked_select(select + measure_cols, from_sql, group, s, s.measures[0], split_cols, per_unit, valid_filter)
    return Compiled(sql, splits, notes)


def _rank_alias(measure: str, split: bool) -> str:
    alias = MEASURE_SQL[measure][1]
    return f"{alias}_valid" if split and measure != "cancelled_pct" else alias


def _ranked_select(cols: list[str], from_sql: str, group: list[str], s: DealsAggregate, measure: str,
                   split: bool, per_unit: bool, valid_filter: bool = False) -> str:
    rank_alias = _rank_alias(measure, split)
    tie = ", ".join(group)           # group keys break ties deterministically
    base = f"SELECT {', '.join(cols)} {from_sql} GROUP BY {', '.join(group)}"
    if per_unit:
        # volumes of different units are not comparable: rank within each unit
        rank_expr = _expr(measure, (split or valid_filter) and measure != "cancelled_pct")
        return (f"({base} QUALIFY ROW_NUMBER() OVER (PARTITION BY d.unit ORDER BY {rank_expr} DESC NULLS LAST, "
                f"{tie}) <= {int(s.top)} ORDER BY d.unit, {rank_alias} DESC NULLS LAST, {tie})")
    return f"({base} ORDER BY {rank_alias} DESC NULLS LAST, {tie} LIMIT {int(s.top)})"


def _compile_deal(s: DealLookup) -> Compiled:
    return Compiled(f"""SELECT d.deal_id, d.deal_date, d.deal_time, d.product_name_full, d.product_canonical,
       d.quantity, d.lot, d.unit, d.volume, d.amount, d.currency, d.amount_uzs, d.start_price, d.status,
       d.contract_id, d.contract_type, d.contract_form, d.trade_type, d.region,
       d.seller_inn, ps.name AS seller_name, d.buyer_inn, pb.name AS buyer_name,
       d.seller_broker, bs.name AS seller_broker_name, d.buyer_broker, bb.name AS buyer_broker_name,
       d.payment_date, d.delivery_date, d.delivered_lots
FROM deals d
LEFT JOIN participants ps ON ps.inn = d.seller_inn LEFT JOIN participants pb ON pb.inn = d.buyer_inn
LEFT JOIN brokers bs ON bs.broker_code = d.seller_broker LEFT JOIN brokers bb ON bb.broker_code = d.buyer_broker
WHERE d.deal_id = {int(s.deal_id)}""")


def _compile_contract(s: ContractLookup) -> Compiled:
    cid = int(s.contract_id)
    return Compiled(f"""SELECT c.contract_id, c.product_name, c.product_canonical, c.unit, c.lot, c.currency,
       c.base_price, c.region, c.contract_type, c.contract_form, c.trade_type, c.payment_term_days,
       c.delivery_term_days, c.registered_date, c.valid_until, c.seller_inn, p.name AS seller_name,
       (SELECT COUNT(*) FROM deals d WHERE d.contract_id = {cid}) AS deals_all,
       (SELECT COUNT(*) FROM deals d WHERE d.contract_id = {cid} AND NOT d.is_cancelled) AS deals_valid,
       (SELECT string_agg(DISTINCT d.seller_broker, ', ' ORDER BY d.seller_broker)
          FROM deals d WHERE d.contract_id = {cid}) AS seller_brokers,
       (SELECT string_agg(DISTINCT d.buyer_broker, ', ' ORDER BY d.buyer_broker)
          FROM deals d WHERE d.contract_id = {cid}) AS buyer_brokers
FROM contracts c LEFT JOIN participants p ON p.inn = c.seller_inn
WHERE c.contract_id = {cid}""")


def _compile_rate(s: UsdRate) -> Compiled:
    if s.date is not None:
        return Compiled(f"SELECT rate_date, rate FROM usd_rates WHERE rate_date = DATE {lit(s.date.isoformat())}")
    where = [f"year(rate_date) = {int(s.year)}"]
    if s.month is not None:
        where.append(f"month(rate_date) = {int(s.month)}")
    return Compiled("SELECT AVG(rate) AS avg_rate, MIN(rate) AS min_rate, MAX(rate) AS max_rate, "
                    f"COUNT(*) AS days FROM usd_rates WHERE {' AND '.join(where)}")


def _compile_registry(s: RegistryCount) -> Compiled:
    where = []
    if s.entity == "participants":
        if s.role:
            where.append(f"role = {lit(s.role)}")
        if s.vat_payer is not None:
            where.append("vat_payer" if s.vat_payer else "NOT vat_payer")
    if s.region:
        where.append(f"region = {lit(s.region)}")
    if s.entity == "contracts" and s.registered_year is not None:
        where.append(f"year(registered_date) = {int(s.registered_year)}")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    return Compiled(f"SELECT COUNT(*) AS {s.entity} FROM {s.entity}{where_sql}")


def compile_spec(spec: Spec) -> Compiled:
    if isinstance(spec, DealsAggregate):
        return _compile_aggregate(spec)
    if isinstance(spec, DealLookup):
        return _compile_deal(spec)
    if isinstance(spec, ContractLookup):
        return _compile_contract(spec)
    if isinstance(spec, UsdRate):
        return _compile_rate(spec)
    if isinstance(spec, RegistryCount):
        return _compile_registry(spec)
    raise TypeError(f"unsupported spec {type(spec).__name__}")
