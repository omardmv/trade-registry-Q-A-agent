"""Semantic layer: a question becomes a small Spec; one compiler turns any Spec into SQL; one renderer answers.

Spec = metric (+stat / target) + optional group_by + order/limit + structured filters + modifiers (share, compare, usd...).
Metrics and dimensions are defined once here, so new combinations ("average cement price per region in 2025",
"top-3 buyers of sugar in Samarkand", "deals per month for wheat") need no new code.

Two parsers fill a Spec: rule_spec (keywords -> fields) and llm_spec (LLM returns JSON, code validates every field).
Entities (products, regions, INN, brokers, periods) always come from the rule extractor and are verified against data.
"""
import json, re, urllib.request
from dataclasses import dataclass, field
from . import core as C
from .entities import product_sql, PRODUCTS, DATA_START, DATA_END

ANNUL = "Аннулирован(Не оплачен)"
STATS = ("avg", "median", "min", "max", "stddev", "sum")
METRICS = ("lots", "price_change", "count", "amount", "volume", "avg_price", "price_stat", "amount_stat", "distinct", "delay", "delivered",
           "undelivered", "deal", "rate", "company", "broker_info", "directory", "both_roles")
DIMS = ("hour", "product_group", "product_category", "industry", "delivery_basis", "packaging", "tnved", "side", "region", "seller", "buyer", "seller_broker", "buyer_broker", "any_broker", "product", "month", "year",
        "quarter", "status", "contract_form", "contract_type", "auction_type", "buyer_region", "seller_region")
DISTINCT_OF = ("seller", "buyer", "company", "broker", "product", "region")
DELAY_OF = ("payment", "delivery", "delivery_after_payment")


@dataclass
class Spec:
    metric: str = "count"
    stat: str | None = None          # for price_stat / amount_stat / delay
    of: str | None = None            # distinct target / delay kind
    group_by: str | None = None
    order: str = "desc"
    limit: int | None = None
    usd: bool = False
    tonnes: bool = False
    unit: str | None = None
    share: bool = False              # numerator = condition filters (status/type/form), denominator = the rest
    compare: str | None = None       # region | product | year : values come from entities
    same_company: bool = False
    same_broker: bool = False
    source: str = "rules"


# ------------------------------------------------------------------ filters (structured)
@dataclass
class Filters:
    where: list = field(default_factory=list)     # entity filters (period, product, region, inn, broker)
    cond: list = field(default_factory=list)      # condition filters (status, contract type/form, auction)
    notes: list = field(default_factory=list)
    has_status: bool = False
    side_entity: tuple | None = None


def parse_filters(t, e, spec):
    """Entity filters from the verified extractor; condition filters from keywords."""
    F = Filters()
    if e.date_from and not (spec.compare == "year"):
        F.where.append(f"deal_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'"); F.notes.append(f"период {e.period_label}")
    if e.products and spec.compare != "product":
        conds, k, nar = [], 0, []
        for pr in e.products:
            bases = [b for (b,) in C.q(f"SELECT DISTINCT product_base FROM deals_enriched WHERE {product_sql(pr)}")]
            nb = C.narrow(t, pr, bases)
            if nb: conds.append("product_base IN (" + ",".join(C.sqlq(b) for b in nb) + ")"); k += len(nb); nar += nb
            else: conds.append(product_sql(pr)); k += len(bases)
        F.where.append("(" + " OR ".join(conds) + ")")
        F.notes.append(("товар: " + "; ".join(nar) + " (по указанному сорту/марке)") if nar else
                       ("товар: " + ", ".join(e.products) + f" ({k} вариантов названия в реестре учтены вместе)"))
    sd = C.side(t)
    if e.regions and spec.compare != "region":
        regs = ",".join(C.sqlq(r) for r in e.regions)
        if C.has(t, C.REG_W) and sd:
            col = "buyer_inn" if sd == "buyer" else "seller_inn"
            F.where.append(f"{col} IN (SELECT \"ИНН\" FROM participants WHERE \"Регион\" IN ({regs}))")
            F.notes.append(f"регион регистрации {'покупателя' if sd == 'buyer' else 'продавца'}: {', '.join(e.regions)}")
        elif spec.group_by in ("seller", "buyer"):
            col = "buyer_inn" if spec.group_by == "buyer" else "seller_inn"
            F.where.append(f"{col} IN (SELECT \"ИНН\" FROM participants WHERE \"Регион\" IN ({regs}))")
            F.notes.append(f"регион компании: {', '.join(e.regions)}")
        else:
            F.where.append(f"region IN ({regs})"); F.notes.append(f"регион склада продавца: {', '.join(e.regions)}")
    for x in e.inns:
        col = {"seller": "seller_inn", "buyer": "buyer_inn"}.get(sd)
        F.where.append(f"{col}='{x}'" if col else f"(seller_inn='{x}' OR buyer_inn='{x}')")
        F.notes.append(f"ИНН {x}" + (f" как {'продавец' if sd == 'seller' else 'покупатель'}" if col else " (продавец или покупатель)"))
    for b in e.brokers:
        col = {"seller": "seller_broker", "buyer": "buyer_broker"}.get(sd)
        F.where.append(f"{col}='{b}'" if col else f"(seller_broker='{b}' OR buyer_broker='{b}')")
        F.notes.append(f"брокер {b}" + ("" if col else " (любая сторона)"))
    sf, sn = C.status_filter(t)
    if sf: F.cond.append(sf); F.notes.append(sn); F.has_status = True
    if spec.group_by == "contract_type": pass
    elif C.has(t, r"экспорт|eksport"): F.cond.append("contract_type='Экспортный'"); F.notes.append("экспортные")
    elif C.has(t, r"внутрен|ichki"): F.cond.append("contract_type='Внутренний'"); F.notes.append("внутренние")
    if spec.group_by == "contract_form": pass
    elif C.has(t, r"форвард|forvard"): F.cond.append("contract_form='Форвард'"); F.notes.append("форвард")
    elif C.has(t, r"фьючерс|fyuchers"): F.cond.append("contract_form='Фьючерс'"); F.notes.append("фьючерс")
    elif C.has(t, r"\bспот\b|\bspot\b"): F.cond.append("contract_form='Спот'"); F.notes.append("спот")
    if C.has(t, r"обратн\w* аукц|teskari auksion"): F.cond.append("auction_type='Обратный аукцион'"); F.notes.append("обратный аукцион")
    elif C.has(t, r"английск\w* аукц|ingliz auksion"): F.cond.append("auction_type='Английский аукцион'"); F.notes.append("английский аукцион")
    elif C.has(t, r"двусторон|ikki tomonlama"): F.cond.append("auction_type='Двусторонний аукцион'"); F.notes.append("двусторонний аукцион")
    if spec.same_company: F.cond.append("seller_inn = buyer_inn"); F.notes.append("продавец = покупатель (один ИНН)")
    if spec.same_broker: F.cond.append("seller_broker = buyer_broker"); F.notes.append("один брокер у обеих сторон")
    return F


def W(parts): return ("WHERE " + " AND ".join(parts)) if parts else ""


# ------------------------------------------------------------------ metric + dimension definitions
MONEY_METRICS = {"amount", "avg_price", "price_stat", "amount_stat", "volume", "delivered", "undelivered", "deal"}
UNIT_METRICS = {"volume", "avg_price", "price_stat", "delivered", "undelivered", "lots"}


def metric_sql(s: Spec):
    """-> (select expressions list, labels, extra where, note)"""
    m = s.metric
    vol = "CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END" if s.tonnes else "volume_units"
    if m == "count": return ["count(*)"], ["сделок"], [], None
    if m == "lots": return ["round(sum(qty_lots))", "count(*)", "round(sum(volume_units),2)"], ["лотов", "сделок", "@unit"], [], "лоты = поле «Количество» (сколько лотов куплено)"
    if m == "price_change":
        f = {"avg": "avg", "median": "median", "min": "min", "max": "max", "stddev": "stddev_samp", "sum": "avg"}[s.stat or "avg"]
        return [f"round({f}(price_vs_start_pct),2)", "count(*)"], ["%", "сделок"], ["start_price > 0"], \
            "изменение цены = (цена за единицу ÷ стартовая цена − 1) × 100%, по сделкам"
    if m == "amount":
        if s.usd: return ["round(sum(amount),2)", "count(*)"], ["USD", "сделок"], ["currency='Доллар США'"], "сумма в долларах США по экспортным контрактам (без пересчёта)"
        return ["round(sum(amount_uzs))", "count(*)"], ["сум", "сделок"], [], "сумма в сумах; долларовые (экспортные) сделки пересчитаны по курсу на дату сделки"
    if m == "volume" and s.stat in ("avg", "median", "min", "max"):
        f = {"avg": "avg", "median": "median", "min": "min", "max": "max"}[s.stat]
        return [f"round({f}({vol}),3)", "count(*)"], ["@unit", "сделок"], [], f"объём одной сделки ({s.stat}), лоты × размер лота"
    if m == "volume": return [f"round(sum({vol}),2)", "count(*)", "round(sum(amount_uzs))"], ["@unit", "сделок", "сум"], [], "объём = количество лотов × размер лота" + ("; килограммы переведены в тонны" if s.tonnes else "")
    if m == "avg_price":
        cur = "Доллар США" if s.usd else "Узбекский сум"
        return ["round(sum(amount)/sum(volume_units),2)", "count(*)", "round(avg(price_per_unit),2)"], ["@price", "сделок", "@price"], (["currency='Доллар США'"] if s.usd else []), "средневзвешенная цена = сумма ÷ объём (в валюте контракта); третья колонка — простое среднее цен"
    if m == "price_stat":
        f = {"avg": "avg", "median": "median", "min": "min", "max": "max", "stddev": "stddev_samp", "sum": "avg"}[s.stat or "avg"]
        cur = "Доллар США" if s.usd else "Узбекский сум"
        return [f"round({f}(price_per_unit),2)", "count(*)"], ["@price", "сделок"], (["currency='Доллар США'"] if s.usd else []), "цена за единицу = сумма ÷ (лоты × размер лота)"
    if m == "amount_stat":
        f = {"avg": "avg", "median": "median", "min": "min", "max": "max", "stddev": "stddev_samp", "sum": "sum"}[s.stat or "avg"]
        return [f"round({f}(amount_uzs))", "count(*)"], ["сум", "сделок"], [], "по сумме сделки в сумах (экспорт пересчитан по курсу)"
    if m == "distinct":
        col = {"seller": "seller_inn", "buyer": "buyer_inn", "product": "product_base", "region": "region"}.get(s.of)
        if col: return [f"count(DISTINCT {col})"], [{"seller": "продавцов", "buyer": "покупателей", "product": "наименований товара", "region": "регионов"}[s.of]], [], "считаются различные значения, встречающиеся в сделках"
        return None  # company / broker handled as union below
    if m == "delay":
        expr, extra, nn = {"payment": ("paid_date - deal_date", ["paid_date IS NOT NULL"], "от сделки до оплаты"),
                           "delivery": ("delivery_date - deal_date", ["delivery_date IS NOT NULL"], "от сделки до поставки"),
                           "delivery_after_payment": ("delivery_date - paid_date", ["delivery_date IS NOT NULL", "paid_date IS NOT NULL"], "от оплаты до поставки")}[s.of or "payment"]
        f = {"avg": "avg", "median": "median", "min": "min", "max": "max", "stddev": "stddev_samp", "sum": "avg"}[s.stat or "avg"]
        return [f"round({f}({expr}),2)", "count(*)"], ["дн.", "сделок"], extra, f"срок {nn}, календарные дни; только сделки с заполненной датой"
    if m in ("delivered", "undelivered"):
        x = "delivered_lots" if m == "delivered" else "(qty_lots - delivered_lots)"
        extra = [] if m == "delivered" else []
        return [f"round(sum({x}))", f"round(sum({x} * lot_size),2)", "count(*)"], ["лотов", "@unit", "сделок"], extra, \
            "поставлено = «Поставлено» (лоты) × размер лота" if m == "delivered" else "недопоставка = (куплено лотов − поставлено лотов) × размер лота"
    raise ValueError(m)


def dim_sql(d):
    """-> (display expr, key expr, join sql, key label). The key is a separate column (INN, broker code, month number)."""
    return {
        "region": ("region", "NULL", "", None),
        "side": ("'side'", "NULL", "", None),
        "product_group": ("product_group", "NULL", "", None), "product_category": ("product_category", "NULL", "", None),
        "industry": ("industry", "NULL", "", None), "delivery_basis": ("delivery_basis", "NULL", "", None),
        "packaging": ("packaging", "NULL", "", None), "tnved": ("tnved", "NULL", "", None),
        "seller": ("ps.\"Наименование\"", "d.seller_inn", "JOIN participants ps ON ps.\"ИНН\"=d.seller_inn", "ИНН"),
        "buyer": ("pb.\"Наименование\"", "d.buyer_inn", "JOIN participants pb ON pb.\"ИНН\"=d.buyer_inn", "ИНН"),
        "seller_region": ("ps.\"Регион\"", "NULL", "JOIN participants ps ON ps.\"ИНН\"=d.seller_inn", None),
        "buyer_region": ("pb.\"Регион\"", "NULL", "JOIN participants pb ON pb.\"ИНН\"=d.buyer_inn", None),
        "seller_broker": ("d.seller_broker", "bs.\"Наименование\"", "JOIN brokers bs ON bs.\"Код брокера\"=d.seller_broker", ""),
        "buyer_broker": ("d.buyer_broker", "bb.\"Наименование\"", "JOIN brokers bb ON bb.\"Код брокера\"=d.buyer_broker", ""),
        "product": ("product_base", "NULL", "", None),
        "month": ("strftime(deal_date, '%Y-%m')", "month(deal_date)", "", None),
        "year": ("CAST(year(deal_date) AS VARCHAR)", "NULL", "", None),
        "quarter": ("CAST(year(deal_date) AS VARCHAR) || '-Q' || CAST(quarter(deal_date) AS VARCHAR)", "NULL", "", None),
        "status": ("status", "NULL", "", None),
        "contract_form": ("contract_form", "NULL", "", None),
        "contract_type": ("contract_type", "NULL", "", None),
        "auction_type": ("auction_type", "NULL", "", None),
        "hour": ("substr(deal_time, 1, 2) || ':00–' || substr(deal_time, 1, 2) || ':59'", "CAST(substr(deal_time, 1, 2) AS INT)", "", None),
    }[d]


# ------------------------------------------------------------------ compiler
def compile_spec(s: Spec, F: Filters, e, lang_i=0):
    """-> (sql, shape) shape in scalar|grouped|share|share_grouped|deal|compare"""
    m = s.metric
    settled = m in MONEY_METRICS and not F.has_status and m != "deal"
    base_where = list(F.where)
    cond = list(F.cond)
    if settled: base_where.append(f"status<>'{ANNUL}'"); F.notes.append("без аннулированных (не состоявшихся) сделок")
    if s.unit: base_where.append(f"unit='{s.unit}'"); F.notes.append("единица: " + s.unit)

    if m == "deal":
        col = "amount_uzs / NULLIF(volume_units, 0)" if s.stat == "price" else "amount_uzs"
        if s.stat == "price": F.notes.append("цены сравниваются в сумах (экспорт пересчитан по курсу на дату сделки)")
        w = base_where + cond
        sql = (f"SELECT deal_id, deal_date, product_name, round(price_per_unit,2) || CASE WHEN currency='Доллар США' THEN ' USD' ELSE '' END, unit, round(amount_uzs), qty_lots, lot_size, "
               f"ps.\"Наименование\", pb.\"Наименование\" FROM deals_enriched d JOIN participants ps ON ps.\"ИНН\"=d.seller_inn "
               f"JOIN participants pb ON pb.\"ИНН\"=d.buyer_inn {W(w)} ORDER BY {col} {'ASC' if s.order == 'asc' else 'DESC'} LIMIT {s.limit or 1}")
        return sql, "deal"

    if m == "distinct" and s.of in ("company", "broker", None):
        w = W(base_where + cond)
        a, b = ("seller_inn", "buyer_inn") if s.of != "broker" else ("seller_broker", "buyer_broker")
        F.notes.append("считаются различные " + ("ИНН (продавцы и покупатели)" if s.of != "broker" else "коды брокеров обеих сторон"))
        return f"SELECT count(*) FROM (SELECT {a} FROM deals_enriched {w} UNION SELECT {b} FROM deals_enriched {w})", "scalar"

    exprs, labels, extra, note = metric_sql(s)
    if note: F.notes.append(note)
    w_all = base_where + extra
    unit_group = m in UNIT_METRICS and not s.unit
    unit_expr = "CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END" if (s.tonnes and m == "volume") else "unit"
    if m in ("avg_price", "price_stat"): unit_expr = "unit || '|' || currency"     # prices stay in each contract's currency

    if s.share:
        if not cond:
            return None, None
        c = " AND ".join(cond)
        main = exprs[0]
        num = re.sub(r"^round\((.*),\s*\d+\)$", r"\1", main) if main.startswith("round(") else main
        if s.group_by:
            dsel, _, join, _ = dim_sql(s.group_by)
            sql = (f"SELECT {dsel} AS g, round(100.0 * {num} FILTER (WHERE {c}) / NULLIF({num}, 0), 2) AS v, {num} FILTER (WHERE {c}), {num} "
                   f"FROM deals_enriched d {join} {W(w_all)} GROUP BY 1 ORDER BY v {'ASC' if s.order == 'asc' else 'DESC'} LIMIT {s.limit or 20}")
            return sql, "share_grouped"
        sql = f"SELECT round(100.0 * {num} FILTER (WHERE {c}) / NULLIF({num}, 0), 4), {num} FILTER (WHERE {c}), {num} FROM deals_enriched {W(w_all)}"
        return sql, "share"

    w_all = w_all + cond
    if s.group_by == "side" and F.side_entity:
        a, b, v = F.side_entity
        ue = f", {unit_expr}" if unit_group else ", NULL"
        parts = [f"SELECT {C.sqlq(lab)} AS g, NULL AS k{ue} AS u, {', '.join(exprs)} FROM deals_enriched {W(w_all + [f'{col}={C.sqlq(v)}'])}" + (f" GROUP BY {unit_expr}" if unit_group else "")
                 for lab, col in ((("как продавец", "sotuvchi sifatida")[lang_i], a), (("как покупатель", "xaridor sifatida")[lang_i], b))]
        return " UNION ALL ".join(parts), "compare"
    if s.compare:
        vals = {"region": e.regions, "product": e.products, "year": sorted({d for d in getattr(e, "years", [])})}[s.compare]
        parts = []
        for v in vals:
            if s.compare == "region": vw = [f"region={C.sqlq(v)}"]
            elif s.compare == "product":
                sets = dict(getattr(e, "product_sets", []) or [])
                vw = ["product_base IN (" + ",".join(C.sqlq(b) for b in sets[v]) + ")"] if v in sets else [product_sql(v)]
            else: vw = [f"year(deal_date)={int(v)}"]
            ue = f", {unit_expr}" if unit_group else ", NULL"
            gval = str(int(v)) if s.compare == "year" else C.sqlq(str(v))
            parts.append(f"SELECT {gval} AS g, NULL AS k{ue} AS u, {', '.join(exprs)} FROM deals_enriched {W(w_all + vw)}" + (f" GROUP BY {unit_expr}" if unit_group else ""))
        return " UNION ALL ".join(parts), "compare"

    if s.group_by:
        if s.group_by == "any_broker":
            sub = f"SELECT seller_broker AS code, * FROM deals_enriched {W(w_all)} UNION ALL SELECT buyer_broker AS code, * FROM deals_enriched {W(w_all)}"
            F.notes.append("брокер на любой стороне сделки (сделка засчитана обоим брокерам)")
            ue = f", {unit_expr}" if unit_group else ", NULL"
            sql = (f"SELECT x.code AS g, b.\"Наименование\" AS k{ue} AS u, {', '.join(exprs)} FROM ({sub}) x JOIN brokers b ON b.\"Код брокера\"=x.code "
                   f"GROUP BY 1, 2, 3 ORDER BY 4 {'ASC' if s.order == 'asc' else 'DESC'} NULLS LAST LIMIT {s.limit or 10}")
            return sql, "grouped"
        dsel, key, join, _ = dim_sql(s.group_by)
        ue = f", {unit_expr}" if unit_group else ", NULL"
        if len(e.products) == 1 and s.metric == "amount" and s.group_by in ("seller", "buyer", "region", "any_broker", "seller_broker", "buyer_broker"):
            exprs = exprs + ["round(sum(CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END),2)", "round(sum(volume_units),2)"]
        chron = s.group_by in ("month", "year", "quarter") and s.limit is None
        sql = (f"SELECT {dsel} AS g, {key} AS k{ue} AS u, {', '.join(exprs)} FROM deals_enriched d {join} {W(w_all)} GROUP BY 1, 2, 3 "
               f"ORDER BY {'1' if chron else 4} {'ASC' if (chron or s.order == 'asc') else 'DESC'} NULLS LAST LIMIT {s.limit or (60 if chron else 10)}")
        return sql, "grouped"

    if unit_group:
        sql = f"SELECT {unit_expr} AS u, {', '.join(exprs)} FROM deals_enriched {W(w_all)} GROUP BY 1 ORDER BY {len(exprs)+1 if False else 3 if len(exprs) > 1 else 2} DESC"
        return sql, "scalar_units"
    return f"SELECT {', '.join(exprs)} FROM deals_enriched {W(w_all)}", "scalar"


# ------------------------------------------------------------------ rendering
MLABEL = {
    "count": ("Количество сделок", "Bitimlar soni"), "amount": ("Сумма сделок", "Bitimlar summasi"),
    "volume": ("Объём", "Hajmi"), "avg_price": ("Средняя цена", "O'rtacha narx"),
    "price_stat": ("Цена", "Narx"), "amount_stat": ("Сумма сделки", "Bitim summasi"),
    "distinct": ("Количество", "Soni"), "delay": ("Срок", "Muddat"),
    "delivered": ("Фактически поставлено", "Amalda yetkazilgan"), "undelivered": ("Недопоставлено", "Yetkazilmagan"),
    "price_change": ("Изменение цены относительно стартовой", "Boshlang'ich narxga nisbatan o'zgarish"),
    "lots": ("Куплено лотов", "Sotib olingan lotlar"),
}
SLABEL = {"avg": ("среднее", "o'rtacha"), "median": ("медиана", "mediana"), "min": ("минимум", "minimum"),
          "max": ("максимум", "maksimum"), "stddev": ("стандартное отклонение", "standart og'ish"), "sum": ("сумма", "jami")}


def _val(s: Spec, v, lab, unit, lang):
    cur = "USD" if s.usd else {"ru": "сум", "uz": "so'm"}[lang]
    if lab == "@unit": return f"{C.fmt(v)} {unit}"
    if lab == "@price":
        if "|" in (unit or ""):
            unit, cn = unit.split("|", 1)
            cur = "USD" if cn == "Доллар США" else {"ru": "сум", "uz": "so'm"}[lang]
        return f"{C.fmt(v, 2)} {cur} за 1 {unit}"
    if lab == "лотов_": return f"{C.fmt(v)} " + {"ru": "лотов", "uz": "lot"}[lang]
    if lab == "сделок": return f"{C.fmt(v)} " + {"ru": "сделок", "uz": "ta bitim"}[lang]
    if lab == "сум": return f"{C.fmt(v)} " + {"ru": "сум", "uz": "so'm"}[lang]
    if lab == "USD": return f"{C.fmt(v, 2)} USD"
    if lab == "дн.": return f"{v} " + {"ru": "дн.", "uz": "kun"}[lang]
    if lab == "%": return f"{v:+.2f}%" if isinstance(v, (int, float)) else f"{v}%"
    if lab == "лотов": return f"{C.fmt(v)} " + {"ru": "лотов", "uz": "lot"}[lang]
    return f"{C.fmt(v)} {lab}"


def render(s: Spec, shape, rows, lang, e):
    L = 0 if lang == "ru" else 1
    if not rows: return None
    head = MLABEL.get(s.metric, ("Результат", "Natija"))[L]
    if s.stat and s.metric in ("price_stat", "amount_stat", "delay"): head += f" ({SLABEL[s.stat][L]})"
    if s.metric == "distinct" and s.of: head = {"seller": ("Уникальных продавцов", "Noyob sotuvchilar"), "buyer": ("Уникальных покупателей", "Noyob xaridorlar"),
                                                "product": ("Различных наименований товара", "Turli mahsulot nomlari"), "region": ("Регионов со сделками", "Bitimlar bo'lgan hududlar"),
                                                "company": ("Уникальных компаний", "Noyob kompaniyalar"), "broker": ("Уникальных брокеров", "Noyob brokerlar")}.get(s.of, (head, head))[L]
    exprs, labels = ([], [])
    if s.metric not in ("deal",) and not (s.metric == "distinct" and s.of in ("company", "broker", None)):
        _, labels, _, _ = metric_sql(s)
    if shape == "scalar":
        if rows[0][0] is None: return None
        if s.metric == "distinct" and not labels: return f"{head}: {C.fmt(rows[0][0])}."
        parts = [_val(s, v, lab, s.unit or "", lang) for v, lab in zip(rows[0], labels)]
        return f"{head}: {parts[0]}" + (f" ({', '.join(parts[1:])})" if len(parts) > 1 else "") + "."
    if shape == "scalar_units":
        out = []
        for r in rows:
            u, vals = r[0], r[1:]
            if vals[0] is None: continue
            parts = [_val(s, v, lab, u, lang) for v, lab in zip(vals, labels)]
            out.append(parts[0] + (f" ({', '.join(p for p in parts[1:] if 'за 1' not in p or True)})" if len(parts) > 1 else ""))
        return (f"{head}: " + "; ".join(out) + ".") if out else None
    if shape in ("grouped", "compare"):
        keylab = dim_sql(s.group_by)[3] if s.group_by and s.group_by != "any_broker" else ("" if s.group_by == "any_broker" else None)
        lines = []
        for i, r in enumerate([x for x in rows if x[0] != "change_pct"]):
            g, k, u = r[0], r[1], r[2]
            vals = r[3:]
            if vals and vals[0] is None and shape != "compare": continue
            parts = [_val(s, v if v is not None else 0, lab, u or "", lang) for v, lab in zip(vals, labels)]
            if len(vals) > len(labels) and vals[len(labels)] is not None:
                parts.append(f"{C.fmt(vals[len(labels)])} " + ("т" if lang == "ru" else "t"))
            name = f"{g}" + (f" ({keylab} {k})" if keylab and k is not None else (f" {k}" if keylab == "" and k else ""))
            lines.append(f"{i+1}. {name} — {parts[0]}" + (f" ({', '.join(parts[1:])})" if len(parts) > 1 else ""))
        if not lines: return None
        title = head + {"ru": " — сравнение:" if shape == "compare" else ":", "uz": " — taqqoslash:" if shape == "compare" else ":"}[lang]
        txt = title + "\n" + "\n".join(lines)
        if s.compare == "year" and len(rows) == 2 and s.metric in ("count", "amount"):
            a, b = rows[0][3], rows[1][3]
            if a: txt += "\n" + {"ru": f"Изменение: {100.0*(b-a)/a:+.2f}%", "uz": f"O'zgarish: {100.0*(b-a)/a:+.2f}%"}[lang]
        return txt
    if shape == "share":
        p, a, b = rows[0]
        if p is None: return None
        return {"ru": f"Доля: {p}% ({C.fmt(a)} из {C.fmt(b)}).", "uz": f"Ulushi: {p}% ({C.fmt(b)} dan {C.fmt(a)})."}[lang]
    if shape == "share_grouped":
        return {"ru": "Доля:", "uz": "Ulushi:"}[lang] + "\n" + "\n".join(f"{i+1}. {g} — {p}% ({C.fmt(a)} из {C.fmt(b)})" for i, (g, p, a, b) in enumerate(rows) if p is not None)
    if shape == "deal":
        out = []
        for r in rows:
            i, d, pn, v, u, amt, ql, ls, sn, bn = r
            vv = str(v); usd = vv.endswith(" USD"); num = float(vv.replace(" USD", "")) if vv.replace(" USD", "").replace(".", "", 1).isdigit() else vv
            val = (f"{C.fmt(num, 2)} " + ("USD" if usd else {"ru": "сум", "uz": "so'm"}[lang]) + {"ru": " за 1 ", "uz": " / 1 "}[lang] + u) if s.stat == "price" else f"{C.fmt(amt)} " + {"ru": "сум", "uz": "so'm"}[lang]
            out.append({"ru": f"Сделка №{i} от {d:%d.%m.%Y}: {pn}; {val}; {C.fmt(ql)} лотов × {C.fmt(ls)} {u}; сумма {C.fmt(amt)} сум. Продавец: {sn}; покупатель: {bn}.",
                        "uz": f"Bitim №{i}, {d:%d.%m.%Y}: {pn}; {val}; {C.fmt(ql)} lot × {C.fmt(ls)} {u}; summa {C.fmt(amt)} so'm. Sotuvchi: {sn}; xaridor: {bn}."}[lang])
        return "\n".join(out)
    return None


# ------------------------------------------------------------------ rule parser
def rule_spec(t, e):
    """Keyword families -> Spec. Returns (spec, confident)."""
    has = C.has
    s = Spec()
    conf = True
    # modifiers
    s.usd = C.usd_wanted(t) and not has(t, r"\bкурс|\bkurs")
    s.tonnes = has(t, r"тонн|tonna|\bт\b")
    u = C._unit_filter(t)
    if u and not (u == "килограмм" and s.tonnes): s.unit = u
    s.same_company = has(t, r"одна (и та же )?компан|сам[аи]? себе|одн[аио]м и тем же|bir xil kompaniya|o'ziga o'zi")
    s.same_broker = has(t, r"bir xil|одинаков|один и тот же|одним и тем же|совпада") and has(t, r"брокер|broker")
    stat = next((f for f, pat in C.STAT_FN if has(t, pat)), None)
    if has(t, r"средн|o'rtacha|ortacha|average") and not stat: stat = "avg"
    price = has(t, r"\bцен|\bnarx|стоимост\w* (за|1)|почем|почём|дешев|дорог|arzon|qimmat|за тонн|за кг|за килограмм|по тонн")
    # dimension (grouping)
    dim = None
    if has(t, r"по регионам|по областям|в разрезе регион|каждом регионе|каждой области|hududlar bo'yicha|viloyatlar bo'yicha|har bir (hudud|viloyat)"): dim = "region"
    elif has(t, r"по месяцам|помесячно|каждый месяц|каждом месяце|oylar bo'yicha|har (bir )?oy"): dim = "month"
    elif has(t, r"по годам|погодно|каждый год|каждом году|yillar bo'yicha|har (bir )?yil"): dim = "year"
    elif has(t, r"по кварталам|chorak(lar)? bo'yicha"): dim = "quarter"
    elif has(t, r"по товарам|по продуктам|mahsulotlar bo'yicha|tovarlar bo'yicha"): dim = "product"
    elif has(t, r"по статусам|holatlar bo'yicha|в разрезе статус") and not has(t, r"(barcha|все[мх]?|любы\w*|всех) (holatlar|статус)"): dim = "status"
    elif has(t, r"(что|как) с оплат|оплат\w* (сделок )?как|to'lov(lar)? (holati|qanday)") and not has(t, C.COUNT_W): dim = "status"
    elif has(t, r"по формам|по форме контракт|shakllar bo'yicha"): dim = "contract_form"
    top = has(t, r"\bтоп|\btop\b|больше всего|больше всех|кто больше|меньше всего|меньше всех|чаще всего|ko'proq|самы[йеяемх]|крупнейш|лидер|eng ko'p|eng kam|eng faol|eng yirik|наибольш|наименьш|какой брокер|какой регион|какая компания|какой товар|какой покупатель|какой продавец|qaysi|\bkim\b|\bгде\b.{0,30}(больше|меньше)|qayerda")
    wd = C.dim_of(t)
    if top and wd and not dim:
        dim = {"any_broker": "any_broker"}.get(wd, wd)
    if dim is None and has(t, r"в каком месяце|в какой месяц|какой месяц|\boy\b.{0,12}qaysi|qaysi oy"): dim = "month"; s.limit = 1
    if dim is None and has(t, r"в каком году|какой год|qaysi yil|\byil\b.{0,12}qaysi"): dim = "year"; s.limit = 1
    if dim is None and has(t, r"экспорт|eksport") and has(t, r"внутрен|ichki"): dim = "contract_type"
    if dim is None and sum(bool(has(t, p)) for p in (r"форвард|forvard", r"фьючерс|fyuchers", r"\bспот\b|\bspot\b")) >= 2: dim = "contract_form"
    s.group_by = dim
    if has(t, r"меньше всего|меньше всех|наименьш|eng kam|самы[йея] (непопуляр|редк)|least"): s.order = "asc"
    if dim and top:
        s.limit = e.top_n or s.limit or (1 if has(t, r"какой|какая|кто|самы|\beng\b|крупнейш(ий|ая|ее)|qaysi|\bkim\b|лидер|\bгде\b|qayerda") else 5)
    elif e.top_n: s.limit = e.top_n
    # compare
    if (len(e.regions) >= 2 or len(e.products) >= 2) and has(t, r"сравн|solishtir|taqqosla|где больше|qaysi birida|\bили\b|\byoki\b|против|\bvs\b|\bчем\b|qaysi biri"):
        s.compare = "region" if len(e.regions) >= 2 else "product"; s.group_by = None
    if getattr(e, "years", None) and len(set(e.years)) == 2 and has(t, r"больше|меньше|выше|ниже|ko'p|kop\b|kam\b|сравн|solishtir|taqqosla|\bвырос|\bрост|\bснизил|\bупал|\bизменил|o'sdi|osdi|kamaydi|o'zgar|ozgar|nisbatan|по сравнению|динамик|\bvs\b"):
        s.compare = "year"; s.group_by = None
    # share
    if has(t, r"дол[яюи]\b|процент|ulush|foiz|\d+\s*%") and not s.compare: s.share = True
    # metric
    if has(t, r"какая сделка|какую сделку|qaysi bitim|(самая|самую|самой) (крупн|больш|дорог|дешев)\w* сделк|сделк\w* (самая|самой) (крупн|больш)|eng (katta|yirik|qimmat|arzon) bitim|крупнейш\w* сделк|максимальн\w* сделк|наибольш\w* сделк"):
        s.metric = "deal"; s.stat = "price" if price else "amount"; s.order = "asc" if has(t, r"дешев|arzon|наименьш|минимальн|самая маленьк") else "desc"; s.group_by = None
    elif has(t, C.EXT_W) and (stat in ("min", "max") or has(t, r"самы\w* (дешев|дорог)|eng (arzon|qimmat)")):
        s.metric = "deal"; s.stat = "price" if price else "amount"; s.order = "asc" if (stat == "min" or has(t, r"дешев|arzon")) else "desc"; s.group_by = None
        if price and not s.unit and s.tonnes: s.unit = "тонна"
    elif has(t, r"уникальн|различн|разных|turli|har xil|noyob|сколько (видов|наименований|всего )?(товар|продукт|регион)|nechta (mahsulot|tovar|hudud|viloyat)|qancha (kompaniya|xaridor|sotuvchi)|nechta (turli )?(kompaniya|xaridor|sotuvchi|broker)|сколько (всего )?(покупател|продав|компан|участник|брокер)"):
        s.metric = "distinct"
        s.of = ("product" if has(t, r"товар|продукт|mahsulot|tovar") and not has(t, r"компан|kompaniya|покупат|продав|xaridor|sotuvchi") else
                "region" if has(t, r"регион|region|hudud|viloyat|област") else "broker" if has(t, r"брокер|broker") else
                "buyer" if has(t, r"покупат|xaridor") else "seller" if has(t, r"продав|sotuvchi") else "company")
    elif has(t, r"\bсрок|\bдней\b|\bдня\b|\bднях\b|сколько дн|kun ichida|necha kun|\bkun\b|задержк|просроч|kechik|как быстро|qanchalik tez"):
        s.metric = "delay"; s.stat = stat if stat in STATS else "avg"
        s.of = ("delivery_after_payment" if has(t, r"после оплаты|от оплаты|с оплаты|to'lovdan") else
                "delivery" if has(t, r"постав|yetkaz") else "payment")
    elif has(t, r"недопостав|не поставлен|yetkazilmagan|осталось недо|qolgan") and not has(t, r"статус|holat|«"):
        s.metric = "undelivered"
    elif has(t, r"фактически постав|поставлено|yetkazib berilgan|yetkazilgan") and has(t, r"\bлот|lot|тонн|литр|tonna|объ|hajm|сколько|qancha"):
        s.metric = "delivered"
    elif has(t, r"средн\w* (чек|сумм\w* сделк|размер)|o'rtacha (summa|bitim|chek)|ortacha (summa|bitim)") or (stat == "avg" and not price and has(t, C.MONEY_W + r"|\bpulga")):
        s.metric = "amount_stat"; s.stat = "avg"
    elif price:
        if stat and stat != "avg": s.metric, s.stat = "price_stat", stat
        else: s.metric = "avg_price"
    elif stat in ("median", "stddev", "min", "max") and has(t, C.MONEY_W + r"|сделк|bitim"):
        s.metric, s.stat = "amount_stat", stat
    elif has(t, C.UNIT_W) and not has(t, C.MONEY_W):
        s.metric = "volume"
    elif has(t, C.SUM_W) and not has(t, r"сколько сделок|nechta bitim|количеств"):
        s.metric = "amount"
    elif e.inns and has(t, r"сколько\s+(он[аи]?\s+)?(продал|купил)|qancha (sotgan|sotib olgan|sotdi)"):
        s.metric = "amount"
    elif e.products and not top and has(t, r"продан|продал|купил|sotil|sotgan|sotib ol") and not has(t, r"сделк|сделок|bitim|nechta|количеств"):
        s.metric = "volume"
    elif has(t, r"сделк|сделок|сделак|сдела|транзакц|bitim|savdolar|торг|операц|количеств|\bчисло|soni|nechta") or (has(t, C.COUNT_W)):
        s.metric = "count"
    elif s.group_by or s.compare:
        s.metric = "count" if (has(t, r"чаще|активн|faol|tez-tez|по числу|по количеству") or s.group_by in ("status", "month", "year", "quarter")) else "amount"
    elif (e.products or e.regions or e.brokers or e.inns) and not has(t, r"сколько|какой|какая|какие|кто|qancha|qaysi|kim|nechta|\?"):
        s.metric = "amount"          # bare keywords ("наманган цемент"): turnover with deal count
    else:
        conf = False
    # top by amount when grouping companies/brokers without an explicit count word
    if s.group_by in ("seller", "buyer", "region", "product", "any_broker", "seller_broker", "buyer_broker") and s.metric == "count" and top \
            and not has(t, r"чаще|активн|faol|количеств|nechta|по сделкам|по числу|по количеству|ko'p bitim|сделок|tez-tez|savdo qilingan|торгуем|bitim"):
        s.metric = "volume" if (has(t, C.UNIT_W) and not has(t, C.MONEY_W)) else "amount"
    if s.metric in ("volume", "delivered", "undelivered") and s.tonnes and s.unit == "тонна": s.unit = None
    return s, conf


# ------------------------------------------------------------------ LLM parser (fills the same Spec; code validates)
PROMPT = """You convert a question about a commodity-exchange trades database into a JSON query spec. Do not answer it.
Fields (use null when not applicable):
 "metric": count | amount | volume | avg_price | price_stat | amount_stat | distinct | delay | delivered | undelivered | deal | none
   count=number of deals; amount=money total; volume=quantity traded; avg_price=average price per unit;
   price_stat / amount_stat = min/max/median/stddev of unit price / of deal amount; distinct=number of different sellers/buyers/products...;
   delay=days between deal, payment and delivery; delivered/undelivered=lots delivered / not delivered;
   deal=show the single deal (largest, cheapest...); none=the database cannot answer this.
 "stat": avg | median | min | max | stddev
 "of": for distinct: seller|buyer|company|broker|product|region; for delay: payment|delivery|delivery_after_payment
 "group_by": region | seller | buyer | seller_broker | buyer_broker | any_broker | product | month | year | quarter | status | contract_form | contract_type
 "order": desc | asc      "limit": integer (top-N), 1 when the question asks "which one / who"
 "share": true if the question asks for a percentage/share
 "compare": region | product | year   (when two regions/products/years are compared)
 "usd": true if amounts must be in US dollars    "tonnes": true if tonnes are asked
Examples:
Q: top 3 sellers of rice by turnover in 2023 -> {"metric":"amount","group_by":"seller","order":"desc","limit":3}
Q: how many deals per month for cement in 2024 -> {"metric":"count","group_by":"month"}
Q: which region sold the least wheat in tonnes -> {"metric":"volume","group_by":"region","order":"asc","limit":1,"tonnes":true}
Q: what share of sugar deals were cancelled -> {"metric":"count","share":true}
Q: median payment time in days -> {"metric":"delay","stat":"median","of":"payment"}
Q: who is the director of company X -> {"metric":"none"}
Return JSON only."""


def llm_spec(question):
    body = json.dumps({"model": C.LLM_MODEL, "format": "json", "stream": False, "options": {"temperature": 0, "num_predict": 160},
                       "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": question}]}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", body, {"content-type": "application/json"})
    try:
        j = json.loads(json.load(urllib.request.urlopen(req, timeout=60))["message"]["content"])
    except Exception:
        return None
    if j.get("metric") == "none": return "none"
    s = Spec(source="llm")
    if j.get("metric") not in METRICS or j.get("metric") in ("rate", "company", "broker_info", "directory", "both_roles"): return None
    s.metric = j["metric"]
    s.stat = j.get("stat") if j.get("stat") in STATS else None
    if s.metric == "deal": s.stat = "price" if j.get("stat") in ("min", "max") and False else "amount"
    of = j.get("of")
    s.of = of if (s.metric == "distinct" and of in DISTINCT_OF) or (s.metric == "delay" and of in DELAY_OF) else None
    s.group_by = j.get("group_by") if j.get("group_by") in DIMS else None
    s.order = "asc" if j.get("order") == "asc" else "desc"
    lim = j.get("limit")
    s.limit = int(lim) if isinstance(lim, (int, float)) and 0 < int(lim) <= 50 else None
    s.share = j.get("share") is True
    s.compare = j.get("compare") if j.get("compare") in ("region", "product", "year") else None
    s.usd = j.get("usd") is True
    s.tonnes = j.get("tonnes") is True
    return s


# ------------------------------------------------------------------ entry
def run_spec(s: Spec, t, e, lang):
    if s.compare == "region" and len(e.regions) < 2: s.compare = None
    if s.compare == "product" and len(e.products) < 2: s.compare = None
    if s.compare == "year" and len(set(getattr(e, "years", []))) < 2: s.compare = None
    if s.metric in ("price_stat", "amount_stat", "delay") and not s.stat: s.stat = "avg"
    if s.metric == "delay" and not s.of: s.of = "payment"
    F = parse_filters(t, e, s)
    sql, shape = compile_spec(s, F, e)
    if not sql: return None
    rows = C.q(sql)
    if shape == "compare" and s.compare == "year" and len(rows) == 2 and rows[0][3]:
        rows = rows + [("change_pct", None, None, round(100.0 * (rows[1][3] - rows[0][3]) / rows[0][3], 2))]
    txt = render(s, shape, rows, lang, e)
    return sql, rows, txt, F.notes


# ------------------------------------------------------------------ plan (from Gemini) -> Spec + Filters
def spec_from_plan(p):
    s = Spec(source="gemini")
    s.metric = p.get("metric") if p.get("metric") in METRICS else "count"
    st = p.get("stat");  s.stat = st if st in STATS else None
    if s.metric == "distinct": s.of = p.get("distinct_of") if p.get("distinct_of") in DISTINCT_OF else "company"
    if s.metric == "delay": s.of = p.get("delay_of") if p.get("delay_of") in DELAY_OF else "payment"
    if s.metric == "deal": s.stat = "price" if p.get("deal_by") == "price" else "amount"
    g = p.get("group_by"); s.group_by = g if g in DIMS else None
    s.order = "asc" if p.get("order") == "asc" else "desc"
    lim = p.get("limit")
    s.limit = int(lim) if isinstance(lim, (int, float)) and 0 < int(lim) <= 50 else None
    s.share = p.get("share") is True
    c = p.get("compare"); s.compare = c if c in ("region", "product", "year") else None
    s.usd = p.get("usd") is True
    s.tonnes = p.get("tonnes") is True
    u = p.get("unit"); s.unit = u if u and u != "none" else None
    if s.metric == "volume" and s.tonnes and s.unit in ("тонна", "килограмм"): s.unit = None
    if s.metric not in UNIT_METRICS and s.metric != "deal": s.unit = None
    return s


_BASES = None


def BASES():
    global _BASES
    if _BASES is None: _BASES = {b for (b,) in C.q("SELECT DISTINCT product_base FROM deals_enriched")}
    return _BASES


STATUS_SQL = {"not_annulled": (f"status<>'{ANNUL}'", "без аннулированных (не состоявшихся) сделок"),
              "executed_full_or_partial": ("status IN ('Исполнен','Исполнен(Частично)')", "статус: исполнен полностью или частично")}


def filters_from_plan(p, e, t, s):
    F = Filters()
    if e.date_from and s.compare != "year":
        F.where.append(f"deal_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'"); F.notes.append(f"период {e.period_label}")
    bases_pick = [b for b in (p.get("product_bases") or []) if b in BASES()]
    if bases_pick and s.compare != "product":
        (F.cond if (s.share and p.get("share_condition") == "product") else F.where).append("product_base IN (" + ",".join(C.sqlq(b) for b in bases_pick) + ")")
        F.notes.append("товар (наименования в реестре): " + "; ".join(bases_pick[:8]) + (f" и ещё {len(bases_pick)-8}" if len(bases_pick) > 8 else ""))
    elif e.products and s.compare != "product":
        variant = (p.get("product_variant") or "").strip().lower()
        conds, k, nar = [], 0, []
        for pr in e.products:
            bases = [b for (b,) in C.q(f"SELECT DISTINCT product_base FROM deals_enriched WHERE {product_sql(pr)}")]
            nb = (C.narrow(" " + variant + " ", pr, bases) if variant else None) or C.narrow(t, pr, bases)
            if nb: conds.append("product_base IN (" + ",".join(C.sqlq(b) for b in nb) + ")"); k += len(nb); nar += nb
            else: conds.append(product_sql(pr)); k += len(bases)
        F.where.append("(" + " OR ".join(conds) + ")")
        F.notes.append(("товар: " + "; ".join(nar) + " (по указанному сорту/марке)") if nar else
                       ("товар: " + ", ".join(e.products) + f" ({k} вариантов названия в реестре учтены вместе)"))
    side = p.get("side") if p.get("side") in ("seller", "buyer") else None
    if e.regions and s.compare != "region":
        regs = ",".join(C.sqlq(r) for r in e.regions)
        scope = p.get("region_scope") or "warehouse"
        if scope in ("seller_company", "buyer_company"):
            col = "buyer_inn" if scope == "buyer_company" else "seller_inn"
            F.where.append(f"{col} IN (SELECT \"ИНН\" FROM participants WHERE \"Регион\" IN ({regs}))")
            F.notes.append(f"регион регистрации {'покупателя' if scope == 'buyer_company' else 'продавца'}: {', '.join(e.regions)}")
        else:
            (F.cond if (s.share and p.get("share_condition") == "region") else F.where).append(f"region IN ({regs})")
            F.notes.append(f"регион склада продавца: {', '.join(e.regions)}")
    if s.group_by == "side" and (e.brokers or e.inns):
        F.side_entity = ("seller_broker", "buyer_broker", e.brokers[0]) if e.brokers else ("seller_inn", "buyer_inn", e.inns[0])
        F.notes.append(f"{'брокер' if e.brokers else 'ИНН'} {F.side_entity[2]}: отдельно как сторона продавца и как сторона покупателя")
    else:
        if s.group_by == "side": s.group_by = None
        for x in e.inns:
            col = {"seller": "seller_inn", "buyer": "buyer_inn"}.get(side)
            F.where.append(f"{col}='{x}'" if col else f"(seller_inn='{x}' OR buyer_inn='{x}')")
            F.notes.append(f"ИНН {x}" + (f" как {'продавец' if side == 'seller' else 'покупатель'}" if col else " (продавец или покупатель)"))
        for b in e.brokers:
            col = {"seller": "seller_broker", "buyer": "buyer_broker"}.get(side)
            F.where.append(f"{col}='{b}'" if col else f"(seller_broker='{b}' OR buyer_broker='{b}')")
            F.notes.append(f"брокер {b}" + ("" if col else " (любая сторона)"))
    for fld, col, lab in (("product_group", "product_group", "группа товара"), ("product_category", "product_category", "категория товара"),
                          ("industry", "industry", "отрасль"), ("delivery_basis", "delivery_basis", "базис поставки"),
                          ("packaging", "packaging", "упаковка")):
        v = p.get(fld)
        if v and v != "none" and v in CARD_VALUES().get(col, ()) and s.group_by != col:
            (F.cond if (s.share and p.get("share_condition") == fld) else F.where).append(f"{col}={C.sqlq(v)}"); F.notes.append(f"{lab}: {v}")
    tn = re.sub(r"\D", "", str(p.get("tnved") or ""))
    if tn: F.where.append(f"tnved LIKE '{tn}%'"); F.notes.append(f"код ТН ВЭД {tn}")
    shc = p.get("share_condition") if s.share else None
    def put(kind, sql, note):
        (F.cond if (not s.share or shc == kind) else F.where).append(sql); F.notes.append(note)
    st = p.get("status")
    if st and st != "none":
        sql, note = STATUS_SQL.get(st, (f"status={C.sqlq(st)}", f"статус: {st}"))
        put("status", sql, note + " (состояние на конец дня 30.10.2026)"); F.has_status = True
    ct = p.get("contract_type")
    if ct in ("Внутренний", "Экспортный") and s.group_by != "contract_type": put("contract_type", f"contract_type='{ct}'", "экспортные" if ct == "Экспортный" else "внутренние")
    cf = p.get("contract_form")
    if cf in ("Спот", "Форвард", "Фьючерс") and s.group_by != "contract_form": put("contract_form", f"contract_form='{cf}'", cf.lower())
    at = p.get("auction_type")
    if at and at != "none": put("auction_type", f"auction_type={C.sqlq(at)}", at.lower())
    late = p.get("late")
    if late == "delivered_late": put("late", "delivered_late", "поставка позже срока (дата поставки > дата оплаты + срок поставки из контракта)")
    elif late == "delivered_on_time": put("late", "delivered_late = false", "поставка в срок")
    elif late == "paid_late": put("late", "paid_late", "оплата позже срока (дата оплаты > дата сделки + срок оплаты из контракта)")
    elif late == "paid_on_time": put("late", "paid_late = false", "оплата в срок")
    if late and late != "none" and s.share:
        F.where.append("delivery_date IS NOT NULL AND paid_date IS NOT NULL" if late.startswith("delivered") else "paid_date IS NOT NULL")
        F.notes.append("только сделки с заполненными датами")
    return F


_CARD = None


def CARD_VALUES():
    global _CARD
    if _CARD is None:
        _CARD = {c: {v for (v,) in C.q(f"SELECT DISTINCT {c} FROM contracts_clean WHERE {c} IS NOT NULL")}
                 for c in ("product_group", "product_category", "industry", "delivery_basis", "packaging")}
    return _CARD


EXTRA_OK = {"count", "amount", "volume", "avg_price", "amount_stat", "price_change", "delay"}


def _breakdown(s, F, e, lang):
    """Several registry names behind one product: show the per-name split so either reading is visible."""
    if s.metric not in ("count", "amount", "volume") or s.stat or s.group_by or s.compare or s.share: return None
    names = [w for w in F.where if w.startswith("product_base IN") or w.startswith("(product_base")]
    if not names: return None
    s2 = Spec(**{**s.__dict__}); s2.group_by = "product"; s2.limit = 8
    F2 = Filters(where=list(F.where), cond=list(F.cond), notes=[], has_status=F.has_status)
    sql, shape = compile_spec(s2, F2, e)
    rows = C.q(sql)
    if len(rows) < 2: return None
    return sql, rows, render(s2, "grouped", rows, lang, e)


def run_plan(p, t, e, lang):
    if p.get("kind") == "contract_lookup":
        return run_lookup(p, t, e, lang)
    if (p.get("kind") == "contracts") or p.get("metric") in CONTRACT_METRICS:
        return run_contracts(p, t, e, lang)
    s = spec_from_plan(p)
    # "when was it cheapest / most expensive" → show the deal row (date), not a per-month maximum
    if s.metric in ("price_stat", "amount_stat") and s.stat in ("min", "max") and (s.group_by in ("month", "year", "quarter") or p.get("deal_by") == "price"):
        s.metric, s.stat, s.group_by = "deal", ("price" if s.metric == "price_stat" else "amount"), None
        s.order = "asc" if p.get("stat") == "min" else "desc"; s.limit = 1
    # two or more different products named → compare product groups, not individual registry spellings
    sets = getattr(e, "product_sets", []) or []
    if len(sets) >= 2 and (s.group_by == "product" or s.compare == "product") and not s.share:
        e.products = [lab for lab, _ in sets]; s.compare = "product"; s.group_by = None; p = {**p, "product_bases": []}
    # a ranking over a small categorical attribute with limit 1 ("EXW or CPT — which is pricier") → show every value
    if s.group_by in ("delivery_basis", "contract_form", "contract_type", "status", "auction_type", "product_group", "side") and s.limit == 1:
        s.limit = None
    if s.compare == "region" and len(e.regions) < 2: s.compare = None
    if s.compare == "product" and len(e.products) < 2: s.compare = None
    if s.compare == "year" and len(set(e.years)) < 2: s.compare = None
    if s.metric in ("price_stat", "amount_stat", "delay") and not s.stat: s.stat = "avg"
    F = filters_from_plan(p, e, t, s)
    from . import trace as TR
    TR.step("spec", source="gemini", spec={k: v for k, v in s.__dict__.items() if v not in (None, False, "")})
    TR.step("filters", where=F.where, cond=F.cond, notes=F.notes)
    sql, shape = compile_spec(s, F, e, 0 if lang == "ru" else 1)
    TR.step("compiled", shape=shape)
    if not sql: return None
    rows = C.q(sql)
    if shape == "compare" and s.compare == "year" and len(rows) == 2 and rows[0][3]:
        rows = rows + [("change_pct", None, None, round(100.0 * (rows[1][3] - rows[0][3]) / rows[0][3], 2))]
    txt = render(s, shape, rows, lang, e)
    if txt is None: return s, sql, rows, None, F.notes
    # multi-part questions: extra metrics over the same filters
    for m2 in [m for m in (p.get("extra_metrics") or []) if m in EXTRA_OK and m != s.metric][:3]:
        s2 = Spec(**{**s.__dict__}); s2.metric = m2
        if m2 == "delay": s2.of = p.get("delay_of") if p.get("delay_of") in DELAY_OF else "payment"; s2.stat = "avg"
        if m2 == "amount_stat": s2.stat = "avg"
        F2 = filters_from_plan(p, e, t, s2)
        sql2, shape2 = compile_spec(s2, F2, e, 0 if lang == "ru" else 1)
        if not sql2: continue
        r2 = C.q(sql2)
        t2 = render(s2, shape2, r2, lang, e)
        if t2: txt += "\n" + t2; rows = list(rows) + list(r2); sql += ";\n" + sql2
    bd = _breakdown(s, F, e, lang)
    if bd:
        txt += "\n" + {"ru": "В том числе по наименованиям в реестре:", "uz": "Shu jumladan reestrdagi nomlar bo'yicha:"}[lang] + "\n" + bd[2].split("\n", 1)[1]
        rows = list(rows) + list(bd[1]); sql += ";\n" + bd[0]
    return s, sql, rows, txt, F.notes


# ------------------------------------------------------------------ contract card lookup (field values)
LOOKUP_COLS = {"tnved": "Код ТН ВЭД", "deposit_pct": "Сумма залога, %", "delivery_basis": "Базис поставки", "packaging": "Упаковка",
               "pay_term_days": "Срок оплаты, дней", "delivery_term_days": "Срок поставки, дней", "base_price": "Базисная цена",
               "warehouse_address": "Адрес склада", "reg_date": "Дата регистрации", "valid_to": "Действует до", "product_base": "Товар",
               "seller": "Продавец", "region": "Регион склада", "brand": "Марка", "grade": "Сорт", "pclass": "Класс", "unit": "Единица",
               "lot_size": "Лот", "currency": "Валюта", "contract_form": "Форма контракта", "contract_type": "Тип контракта",
               "auction_type": "Тип торгов", "product_category": "Категория товара", "product_group": "Группа товара", "industry": "Отрасль"}


def run_lookup(p, t, e, lang):
    fields = [f for f in (p.get("lookup_fields") or []) if f in LOOKUP_COLS][:6] or ["product_base", "delivery_basis", "pay_term_days"]
    col = lambda f: {"seller": "ps.\"Наименование\" || ' (ИНН ' || c.seller_inn || ')'", "region": "c.region_lat"}.get(f, "c." + f)
    ids = [re.sub(r"\D", "", str(x)) for x in (p.get("contract_ids") or []) if re.sub(r"\D", "", str(x))]
    if not ids: ids = re.findall(r"\b(1\d{5})\b", t)
    join = "LEFT JOIN participants ps ON ps.\"ИНН\"=c.seller_inn"
    if ids:
        sql = f"SELECT c.contract_id, {', '.join(col(f) for f in fields)} FROM contracts_clean c {join} WHERE c.contract_id IN ({','.join(ids)}) ORDER BY 1"
        rows = C.q(sql)
        if not rows: return None, sql, [], None, ["карточки контрактов"]
        lines = [f"Контракт №{r[0]}: " + "; ".join(f"{LOOKUP_COLS[f]}: {C.fmt(v, 2) if isinstance(v, float) else v}" for f, v in zip(fields, r[1:])) for r in rows]
        return None, sql, rows, "\n".join(lines), ["карточки контрактов (contracts.csv)"]
    w = []
    bases = [b for b in (p.get("product_bases") or []) if b in BASES()]
    if bases: w.append("c.product_base IN (" + ",".join(C.sqlq(b) for b in bases) + ")")
    elif e.products: w.append("(" + " OR ".join(product_sql(x).replace("product_base", "c.product_base") for x in e.products) + ")")
    if e.regions: w.append("c.region_lat IN (" + ",".join(C.sqlq(r) for r in e.regions) + ")")
    for x in e.inns: w.append(f"c.seller_inn='{x}'")
    if not w: return None, None, [], None, []
    f = fields[0]
    sql = f"SELECT {col(f)} AS v, count(*) FROM contracts_clean c {join} {W(w)} GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
    rows = C.q(sql)
    if not rows: return None, sql, [], None, ["карточки контрактов"]
    head = {"ru": f"{LOOKUP_COLS[f]} в карточках контрактов (значение — число контрактов):", "uz": f"{LOOKUP_COLS[f]} (qiymat — shartnomalar soni):"}[lang]
    return None, sql, rows, head + "\n" + "\n".join(f"• {C.fmt(v, 2) if isinstance(v, float) else v} — {C.fmt(n)}" for v, n in rows), ["карточки контрактов (contracts.csv)"]


# ------------------------------------------------------------------ contract cards (contracts.csv)
CONTRACT_METRICS = {"contracts", "base_price", "deposit", "pay_term", "delivery_term"}
CDIMS = {"product": "product_base", "region": "region_lat", "seller": "seller_inn", "product_group": "product_group",
         "product_category": "product_category", "industry": "industry", "delivery_basis": "delivery_basis", "packaging": "packaging",
         "contract_form": "contract_form", "contract_type": "contract_type", "auction_type": "auction_type", "tnved": "tnved",
         "year": "CAST(year(reg_date) AS VARCHAR)", "month": "strftime(reg_date, '%Y-%m')"}


def run_contracts(p, t, e, lang):
    from . import trace as TR
    TR.step("spec", source="gemini", kind="contracts", metric=p.get("metric"), group_by=p.get("group_by"))
    m = p.get("metric") if p.get("metric") in CONTRACT_METRICS else "contracts"
    stat = p.get("stat") if p.get("stat") in ("avg", "median", "min", "max") else "avg"
    w, notes = [], ["карточки контрактов (contracts.csv)"]
    bases = [b for b in (p.get("product_bases") or []) if b in BASES()]
    if bases: w.append("product_base IN (" + ",".join(C.sqlq(b) for b in bases) + ")"); notes.append("товар: " + "; ".join(bases[:6]))
    elif e.products: w.append("(" + " OR ".join(product_sql(x) for x in e.products) + ")"); notes.append("товар: " + ", ".join(e.products))
    if e.regions: w.append("region_lat IN (" + ",".join(C.sqlq(r) for r in e.regions) + ")"); notes.append("регион склада: " + ", ".join(e.regions))
    for x in e.inns: w.append(f"seller_inn='{x}'"); notes.append(f"продавец ИНН {x}")
    if e.date_from:
        if p.get("contract_date_mode") == "active":
            w.append(f"reg_date <= DATE '{e.date_to}' AND valid_to >= DATE '{e.date_from}'"); notes.append(f"действовали в период {e.period_label}")
        else:
            w.append(f"reg_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'"); notes.append(f"зарегистрированы в период {e.period_label}")
    g = p.get("group_by") if p.get("group_by") in CDIMS else None
    for fld, col in (("product_group", "product_group"), ("product_category", "product_category"), ("industry", "industry"),
                     ("delivery_basis", "delivery_basis"), ("packaging", "packaging")):
        v = p.get(fld)
        if v and v != "none" and v in CARD_VALUES().get(col, ()) and g != fld: w.append(f"{col}={C.sqlq(v)}"); notes.append(f"{fld}: {v}")
    for fld in ("contract_type", "contract_form", "auction_type"):
        v = p.get(fld)
        if v and v != "none" and g != fld: w.append(f"{fld}={C.sqlq(v)}"); notes.append(v)
    tn = re.sub(r"\D", "", str(p.get("tnved") or ""))
    if tn: w.append(f"tnved LIKE '{tn}%'"); notes.append(f"ТН ВЭД {tn}")
    shc = p.get("share_condition") if p.get("share") else None
    if shc in ("delivery_basis", "packaging", "product_category", "industry", "product_group", "contract_form", "contract_type", "auction_type", "region"):
        target = {"region": "region_lat"}.get(shc, shc)
        cond = [x for x in w if x.startswith(target + "=") or x.startswith(target + " IN")]
        rest = [x for x in w if x not in cond]
        if cond:
            sql = f"SELECT round(100.0 * count(*) FILTER (WHERE {' AND '.join(cond)}) / NULLIF(count(*), 0), 4), count(*) FILTER (WHERE {' AND '.join(cond)}), count(*) FROM contracts_clean {W(rest)}"
            rows = C.q(sql)
            txt = {"ru": f"Доля контрактов: {rows[0][0]}% ({C.fmt(rows[0][1])} из {C.fmt(rows[0][2])}).", "uz": f"Shartnomalar ulushi: {rows[0][0]}% ({C.fmt(rows[0][2])} tadan {C.fmt(rows[0][1])})."}[lang]
            return None, sql, rows, txt, notes
    f = {"avg": "avg", "median": "median", "min": "min", "max": "max"}[stat]
    expr, unit_dep, lab = {"contracts": ("count(*)", False, ("Контрактов", "Shartnomalar")),
                           "base_price": (f"round({f}(base_price),2)", True, ("Базисная цена", "Bazis narx")),
                           "deposit": (f"round({f}(deposit_pct),2)", False, ("Сумма залога, %", "Garov summasi, %")),
                           "pay_term": (f"round({f}(pay_term_days),2)", False, ("Срок оплаты, дней", "To'lov muddati, kun")),
                           "delivery_term": (f"round({f}(delivery_term_days),2)", False, ("Срок поставки, дней", "Yetkazish muddati, kun"))}[m]
    L = 0 if lang == "ru" else 1
    head = lab[L] + ("" if m == "contracts" else f" ({SLABEL[stat][L]})")
    if m == "base_price": notes.append("базисная цена за единицу на дату регистрации контракта, в валюте контракта")
    if g:
        gx = CDIMS[g]
        name = "ps.\"Наименование\" || ' (ИНН ' || c.seller_inn || ')'" if g == "seller" else gx
        join = "JOIN participants ps ON ps.\"ИНН\"=c.seller_inn" if g == "seller" else ""
        uc = ", unit, currency" if unit_dep else ""
        sql = (f"SELECT {name} AS g{uc}, {expr} AS v, count(*) FROM contracts_clean c {join} {W(w)} GROUP BY 1{', 2, 3' if unit_dep else ''} "
               f"ORDER BY v {'ASC' if p.get('order') == 'asc' else 'DESC'} NULLS LAST LIMIT {int(p.get('limit') or 10) if str(p.get('limit') or '').isdigit() and int(p.get('limit') or 0) > 0 else 10}")
        rows = C.q(sql)
        if not rows: return None, sql, rows, None, notes
        lines = [f"{i+1}. {r[0]}" + (f" — {C.fmt(r[3],2)} {r[2] == 'Доллар США' and 'USD' or 'сум'} за 1 {r[1]} ({C.fmt(r[4])} контр.)" if unit_dep else f" — {C.fmt(r[1], 2 if m != 'contracts' else 0)}" + ("" if m == "contracts" else f" ({C.fmt(r[2])} контр.)")) for i, r in enumerate(rows)]
        return None, sql, rows, head + ":\n" + "\n".join(lines), notes
    if unit_dep:
        sql = f"SELECT unit, currency, {expr}, count(*) FROM contracts_clean {W(w)} GROUP BY 1, 2 ORDER BY 4 DESC"
        rows = C.q(sql)
        if not rows: return None, sql, rows, None, notes
        txt = head + ": " + "; ".join(f"{C.fmt(r[2],2)} {'USD' if r[1] == 'Доллар США' else 'сум'} за 1 {r[0]} ({C.fmt(r[3])} контр.)" for r in rows) + "."
        return None, sql, rows, txt, notes
    sql = f"SELECT {expr}, count(*) FROM contracts_clean {W(w)}"
    rows = C.q(sql)
    if not rows or rows[0][0] is None: return None, sql, [], None, notes
    v = C.fmt(rows[0][0], 0 if m == "contracts" else 2)
    txt = f"{head}: {v}" + ("" if m == "contracts" else f" ({C.fmt(rows[0][1])} " + ("контрактов" if L == 0 else "ta shartnoma") + ")") + "."
    return None, sql, rows, txt, notes
