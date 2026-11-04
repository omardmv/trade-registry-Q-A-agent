def patch(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert a in s, f"{p}: MISSING {a[:90]}"
        s = s.replace(a, b, 1)
    open(p, 'w').write(s)

patch('agent/gemini.py', [
 ('''"kind": {"type": "STRING", "enum": ["deals", "contracts", "usd_rate",''', '''"kind": {"type": "STRING", "enum": ["deals", "contracts", "contract_lookup", "usd_rate",'''),
 ('''"share_condition": {"type": "STRING", "enum": ["none", "status", "contract_type", "contract_form", "auction_type", "late"]},''',
  '''"share_condition": {"type": "STRING", "enum": ["none", "status", "contract_type", "contract_form", "auction_type", "late", "region",
                                                         "product", "delivery_basis", "packaging", "product_category", "industry"]},
        "contract_ids": arr(S),
        "lookup_fields": arr({"type": "STRING", "enum": ["tnved", "deposit_pct", "delivery_basis", "packaging", "pay_term_days", "delivery_term_days",
                                                        "base_price", "warehouse_address", "reg_date", "valid_to", "product_base", "seller", "region",
                                                        "brand", "grade", "pclass", "unit", "lot_size", "currency", "contract_form", "contract_type",
                                                        "auction_type", "product_category", "product_group", "industry"]}),
        "activity": {"type": "STRING", "enum": ["none", "Кластер", "Производитель", "Производственное предприятие", "Перерабатывающее предприятие",
                                                 "Торговая компания", "Транспортная компания", "Строительная компания"]},'''),
 ('''"rate_mode": {"type": "STRING", "enum": ["none", "on_date", "avg", "max", "min", "first", "last"]},''',
  '''"rate_mode": {"type": "STRING", "enum": ["none", "on_date", "avg", "max", "min", "first", "last", "change"]},'''),
 ('''kind: deals | contracts (questions about contract cards themselves''', '''kind: contract_lookup = read card fields: of specific contracts by number (contract_ids, lookup_fields), or which values a field takes
      for a product ("какой код ТН ВЭД у хлопка", "какой залог у цемента"; lookup_fields + product_bases) |
      deals | contracts (questions about contract cards themselves'''),
 ('''rate_date / rate_mode: for usd_rate questions.''', '''rate_date / rate_mode: for usd_rate questions (change = % change of the rate between the start and end of the period).
activity: participant activity type for directory questions. share_condition may also be region/product/delivery_basis/... :
  the numerator is that filter, the denominator is everything else (e.g. "% of bearing deals in Samarkand" → share_condition region).
volume with stat avg = average volume per deal. For "when was X cheapest/most expensive" use metric deal, deal_by price (not price_stat).'''),
])

patch('agent/semantic.py', [
 # volume per deal stats
 ('''    if m == "volume": return [f"round(sum({vol}),2)", "count(*)", "round(sum(amount_uzs))"], ["@unit", "сделок", "сум"], [],''',
  '''    if m == "volume" and s.stat in ("avg", "median", "min", "max"):
        f = {"avg": "avg", "median": "median", "min": "min", "max": "max"}[s.stat]
        return [f"round({f}({vol}),3)", "count(*)"], ["@unit", "сделок"], [], f"объём одной сделки ({s.stat}), лоты × размер лота"
    if m == "volume": return [f"round(sum({vol}),2)", "count(*)", "round(sum(amount_uzs))"], ["@unit", "сделок", "сум"], [],'''),
 # share with 4 decimals
 ('''round(100.0 * {num} FILTER (WHERE {c}) / NULLIF({num}, 0), 2), {num} FILTER (WHERE {c}), {num} FROM deals_enriched {W(w_all)}"''',
  '''round(100.0 * {num} FILTER (WHERE {c}) / NULLIF({num}, 0), 4), {num} FILTER (WHERE {c}), {num} FROM deals_enriched {W(w_all)}"'''),
 # entity filters can be the share numerator
 ('''        F.where.append("product_base IN (" + ",".join(C.sqlq(b) for b in bases_pick) + ")")''',
  '''        (F.cond if (s.share and p.get("share_condition") == "product") else F.where).append("product_base IN (" + ",".join(C.sqlq(b) for b in bases_pick) + ")")'''),
 ('''        else:
            F.where.append(f"region IN ({regs})"); F.notes.append(f"регион склада продавца: {', '.join(e.regions)}")
    if s.group_by == "side" and (e.brokers or e.inns):''', '''        else:
            (F.cond if (s.share and p.get("share_condition") == "region") else F.where).append(f"region IN ({regs})")
            F.notes.append(f"регион склада продавца: {', '.join(e.regions)}")
    if s.group_by == "side" and (e.brokers or e.inns):'''),
 ('''        if v and v != "none" and v in CARD_VALUES().get(col, ()) and s.group_by != col:
            F.where.append(f"{col}={C.sqlq(v)}"); F.notes.append(f"{lab}: {v}")''',
  '''        if v and v != "none" and v in CARD_VALUES().get(col, ()) and s.group_by != col:
            (F.cond if (s.share and p.get("share_condition") == fld) else F.where).append(f"{col}={C.sqlq(v)}"); F.notes.append(f"{lab}: {v}")'''),
 # plan normalisation before compiling
 ('''    s = spec_from_plan(p)
    if s.compare == "region" and len(e.regions) < 2: s.compare = None''', '''    s = spec_from_plan(p)
    # "when was it cheapest / most expensive" → show the deal row (date), not a per-month maximum
    if s.metric in ("price_stat", "amount_stat") and s.stat in ("min", "max") and (s.group_by in ("month", "year", "quarter") or p.get("deal_by") == "price"):
        s.metric, s.stat, s.group_by = "deal", ("price" if s.metric == "price_stat" else "amount"), None
        s.order = "asc" if p.get("stat") == "min" else "desc"; s.limit = 1
    # two or more different products named → compare product groups, not individual registry spellings
    picked = [b for b in (p.get("product_bases") or []) if b in BASES()]
    groups = [n for n, rx, _ in PRODUCTS if any(C.q(f"SELECT 1 WHERE {C.sqlq(b)} SIMILAR TO {C.sqlq(rx)}") for b in picked)] if picked else []
    if len(groups) >= 2 and (s.group_by == "product" or s.compare == "product") and not s.share:
        e.products = groups; s.compare = "product"; s.group_by = None; p = {**p, "product_bases": []}
    # a ranking over a small categorical attribute with limit 1 ("EXW or CPT — which is pricier") → show every value
    if s.group_by in ("delivery_basis", "contract_form", "contract_type", "status", "auction_type", "product_group", "side") and s.limit == 1:
        s.limit = None
    if s.compare == "region" and len(e.regions) < 2: s.compare = None'''),
 # contract lookup
 ('''# ------------------------------------------------------------------ contract cards (contracts.csv)''', '''# ------------------------------------------------------------------ contract card lookup (field values)
LOOKUP_COLS = {"tnved": "Код ТН ВЭД", "deposit_pct": "Сумма залога, %", "delivery_basis": "Базис поставки", "packaging": "Упаковка",
               "pay_term_days": "Срок оплаты, дней", "delivery_term_days": "Срок поставки, дней", "base_price": "Базисная цена",
               "warehouse_address": "Адрес склада", "reg_date": "Дата регистрации", "valid_to": "Действует до", "product_base": "Товар",
               "seller": "Продавец", "region": "Регион склада", "brand": "Марка", "grade": "Сорт", "pclass": "Класс", "unit": "Единица",
               "lot_size": "Лот", "currency": "Валюта", "contract_form": "Форма контракта", "contract_type": "Тип контракта",
               "auction_type": "Тип торгов", "product_category": "Категория товара", "product_group": "Группа товара", "industry": "Отрасль"}


def run_lookup(p, t, e, lang):
    fields = [f for f in (p.get("lookup_fields") or []) if f in LOOKUP_COLS][:6] or ["product_base", "delivery_basis", "pay_term_days"]
    col = lambda f: {"seller": "ps.\\"Наименование\\" || ' (ИНН ' || c.seller_inn || ')'", "region": "c.region_lat"}.get(f, "c." + f)
    ids = [re.sub(r"\\D", "", str(x)) for x in (p.get("contract_ids") or []) if re.sub(r"\\D", "", str(x))]
    if not ids: ids = re.findall(r"\\b(1\\d{5})\\b", t)
    join = "LEFT JOIN participants ps ON ps.\\"ИНН\\"=c.seller_inn"
    if ids:
        sql = f"SELECT c.contract_id, {', '.join(col(f) for f in fields)} FROM contracts_clean c {join} WHERE c.contract_id IN ({','.join(ids)}) ORDER BY 1"
        rows = C.q(sql)
        if not rows: return None, sql, [], None, ["карточки контрактов"]
        lines = [f"Контракт №{r[0]}: " + "; ".join(f"{LOOKUP_COLS[f]}: {C.fmt(v, 2) if isinstance(v, float) else v}" for f, v in zip(fields, r[1:])) for r in rows]
        return None, sql, rows, "\\n".join(lines), ["карточки контрактов (contracts.csv)"]
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
    return None, sql, rows, head + "\\n" + "\\n".join(f"• {C.fmt(v, 2) if isinstance(v, float) else v} — {C.fmt(n)}" for v, n in rows), ["карточки контрактов (contracts.csv)"]


# ------------------------------------------------------------------ contract cards (contracts.csv)'''),
 ('''def run_plan(p, t, e, lang):
    if (p.get("kind") == "contracts") or p.get("metric") in CONTRACT_METRICS:''', '''def run_plan(p, t, e, lang):
    if p.get("kind") == "contract_lookup":
        return run_lookup(p, t, e, lang)
    if (p.get("kind") == "contracts") or p.get("metric") in CONTRACT_METRICS:'''),
 # contracts share (e.g. % of cotton contracts with FCA)
 ('''    f = {"avg": "avg", "median": "median", "min": "min", "max": "max"}[stat]
    expr, unit_dep, lab = {"contracts":''', '''    shc = p.get("share_condition") if p.get("share") else None
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
    expr, unit_dep, lab = {"contracts":'''),
])
patch('agent/semantic.py', [('''from .entities import product_sql, PRODUCTS, DATA_START, DATA_END''', '''from .entities import product_sql, PRODUCTS, DATA_START, DATA_END''')])

patch('agent/core.py', [
 # rate change mode
 ('''                       "last": f"SELECT rate_date, uzs_per_usd FROM usd_rates {w} ORDER BY rate_date DESC LIMIT 1"}''',
  '''                       "last": f"SELECT rate_date, uzs_per_usd FROM usd_rates {w} ORDER BY rate_date DESC LIMIT 1",
                       "change": f"SELECT min(rate_date), max(rate_date), arg_min(uzs_per_usd, rate_date), arg_max(uzs_per_usd, rate_date), round(100.0*(arg_max(uzs_per_usd, rate_date)/arg_min(uzs_per_usd, rate_date)-1),4) FROM usd_rates {w}"}'''),
 ('''            if len(r[0]) == 2:''', '''            if mode == "change":
                d0, d1, v0, v1, pct = r[0]
                txt = {"ru": f"Курс доллара: {fmt(v0,2)} сум ({d0:%d.%m.%Y}) → {fmt(v1,2)} сум ({d1:%d.%m.%Y}); изменение {pct:+.2f}%.",
                       "uz": f"Dollar kursi: {fmt(v0,2)} so'm ({d0:%d.%m.%Y}) → {fmt(v1,2)} so'm ({d1:%d.%m.%Y}); o'zgarish {pct:+.2f}%."}[lang]
                return wrap(sql, [(pct, v0, v1)], txt, ["курс из usd_rates: первый и последний день периода"], "gemini:usd_rate")
            if len(r[0]) == 2:'''),
 # directory: explicit activity from the plan
 ('''            if p.get("share"): t2 += " процент "''', '''            if p.get("share"): t2 += " процент "
            if p.get("activity") and p.get("activity") != "none": t2 += f" §act={p['activity']}§ "'''),
 ('''    for pat, val in ACTIVITY:
        if has(t, pat): w.append(f"\\"Вид деятельности\\"='{val}'"); n.append("вид деятельности: " + val); break''',
  '''    act = re.search(r"§act=([^§]+)§", t)
    if act: w.append(f"\\"Вид деятельности\\"={sqlq(act.group(1))}"); n.append("вид деятельности: " + act.group(1))
    else:
        for pat, val in ACTIVITY:
            if has(t, pat): w.append(f"\\"Вид деятельности\\"='{val}'"); n.append("вид деятельности: " + val); break'''),
])
print("ok")
