def patch(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert a in s, f"{p}: MISSING {a[:90]}"
        s = s.replace(a, b)
    open(p, 'w').write(s)

SEM = 'agent/semantic.py'
patch(SEM, [
 ('''METRICS = ("count", "amount", "volume", "avg_price", "price_stat", "amount_stat", "distinct", "delay", "delivered",''',
  '''METRICS = ("price_change", "count", "amount", "volume", "avg_price", "price_stat", "amount_stat", "distinct", "delay", "delivered",'''),
 ('''DIMS = ("side", "region",''', '''DIMS = ("product_group", "product_category", "industry", "delivery_basis", "packaging", "tnved", "side", "region",'''),
 # new deal metric: price change vs start price
 ('''    if m == "count": return ["count(*)"], ["сделок"], [], None''', '''    if m == "count": return ["count(*)"], ["сделок"], [], None
    if m == "price_change":
        f = {"avg": "avg", "median": "median", "min": "min", "max": "max", "stddev": "stddev_samp", "sum": "avg"}[s.stat or "avg"]
        return [f"round({f}(price_vs_start_pct),2)", "count(*)"], ["%", "сделок"], ["start_price > 0"], \\
            "изменение цены = (цена за единицу ÷ стартовая цена − 1) × 100%, по сделкам"'''),
 ('''    if lab == "дн.": return f"{v} " + {"ru": "дн.", "uz": "kun"}[lang]''', '''    if lab == "дн.": return f"{v} " + {"ru": "дн.", "uz": "kun"}[lang]
    if lab == "%": return f"{v:+.2f}%" if isinstance(v, (int, float)) else f"{v}%"'''),
 ('''    "delivered": ("Фактически поставлено", "Amalda yetkazilgan"), "undelivered": ("Недопоставлено", "Yetkazilmagan"),''',
  '''    "delivered": ("Фактически поставлено", "Amalda yetkazilgan"), "undelivered": ("Недопоставлено", "Yetkazilmagan"),
    "price_change": ("Изменение цены относительно стартовой", "Boshlang'ich narxga nisbatan o'zgarish"),'''),
 # new deal dimensions
 ('''        "region": ("region", "NULL", "", None),
        "side": ("'side'", "NULL", "", None),''', '''        "region": ("region", "NULL", "", None),
        "side": ("'side'", "NULL", "", None),
        "product_group": ("product_group", "NULL", "", None), "product_category": ("product_category", "NULL", "", None),
        "industry": ("industry", "NULL", "", None), "delivery_basis": ("delivery_basis", "NULL", "", None),
        "packaging": ("packaging", "NULL", "", None), "tnved": ("tnved", "NULL", "", None),'''),
])

# taxonomy / card filters + late flags in plan filters; multi-metric + breakdown in run_plan; contracts path
patch(SEM, [
 ('''    shc = p.get("share_condition") if s.share else None
    def put(kind, sql, note):''', '''    for fld, col, lab in (("product_group", "product_group", "группа товара"), ("product_category", "product_category", "категория товара"),
                          ("industry", "industry", "отрасль"), ("delivery_basis", "delivery_basis", "базис поставки"),
                          ("packaging", "packaging", "упаковка")):
        v = p.get(fld)
        if v and v != "none" and v in CARD_VALUES().get(col, ()) and s.group_by != col:
            F.where.append(f"{col}={C.sqlq(v)}"); F.notes.append(f"{lab}: {v}")
    tn = re.sub(r"\\D", "", str(p.get("tnved") or ""))
    if tn: F.where.append(f"tnved LIKE '{tn}%'"); F.notes.append(f"код ТН ВЭД {tn}")
    shc = p.get("share_condition") if s.share else None
    def put(kind, sql, note):'''),
 ('''    at = p.get("auction_type")
    if at and at != "none": put("auction_type", f"auction_type={C.sqlq(at)}", at.lower())
    return F''', '''    at = p.get("auction_type")
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
    return _CARD'''),
 ('''def run_plan(p, t, e, lang):
    s = spec_from_plan(p)''', '''EXTRA_OK = {"count", "amount", "volume", "avg_price", "amount_stat", "price_change", "delay"}


def _breakdown(s, F, e, lang):
    """Several registry names behind one product: show the per-name split so either reading is visible."""
    if s.metric not in ("count", "amount", "volume") or s.group_by or s.compare or s.share: return None
    names = [w for w in F.where if w.startswith("product_base IN") or w.startswith("(product_base")]
    if not names: return None
    s2 = Spec(**{**s.__dict__}); s2.group_by = "product"; s2.limit = 8
    F2 = Filters(where=list(F.where), cond=list(F.cond), notes=[], has_status=F.has_status)
    sql, shape = compile_spec(s2, F2, e)
    rows = C.q(sql)
    if len(rows) < 2: return None
    return sql, rows, render(s2, "grouped", rows, lang, e)


def run_plan(p, t, e, lang):
    if (p.get("kind") == "contracts") or p.get("metric") in CONTRACT_METRICS:
        return run_contracts(p, t, e, lang)
    s = spec_from_plan(p)'''),
 ('''    if shape == "compare" and s.compare == "year" and len(rows) == 2 and rows[0][3]:
        rows = rows + [("change_pct", None, None, round(100.0 * (rows[1][3] - rows[0][3]) / rows[0][3], 2))]
    return s, sql, rows, render(s, shape, rows, lang, e), F.notes''', '''    if shape == "compare" and s.compare == "year" and len(rows) == 2 and rows[0][3]:
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
        if t2: txt += "\\n" + t2; rows = list(rows) + list(r2); sql += ";\\n" + sql2
    bd = _breakdown(s, F, e, lang)
    if bd:
        txt += "\\n" + {"ru": "В том числе по наименованиям в реестре:", "uz": "Shu jumladan reestrdagi nomlar bo'yicha:"}[lang] + "\\n" + bd[2].split("\\n", 1)[1]
        rows = list(rows) + list(bd[1]); sql += ";\\n" + bd[0]
    return s, sql, rows, txt, F.notes


# ------------------------------------------------------------------ contract cards (contracts.csv)
CONTRACT_METRICS = {"contracts", "base_price", "deposit", "pay_term", "delivery_term"}
CDIMS = {"product": "product_base", "region": "region_lat", "seller": "seller_inn", "product_group": "product_group",
         "product_category": "product_category", "industry": "industry", "delivery_basis": "delivery_basis", "packaging": "packaging",
         "contract_form": "contract_form", "contract_type": "contract_type", "auction_type": "auction_type", "tnved": "tnved",
         "year": "CAST(year(reg_date) AS VARCHAR)", "month": "strftime(reg_date, '%Y-%m')"}


def run_contracts(p, t, e, lang):
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
    tn = re.sub(r"\\D", "", str(p.get("tnved") or ""))
    if tn: w.append(f"tnved LIKE '{tn}%'"); notes.append(f"ТН ВЭД {tn}")
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
        name = "ps.\\"Наименование\\" || ' (ИНН ' || c.seller_inn || ')'" if g == "seller" else gx
        join = "JOIN participants ps ON ps.\\"ИНН\\"=c.seller_inn" if g == "seller" else ""
        uc = ", unit, currency" if unit_dep else ""
        sql = (f"SELECT {name} AS g{uc}, {expr} AS v, count(*) FROM contracts_clean c {join} {W(w)} GROUP BY 1{', 2, 3' if unit_dep else ''} "
               f"ORDER BY v {'ASC' if p.get('order') == 'asc' else 'DESC'} NULLS LAST LIMIT {int(p.get('limit') or 10) if str(p.get('limit') or '').isdigit() and int(p.get('limit') or 0) > 0 else 10}")
        rows = C.q(sql)
        if not rows: return None, sql, rows, None, notes
        lines = [f"{i+1}. {r[0]}" + (f" — {C.fmt(r[3],2)} {r[2] == 'Доллар США' and 'USD' or 'сум'} за 1 {r[1]} ({C.fmt(r[4])} контр.)" if unit_dep else f" — {C.fmt(r[1], 2 if m != 'contracts' else 0)}" + ("" if m == "contracts" else f" ({C.fmt(r[2])} контр.)")) for i, r in enumerate(rows)]
        return None, sql, rows, head + ":\\n" + "\\n".join(lines), notes
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
    return None, sql, rows, txt, notes'''),
])

GEM = 'agent/gemini.py'
patch(GEM, [
 ('''"kind": {"type": "STRING", "enum": ["deals", "usd_rate", "company_info", "broker_info", "participants_directory", "brokers_directory"]},''',
  '''"kind": {"type": "STRING", "enum": ["deals", "contracts", "usd_rate", "company_info", "broker_info", "participants_directory", "brokers_directory"]},'''),
 ('''"delay", "delivered", "undelivered", "deal"]},''', '''"delay", "delivered", "undelivered", "deal", "price_change",
                                               "contracts", "base_price", "deposit", "pay_term", "delivery_term"]},
        "extra_metrics": arr({"type": "STRING", "enum": ["count", "amount", "volume", "avg_price", "amount_stat", "price_change", "delay"]}),'''),
 ('''"month", "year", "quarter", "status", "contract_form", "contract_type", "side"]},''', '''"month", "year", "quarter", "status", "contract_form", "contract_type", "side",
                                                 "product_group", "product_category", "industry", "delivery_basis", "packaging", "tnved", "auction_type"]},'''),
 ('''"share_condition": {"type": "STRING", "enum": ["none", "status", "contract_type", "contract_form", "auction_type"]},''',
  '''"share_condition": {"type": "STRING", "enum": ["none", "status", "contract_type", "contract_form", "auction_type", "late"]},'''),
 ('''        "vat": {"type": "STRING", "enum": ["none", "yes", "no"]},''', '''        "vat": {"type": "STRING", "enum": ["none", "yes", "no"]},
        "late": {"type": "STRING", "enum": ["none", "delivered_late", "delivered_on_time", "paid_late", "paid_on_time"]},
        "product_group": {"type": "STRING", "enum": ["none", "Высоколиквидные товары", "Прочие товары"]},
        "product_category": {"type": "STRING", "enum": ["none", "Металлопродукция", "Сельскохозяйственная продукция", "Промышленные товары", "Строительные материалы",
                                                         "Продовольственные товары", "Химическая продукция", "Нефтепродукты", "Полимерная продукция"]},
        "industry": {"type": "STRING", "enum": ["none", "Сельское хозяйство", "Пищевая промышленность", "Строительная индустрия", "Нефтегазовая промышленность",
                                                 "Химическая промышленность", "Металлургия", "Лесная промышленность", "Текстильная промышленность",
                                                 "Машиностроение", "Электротехническая промышленность"]},
        "delivery_basis": {"type": "STRING", "enum": ["none", "EXW", "FCA", "DAP", "CPT"]},
        "packaging": S, "tnved": S,
        "contract_date_mode": {"type": "STRING", "enum": ["none", "registered", "active"]},'''),
 ('''- participants: INN (STIR), company name, registration region, activity type, role (seller or buyer), VAT payer yes/no.''',
  '''  Each deal belongs to a contract card (contracts.csv): product group (Высоколиквидные/Прочие товары), product category
  (Нефтепродукты, Строительные материалы, Продовольственные товары...), industry (Сельское хозяйство, Металлургия...), customs code ТН ВЭД,
  brand/size/grade/type/class, unit and lot, currency, base price per unit at registration, deposit % (Сумма залога), delivery basis
  (EXW/FCA/DAP/CPT), packaging (навалом, в мешках...), warehouse region and address, payment term and delivery term in days,
  registration date and valid-until date. Derived per deal: delivered late (delivery date > payment date + delivery term),
  paid late (payment date > deal date + payment term), price change vs start price in %.
- participants: INN (STIR), company name, registration region, activity type, role (seller or buyer), VAT payer yes/no.'''),
 ('''Questions about deals of a product/region/period that simply may have zero rows ARE answerable.''', '''Questions about deals of a product/region/period that simply may have zero rows ARE answerable.
NEVER set answerable=false just because a company, product or place name looks unfamiliar or is misspelled: put it in the right field
(company_names, product_bases, regions) and code will check it against the registry.'''),
 ('''kind: deals | usd_rate |''', '''kind: deals | contracts (questions about contract cards themselves: how many contracts, base price, deposit %, payment/delivery
      term of contracts, contracts registered/valid in a period; metric contracts/base_price/deposit/pay_term/delivery_term) | usd_rate |'''),
 ('''  (set delay_of, stat); delivered / undelivered = lots delivered / not delivered; deal=show specific deal(s) (largest/cheapest; set deal_by, order, limit).''',
  '''  (set delay_of, stat); delivered / undelivered = lots delivered / not delivered; deal=show specific deal(s) (largest/cheapest; set deal_by, order, limit);
  price_change = how much the final unit price differs from the start price, in % (set stat).
extra_metrics: further metrics when the question asks for several things at once ("how many deals AND for what amount", "tonnes and number of deals").
late: deals delivered/paid late or on time (use share=true, share_condition=late for "what % were delivered late").
product_group / product_category / industry / delivery_basis / packaging / tnved: contract-card filters when the question names them
  (e.g. "нефтепродукты" → product_category Нефтепродукты; "строительная индустрия" → industry). Also usable in group_by.
contract_date_mode: for kind=contracts with a period: registered (default) or active (valid during the period).'''),
 ('''Q: кто директор компании Zoket Invest -> answerable false, missing "в данных нет сведений о руководителях компаний"''',
  '''Q: кто директор компании Zoket Invest -> answerable false, missing "в данных нет сведений о руководителях компаний"
Q: сколько сделок по рису в 2024 и сколько тонн -> metric count, extra_metrics ["volume"], tonnes true, products ["рис"], date 2024
Q: qancha shartnoma sement bo'yicha ro'yxatdan o'tgan -> kind contracts, metric contracts, products ["цемент"]
Q: какой процент поставок по сахару был с опозданием -> metric count, share true, share_condition late, late delivered_late, products ["сахар"]
Q: сколько сделок по нефтепродуктам в 2025 -> metric count, product_category "Нефтепродукты", date 2025
Q: на сколько процентов в среднем выросла цена от стартовой на английском аукционе -> metric price_change, stat avg, auction_type "Английский аукцион"'''),
])
print("patched")
