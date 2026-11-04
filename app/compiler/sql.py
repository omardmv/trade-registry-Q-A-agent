"""Compile validated specs into SQL from fixed templates. All SQL here is reviewed and unit-tested."""
from dataclasses import dataclass, field, replace

from app.compiler.spec import (BrokerLookup, ContractLookup, DealLookup, DealsAggregate, Filters, ParticipantLookup,
                               RegistryCount, Spec, UsdRate)

# measure -> (expression template, alias). Placeholders are FILTER clauses that combine the status filter (when the
# valid view is computed) with: {f} nothing else; {n} mass deals only (volume_tonnes known, for money that is divided
# by tonnes); {l} late deliveries only. {s} is the share_of condition.
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
    "distinct_products": ("COUNT(DISTINCT d.product_canonical){f}", "products"),
    "distinct_regions": ("COUNT(DISTINCT d.region){f}", "regions"),
    "distinct_seller_brokers": ("COUNT(DISTINCT d.seller_broker){f}", "seller_brokers"),
    "distinct_buyer_brokers": ("COUNT(DISTINCT d.buyer_broker){f}", "buyer_brokers"),
    "distinct_brokers": ("len(list_distinct(flatten(LIST([d.seller_broker, d.buyer_broker]){f})))", "brokers"),
    "share_pct": ("ROUND(100.0 * COUNT(*) FILTER (WHERE {s}) / NULLIF(COUNT(*), 0), 2)", "share_pct"),
    "median_price_uzs_per_kg": ("MEDIAN(d.amount_uzs / (d.volume_tonnes * 1000)){n}", "median_price_uzs_per_kg"),
    "mean_deal_price_uzs_per_t": ("AVG(d.amount_uzs / d.volume_tonnes){n}", "mean_price_uzs_per_t"),
    "min_unit_price_uzs": ("MIN(d.amount_uzs / d.volume){f}", "min_unit_price_uzs"),
    "max_unit_price_uzs": ("MAX(d.amount_uzs / d.volume){f}", "max_unit_price_uzs"),
    "avg_price_change_pct": ("AVG(d.price_vs_start_pct){f}", "avg_price_change_pct"),
    "avg_days_to_payment": ("AVG(d.days_to_payment){f}", "avg_days_to_payment"),
    "avg_days_to_delivery": ("AVG(d.days_to_delivery){f}", "avg_days_to_delivery"),
    "avg_delivery_delay_days": ("AVG(d.delivery_late_days){l}", "avg_delivery_delay_days"),
    "late_delivery_count": ("COUNT(*){l}", "late_deliveries"),
    "late_payment_count": ("COUNT(*) FILTER (WHERE d.payment_late)", "late_payments"),
    "undelivered_lots": ("SUM(d.quantity - COALESCE(d.delivered_lots, 0)){f}", "undelivered_lots"),
    "delivered_volume": ("SUM(d.delivered_lots * d.lot){f}", "delivered_volume"),
    "undelivered_volume": ("SUM((d.quantity - COALESCE(d.delivered_lots, 0)) * d.lot){f}", "undelivered_volume"),
}
# Measures computed over every deal of the filtered scope, whatever the status view (their meaning needs the
# cancelled deals: a share of all deals, the cancelled share, unpaid-after-term deals).
SCOPE_FREE = ("cancelled_pct", "share_pct", "late_payment_count")
# Measures in units of measure: grouped by unit, rankings partitioned by unit.
PER_UNIT = ("volume", "delivered_volume", "undelivered_volume", "min_unit_price_uzs", "max_unit_price_uzs")
_UZS_MEASURES = ("amount_uzs", "avg_amount_uzs", "avg_price_uzs_per_kg", "max_amount_uzs", "median_price_uzs_per_kg",
                 "mean_deal_price_uzs_per_t", "min_unit_price_uzs", "max_unit_price_uzs")


@dataclass(frozen=True)
class Dim:
    select: tuple[str, ...]   # "expr AS alias"
    group: tuple[str, ...]
    join: str = ""
    order_asc: bool = False   # time series: order by key ascending


def _col(column: str, alias: str | None = None) -> Dim:
    return Dim((f"d.{column} AS {alias or column}",), (f"d.{column}",))


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
    "region": _col("region"),
    "product": _col("product_canonical", "product"),
    "product_family": _col("product_family"),
    "month": Dim(("d.year AS year", "d.month AS month"), ("d.year", "d.month"), order_asc=True),
    "year": Dim(("d.year AS year",), ("d.year",), order_asc=True),
    "quarter": Dim(("d.year AS year", "d.quarter AS quarter"), ("d.year", "d.quarter"), order_asc=True),
    "hour": Dim(("d.deal_hour AS hour",), ("d.deal_hour",), order_asc=True),
    "contract_type": _col("contract_type"),
    "contract_form": _col("contract_form"),
    "trade_type": _col("trade_type"),
    "status": _col("status"),
    "unit": _col("unit"),
    "contract": _col("contract_id"),
    "deal": Dim(("d.deal_id AS deal_id", "d.deal_date AS deal_date", "d.product_canonical AS product",
                 "d.seller_inn AS seller_inn", "ps.name AS seller_name", "d.buyer_inn AS buyer_inn",
                 "pb.name AS buyer_name", "d.unit_price AS unit_price", "d.currency AS currency", "d.unit AS unit"),
                ("d.deal_id", "d.deal_date", "d.product_canonical", "d.seller_inn", "ps.name", "d.buyer_inn",
                 "pb.name", "d.unit_price", "d.currency", "d.unit"),
                "LEFT JOIN participants ps ON ps.inn = d.seller_inn LEFT JOIN participants pb ON pb.inn = d.buyer_inn"),
    "category": _col("category"),
    "industry": _col("industry"),
    "product_group": _col("product_group"),
    "delivery_basis": _col("delivery_basis"),
    "packaging": _col("packaging"),
    "hs_code": _col("hs_code"),
    "product_variant": _col("product_name_full", "product_variant"),
    "buyer_region": _col("buyer_region"),
    "seller_region": _col("seller_region"),
    "buyer_activity": _col("buyer_activity"),
    "seller_activity": _col("seller_activity"),
}
_UNIT = Dim(("d.unit AS unit",), ("d.unit",))

# list filter -> deals column (plain IN)
_LIST_FILTERS = (("products", "product_canonical"), ("product_variants", "product_name_full"), ("regions", "region"),
                 ("buyer_inns", "buyer_inn"), ("seller_inns", "seller_inn"), ("seller_brokers", "seller_broker"),
                 ("buyer_brokers", "buyer_broker"), ("statuses", "status"), ("categories", "category"),
                 ("industries", "industry"), ("product_groups", "product_group"),
                 ("delivery_bases", "delivery_basis"), ("packagings", "packaging"), ("hs_codes", "hs_code"),
                 ("buyer_regions", "buyer_region"), ("seller_regions", "seller_region"),
                 ("buyer_activities", "buyer_activity"), ("seller_activities", "seller_activity"),
                 ("units", "unit"))


@dataclass
class Compiled:
    sql: str
    splits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _in(column: str, values) -> str:
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
    if f.product_family:
        w.append(f"{a}.product_family = {lit(f.product_family)}")
    if f.participant_inns:
        w.append(f"({_in(f'{a}.buyer_inn', f.participant_inns)} OR {_in(f'{a}.seller_inn', f.participant_inns)})")
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
    for key, column in _LIST_FILTERS:
        values = getattr(f, key)
        if values:
            w.append(_in(f"{a}.{column}", values))
    return w


def _share_condition(s: DealsAggregate) -> str:
    if s.share_of is None:
        return "FALSE"
    return _in(f"d.{s.share_of.field}", s.share_of.values)


def _filter_clause(conds: list[str]) -> str:
    return f" FILTER (WHERE {' AND '.join(conds)})" if conds else ""


_SHARE_BASIS_COL = {"amount_uzs": "d.amount_uzs", "volume_t": "d.volume_tonnes"}
# percent measures: a change between periods is a difference in percentage points
PERCENT_POINT_MEASURES = ("share_pct", "cancelled_pct", "avg_price_change_pct")


def _expr(measure: str, valid: bool, s: DealsAggregate | None = None) -> str:
    expr = MEASURE_SQL[measure][0]
    if measure == "share_pct" and s is not None and s.share_of is not None and s.share_of.basis != "deals":
        # money / tonnes shares are over deals that took place, unless a status filter defines the rows
        col = _SHARE_BASIS_COL[s.share_of.basis]
        conds = [] if s.filters.statuses else ["NOT d.is_cancelled"]
        num = _filter_clause([_share_condition(s)] + conds)
        return f"ROUND(100.0 * SUM({col}){num} / NULLIF(SUM({col}){_filter_clause(conds)}, 0), 2)"
    status = [] if measure in SCOPE_FREE or not valid else ["NOT d.is_cancelled"]
    return expr.format(f=_filter_clause(status), n=_filter_clause(["d.volume_tonnes IS NOT NULL"] + status),
                       l=_filter_clause(["d.delivery_late_days > 0"] + status),
                       s=_share_condition(s) if s is not None else "FALSE")


def _measure_pairs(s: DealsAggregate, split: bool, valid_filter: bool = False) -> list[tuple[str, str]]:
    """(expression, alias) of every output measure column."""
    out = []
    for m in s.measures:
        alias = MEASURE_SQL[m][1]
        if valid_filter:         # valid scope without a WHERE: scope-free measures need the cancelled rows
            out.append((_expr(m, True, s), alias))
        elif split and m not in SCOPE_FREE:
            out.append((_expr(m, False, s), f"{alias}_all"))
            out.append((_expr(m, True, s), f"{alias}_valid"))
        else:
            out.append((_expr(m, False, s), alias))
    return out


def _measure_cols(s: DealsAggregate, split: bool, valid_filter: bool = False) -> list[str]:
    return [f"{e} AS {a}" for e, a in _measure_pairs(s, split, valid_filter)]


def _currency_notes(measures: list[str]) -> list[str]:
    notes = []
    if any(m in _UZS_MEASURES for m in measures):
        notes.append("uzs_converted")
    if "amount_usd" in measures:
        notes.append("usd_converted")
    return notes


def _scope(s: DealsAggregate) -> str:
    scope = s.status_scope
    if s.group_by not in (None, "broker_side") and scope == "split":
        scope = "valid"          # rankings are computed on deals that took place
    if s.filters.statuses:
        scope = "all"            # explicit status filter overrides the default
    if scope != "all" and all(m in SCOPE_FREE for m in s.measures):
        scope = "all"            # nothing else to restrict or split; the share needs every deal
    return scope


def _compile_aggregate(s: DealsAggregate) -> Compiled:
    notes = _currency_notes(s.measures)
    splits = []
    if "amount_uzs" in s.measures and "amount_usd" in s.measures:
        splits.append("currency")
    if "avg_price_uzs_per_kg" in s.measures and "mean_deal_price_uzs_per_t" in s.measures:
        splits.append("price_average")
    where = where_clauses(s.filters)
    grouped = s.group_by is not None
    scope = _scope(s)
    valid_filter = scope == "valid" and any(m in SCOPE_FREE for m in s.measures)
    if scope == "valid":
        if not valid_filter:
            where.append("NOT d.is_cancelled")
        notes.append("excluded_cancelled")
    elif scope == "all":
        notes.append("all_statuses")
    else:
        notes.append("status_split")
        splits.append("status_scope")
    if s.order == "asc":
        notes.append("order_asc")
    split_cols = scope == "split"
    dims: list[Dim] = []
    if grouped and s.group_by not in ("participant", "broker_side"):
        dims.append(DIMS[s.group_by])
    per_unit_measures = [m for m in s.measures if m in PER_UNIT]
    if per_unit_measures:
        if not any(sel.endswith(" AS unit") for dim in dims for sel in dim.select):
            dims.append(_UNIT)
        notes.append("volume_by_unit")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    measure_cols = _measure_cols(s, split_cols, valid_filter)

    if s.compare_periods:
        notes.append("compare_periods")
        return Compiled(_compare_periods_sql(s, scope, valid_filter, dims), splits, notes)

    if s.group_by == "broker_side":
        notes.append("broker_side_split")
        return Compiled(_broker_side_sql(s, scope, valid_filter, split_cols, measure_cols), splits, notes)

    per_unit = grouped and bool(per_unit_measures)
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

    if not grouped:          # per-unit measures only
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


def _broker_side_sql(s: DealsAggregate, scope: str, valid_filter: bool, split: bool, measure_cols: list[str]) -> str:
    """The filtered brokers counted separately as seller broker and as buyer broker (a deal with the broker on both
    sides appears in both rows)."""
    unit = any(m in PER_UNIT for m in s.measures)
    parts = []
    for side, col, alias in (("seller", "seller_broker", "bs"), ("buyer", "buyer_broker", "bb")):
        f = replace(s.filters, brokers=[], **{f"{col}s": list(s.filters.brokers)})
        where = where_clauses(f)
        if scope == "valid" and not valid_filter:
            where.append("NOT d.is_cancelled")
        sel = [f"'{side}' AS side", f"d.{col} AS broker_code", f"{alias}.name AS broker_name"]
        grp = [f"d.{col}", f"{alias}.name"]
        if unit:
            sel.append("d.unit AS unit")
            grp.append("d.unit")
        parts.append(f"(SELECT {', '.join(sel + measure_cols)} FROM deals d LEFT JOIN brokers {alias} "
                     f"ON {alias}.broker_code = d.{col} WHERE {' AND '.join(where)} GROUP BY {', '.join(grp)} "
                     f"ORDER BY {', '.join(grp)})")
    return " UNION ALL ".join(parts)


def _compare_periods_sql(s: DealsAggregate, scope: str, valid_filter: bool, dims: list[Dim]) -> str:
    """One row per period (and unit for per-unit measures) plus the change versus the first period, in %."""
    pairs = _measure_pairs(s, scope == "split", valid_filter)
    unit = bool(dims)                      # only the unit dimension is possible here (no group_by)
    branches = []
    for i, p in enumerate(s.compare_periods, 1):
        where = where_clauses(replace(s.filters, year=p.year, month_from=p.month_from, month_to=p.month_to))
        if scope == "valid" and not valid_filter:
            where.append("NOT d.is_cancelled")
        mf = "CAST(NULL AS INTEGER)" if p.month_from is None else str(int(p.month_from))
        mt = "CAST(NULL AS INTEGER)" if p.month_to is None else str(int(p.month_to))
        cols = [f"{i} AS period_no", f"{int(p.year)} AS year", f"{mf} AS month_from", f"{mt} AS month_to"]
        cols += (["d.unit AS unit"] if unit else []) + [f"{e} AS {a}" for e, a in pairs]
        branch = f"SELECT {', '.join(cols)} FROM deals d WHERE {' AND '.join(where)}"
        if unit:
            branch += " GROUP BY d.unit"
        branches.append(f"({branch})")
    aliases = [a for _, a in pairs]
    points = {MEASURE_SQL[m][1] for m in PERCENT_POINT_MEASURES}
    window = "PARTITION BY unit ORDER BY period_no" if unit else "ORDER BY period_no"
    change = []
    for a in aliases:
        if a in points:      # a share changes by percentage points, not by a percent of itself
            name = "change_pp" if len(aliases) == 1 else f"{a}_change_pp"
            change.append(f"ROUND({a} - LAG({a}) OVER ({window}), 2) AS {name}")
        else:
            name = "change_pct" if len(aliases) == 1 else f"{a}_change_pct"
            change.append(f"ROUND(100.0 * ({a} / NULLIF(LAG({a}) OVER ({window}), 0) - 1), 2) AS {name}")
    head = ["year", "month_from", "month_to"] + (["unit"] if unit else []) + aliases + change
    order = "period_no, unit" if unit else "period_no"
    return f"SELECT {', '.join(head)} FROM ({' UNION ALL '.join(branches)}) p ORDER BY {order}"


def _rank_alias(measure: str, split: bool) -> str:
    alias = MEASURE_SQL[measure][1]
    return f"{alias}_valid" if split and measure not in SCOPE_FREE else alias


def _ranked_select(cols: list[str], from_sql: str, group: list[str], s: DealsAggregate, measure: str,
                   split: bool, per_unit: bool, valid_filter: bool = False) -> str:
    rank_alias = _rank_alias(measure, split)
    direction = "ASC" if s.order == "asc" else "DESC"
    tie = ", ".join(group)           # group keys break ties deterministically
    base = f"SELECT {', '.join(cols)} {from_sql} GROUP BY {', '.join(group)}"
    if valid_filter and measure not in SCOPE_FREE:
        # valid view without a WHERE (a scope-free measure needs the cancelled rows): a group whose deals were all
        # cancelled has no valid value and must not enter the ranking (it would win an ascending one)
        base += " HAVING COUNT(*) FILTER (WHERE NOT d.is_cancelled) > 0"
    if per_unit:
        # volumes of different units are not comparable: rank within each unit
        rank_expr = _expr(measure, (split or valid_filter) and measure not in SCOPE_FREE, s)
        return (f"({base} QUALIFY ROW_NUMBER() OVER (PARTITION BY d.unit ORDER BY {rank_expr} {direction} NULLS LAST, "
                f"{tie}) <= {int(s.top)} ORDER BY d.unit, {rank_alias} {direction} NULLS LAST, {tie})")
    return f"({base} ORDER BY {rank_alias} {direction} NULLS LAST, {tie} LIMIT {int(s.top)})"


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
    return Compiled(f"""SELECT c.contract_id, c.product_name, c.product_canonical, c.product_family,
       c.brand, c.size, c.grade, c.product_type, c.product_class, c.hs_code, c.product_group, c.category, c.industry,
       c.unit, c.lot, c.currency, c.base_price, c.deposit_pct, c.delivery_basis, c.packaging,
       c.region, c.warehouse_address, c.contract_type, c.contract_form, c.trade_type, c.payment_term_days,
       c.delivery_term_days, c.registered_date, c.valid_until, c.seller_inn, p.name AS seller_name,
       (SELECT COUNT(*) FROM deals d WHERE d.contract_id = {cid}) AS deals_all,
       (SELECT COUNT(*) FROM deals d WHERE d.contract_id = {cid} AND NOT d.is_cancelled) AS deals_valid,
       (SELECT string_agg(DISTINCT d.seller_broker, ', ' ORDER BY d.seller_broker)
          FROM deals d WHERE d.contract_id = {cid}) AS seller_brokers,
       (SELECT string_agg(DISTINCT d.buyer_broker, ', ' ORDER BY d.buyer_broker)
          FROM deals d WHERE d.contract_id = {cid}) AS buyer_brokers
FROM contracts c LEFT JOIN participants p ON p.inn = c.seller_inn
WHERE c.contract_id = {cid}""")


def _compile_participant(s: ParticipantLookup) -> Compiled:
    inn = lit(s.inn)
    return Compiled(f"""SELECT p.inn, p.name, p.region, p.activity, p.role, p.vat_payer,
       p.deals_as_buyer, p.deals_as_seller,
       (SELECT COUNT(*) FROM deals d WHERE d.buyer_inn = {inn} AND NOT d.is_cancelled) AS deals_as_buyer_valid,
       (SELECT COUNT(*) FROM deals d WHERE d.seller_inn = {inn} AND NOT d.is_cancelled) AS deals_as_seller_valid,
       (SELECT SUM(d.amount_uzs) FROM deals d WHERE d.buyer_inn = {inn} AND NOT d.is_cancelled) AS bought_uzs_valid,
       (SELECT SUM(d.amount_uzs) FROM deals d WHERE d.seller_inn = {inn} AND NOT d.is_cancelled) AS sold_uzs_valid,
       (SELECT MIN(d.deal_date) FROM deals d WHERE d.buyer_inn = {inn} OR d.seller_inn = {inn}) AS first_deal_date,
       (SELECT MAX(d.deal_date) FROM deals d WHERE d.buyer_inn = {inn} OR d.seller_inn = {inn}) AS last_deal_date
FROM participants p WHERE p.inn = {inn}""")


def _compile_broker(s: BrokerLookup) -> Compiled:
    code = lit(s.broker_code)
    return Compiled(f"""SELECT b.broker_code, b.name, b.inn, b.region, b.deals_as_seller_broker, b.deals_as_buyer_broker,
       (SELECT COUNT(*) FROM deals d WHERE d.seller_broker = {code} AND NOT d.is_cancelled)
          AS deals_as_seller_broker_valid,
       (SELECT COUNT(*) FROM deals d WHERE d.buyer_broker = {code} AND NOT d.is_cancelled)
          AS deals_as_buyer_broker_valid,
       (SELECT COUNT(*) FROM deals d WHERE d.seller_broker = {code} OR d.buyer_broker = {code}) AS deals_either_side
FROM brokers b WHERE b.broker_code = {code}""")


def _compile_rate(s: UsdRate) -> Compiled:
    if s.date is not None:
        return Compiled(f"SELECT rate_date, rate FROM usd_rates WHERE rate_date = DATE {lit(s.date.isoformat())}")
    where = [f"year(rate_date) = {int(s.year)}"]
    if s.month is not None:
        where.append(f"month(rate_date) = {int(s.month)}")
    w = " AND ".join(where)
    if s.extreme is not None:
        fn = "MIN" if s.extreme == "min" else "MAX"
        return Compiled(f"SELECT rate_date, rate FROM usd_rates WHERE {w} "
                        f"AND rate = (SELECT {fn}(rate) FROM usd_rates WHERE {w}) ORDER BY rate_date")
    return Compiled("SELECT AVG(rate) AS avg_rate, MIN(rate) AS min_rate, MAX(rate) AS max_rate, "
                    f"COUNT(*) AS days FROM usd_rates WHERE {w}")


def _compile_registry(s: RegistryCount) -> Compiled:
    where = []
    if s.entity == "participants":
        if s.role:
            where.append(f"role = {lit(s.role)}")
        if s.vat_payer is not None:
            where.append("vat_payer" if s.vat_payer else "NOT vat_payer")
        if s.activity:
            where.append(f"activity = {lit(s.activity)}")
    if s.region:
        where.append(f"region = {lit(s.region)}")
    if s.entity == "contracts":
        if s.registered_year is not None:
            where.append(f"year(registered_date) = {int(s.registered_year)}")
        if s.products:
            where.append(_in("product_canonical", s.products))
        if s.contract_form:
            where.append(f"contract_form = {lit(s.contract_form)}")
        if s.seller_inn:
            where.append(f"seller_inn = {lit(s.seller_inn)}")
        if s.active_on is not None:
            d = lit(s.active_on.isoformat())
            where.append(f"registered_date <= DATE {d} AND valid_until >= DATE {d}")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    return Compiled(f"SELECT COUNT(*) AS {s.entity} FROM {s.entity}{where_sql}")


def compile_spec(spec: Spec) -> Compiled:
    if isinstance(spec, DealsAggregate):
        return _compile_aggregate(spec)
    if isinstance(spec, DealLookup):
        return _compile_deal(spec)
    if isinstance(spec, ContractLookup):
        return _compile_contract(spec)
    if isinstance(spec, ParticipantLookup):
        return _compile_participant(spec)
    if isinstance(spec, BrokerLookup):
        return _compile_broker(spec)
    if isinstance(spec, UsdRate):
        return _compile_rate(spec)
    if isinstance(spec, RegistryCount):
        return _compile_registry(spec)
    raise TypeError(f"unsupported spec {type(spec).__name__}")
