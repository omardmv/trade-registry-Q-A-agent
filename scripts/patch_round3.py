import re
p = 'agent/core.py'; s = open(p).read()
def rep(a, b, cnt=1):
    global s
    assert s.count(a) >= 1, "MISSING: " + a[:80]
    s = s.replace(a, b)

# ---------------- build_filters rewrite
start = s.index("def build_filters(t, e, dim=None):"); end = s.index("def where(w):")
s = s[:start] + r'''def sqlq(x): return "'" + str(x).replace("'", "''") + "'"


NARROW_STOP = ("перв", "втор", "трет", "четв", "квар", "год", "меся", "сдел", "скол", "всег", "прода", "купи", "цен", "сред", "тонн", "объ")


def narrow(t, product, bases):
    """If the question names a specific grade/variant, keep only base names containing it."""
    from .entities import PRODUCTS as P
    aliases = [a.strip() for n, _, al in P if n == product for a in al]
    tt = re.sub(r"(\d)\.(\d)", r"\1,\2", t)
    tt = re.sub(r"\b(аи|ai)[\s-]?(\d{2})\b", r"аи-\2", tt)
    quals = []
    for tok in set(re.findall(r"[а-яa-z0-9ø][а-яa-z0-9ø,/-]*", tt)):
        if tok.isdigit() or len(tok) < 3 or any(a and a in tok for a in aliases): continue
        stem = tok if any(c.isdigit() for c in tok) else tok[:max(4, len(tok) - 2)]
        if len(stem) < 3 or stem.startswith(NARROW_STOP): continue
        hit = [b for b in bases if stem in b]
        if 0 < len(hit) < len(bases): quals.append(stem)
    if not quals: return None
    res = [b for b in bases if all(x in b for x in quals)]
    return res or None


def status_filter(t):
    if has(t, r"не аннулир|без аннулир|кроме аннулир|исключая аннулир|bekor qilinmagan|bekor qilinganlarsiz|bekor qilinganlardan tashqari|не отмен"):
        return f"status<>'{ANNUL}'", "без аннулированных (не состоявшихся) сделок"
    if has(t, r"(исполнен\w*|bajarilgan) и частичн|частичн\w* и (полностью )?исполн|to'liq va qisman"):
        return "status IN ('Исполнен','Исполнен(Частично)')", "статус: исполнен полностью или частично"
    if has(t, r"аннулир|bekor|неоплат|не оплач|to'lanmagan|отмен"): return f"status='{ANNUL}'", "статус: аннулирован (не оплачен)"
    if has(t, r"частичн|qisman"): return "status='Исполнен(Частично)'", "статус: исполнен частично"
    if has(t, r"состоял|состоявш|amalga osh|реальн"): return f"status<>'{ANNUL}'", "без аннулированных (не состоявшихся) сделок"
    if has(t, r"статус\w*\s*«?нов|\bнов(ый|ые|ых|ым)\b|\byangi\b"): return "status='Новый'", "статус: Новый (заключены 30.10.2026)"
    if has(t, r"не подписан|imzolanmagan"): return "status='Не подписан'", "статус: не подписан"
    if has(t, r"жд[её]т оплат|ждут оплат|to'lov kut"): return "status='Ждёт оплату'", "статус: ждёт оплату"
    if has(t, r"исполнен|bajarilgan"): return "status='Исполнен'", "статус: исполнен"
    return None, None


REG_W = r"зарегистр|ro'yxatdan|расположен|joylashgan|находящ|из региона"


def build_filters(t, e, dim=None, settled=False):
    w, notes = [], []
    if e.date_from:
        w.append(f"deal_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'"); notes.append(f"период {e.period_label}")
    if e.products:
        conds, k, nar = [], 0, False
        for pr in e.products:
            bases = [b for (b,) in q(f"SELECT DISTINCT product_base FROM deals_enriched WHERE {product_sql(pr)}")]
            nb = narrow(t, pr, bases)
            if nb:
                conds.append("product_base IN (" + ",".join(sqlq(b) for b in nb) + ")"); k += len(nb); nar = True
            else:
                conds.append(product_sql(pr)); k += len(bases)
        w.append("(" + " OR ".join(conds) + ")")
        if nar: notes.append("товар: " + "; ".join(b for c in conds for b in re.findall(r"'([^']+)'", c) if not c.startswith("product_base SIMILAR")) + " (по указанному сорту/марке)")
        else: notes.append("товар: " + ", ".join(e.products) + f" ({k} вариантов названия в реестре учтены вместе)")
    sd = side(t)
    if e.regions:
        regs = ",".join(sqlq(r) for r in e.regions)
        if has(t, REG_W) and sd:
            col = "buyer_inn" if sd == "buyer" else "seller_inn"
            w.append(f"{col} IN (SELECT \"ИНН\" FROM participants WHERE \"Регион\" IN ({regs}))"); notes.append(f"регион регистрации {'покупателя' if sd=='buyer' else 'продавца'}: {', '.join(e.regions)}")
        elif dim in ("buyer", "seller"):
            w.append(f"p.\"Регион\" IN ({regs})"); notes.append(f"регион компании: {', '.join(e.regions)}")
        else:
            w.append(f"region IN ({regs})"); notes.append(f"регион склада продавца: {', '.join(e.regions)}")
    for x in e.inns:
        col = {"seller": "seller_inn", "buyer": "buyer_inn"}.get(sd)
        w.append(f"{col}='{x}'" if col else f"(seller_inn='{x}' OR buyer_inn='{x}')"); notes.append(f"ИНН {x}" + (f" как {'продавец' if sd=='seller' else 'покупатель'}" if col else " (продавец или покупатель)"))
    for b in e.brokers:
        col = {"seller": "seller_broker", "buyer": "buyer_broker"}.get(sd)
        w.append(f"{col}='{b}'" if col else f"(seller_broker='{b}' OR buyer_broker='{b}')"); notes.append(f"брокер {b}" + ("" if col else " (любая сторона)"))
    sf, sn = status_filter(t)
    if sf: w.append(sf); notes.append(sn)
    elif settled: w.append(f"status<>'{ANNUL}'"); notes.append("без аннулированных (не состоявшихся) сделок")
    if has(t, r"экспорт|eksport"): w.append("contract_type='Экспортный'"); notes.append("экспортные")
    elif has(t, r"внутрен|ichki"): w.append("contract_type='Внутренний'"); notes.append("внутренние")
    if has(t, r"форвард|forvard"): w.append("contract_form='Форвард'"); notes.append("форвард")
    elif has(t, r"фьючерс|fyuchers"): w.append("contract_form='Фьючерс'"); notes.append("фьючерс")
    elif has(t, r"\bспот\b|\bspot\b"): w.append("contract_form='Спот'"); notes.append("спот")
    if has(t, r"обратн\w* аукц|teskari auksion"): w.append("auction_type='Обратный аукцион'"); notes.append("обратный аукцион")
    elif has(t, r"английск\w* аукц|ingliz auksion"): w.append("auction_type='Английский аукцион'"); notes.append("английский аукцион")
    elif has(t, r"двусторон|ikki tomonlama"): w.append("auction_type='Двусторонний аукцион'"); notes.append("двусторонний аукцион")
    return w, notes


''' + s[end:]

# settled defaults for money/volume/price templates
for fn in ["tpl_amount", "tpl_volume", "tpl_avg_price", "tpl_avg_deal", "tpl_largest"]:
    a = f"def {fn}(t, e, lang):\n    w, n = build_filters(t, e)"
    rep(a, f"def {fn}(t, e, lang):\n    w, n = build_filters(t, e, settled=True)")
rep('''    w, n = build_filters(t, e)
    sqlf = {"median"''', '''    w, n = build_filters(t, e, settled=True)
    sqlf = {"median"''')

# count shows settled/annulled split
rep('''    sql = f"SELECT count(*) FROM deals_enriched {where(w)}"
    r = q(sql); v = r[0][0]
    txt = {"ru": f"Количество сделок: {fmt(v)}.", "uz": f"Bitimlar soni: {fmt(v)} ta."}[lang]''', '''    split = not any("status" in x for x in w)
    sql = f"SELECT count(*){', count(*) FILTER (WHERE status<>' + sqlq(ANNUL) + ')' if split else ''} FROM deals_enriched {where(w)}"
    r = q(sql); v = r[0][0]
    txt = {"ru": f"Количество сделок: {fmt(v)}.", "uz": f"Bitimlar soni: {fmt(v)} ta."}[lang]
    if split and v and r[0][1] != v:
        txt += {"ru": f" Из них состоялось {fmt(r[0][1])}, аннулировано (не оплачено) {fmt(v - r[0][1])}.",
                "uz": f" Shundan amalga oshgan {fmt(r[0][1])} ta, bekor qilingan (to'lanmagan) {fmt(v - r[0][1])} ta."}[lang]''')

# top: by-amount excludes annulled; broker any side
rep('''    w, n = build_filters(t, e, dim=d if d in ("buyer", "seller") else None)
    by_count = has(t, r"чаще|активн|faol|количеств|nechta|по сделкам|по числу|по количеству|ko'p bitim|сделок больше|tez-tez")''',
'''    by_count = has(t, r"чаще|активн|faol|количеств|nechta|по сделкам|по числу|по количеству|ko'p bitim|сделок больше|tez-tez")
    by_vol = (not by_count) and has(t, r"тонн|tonna|\\bкг\\b|килограмм|литр|объ[её]м|hajm") and not has(t, r"сумм|summa|pul|so'm|\\bсум\\b|денег|долл")
    w, n = build_filters(t, e, dim=d if d in ("buyer", "seller") else None, settled=not by_count)
    if d == "any_broker":
        k = e.top_n or (1 if has(t, r"какой|какая|кто|самы|\\beng\\b|qaysi|\\bkim\\b|лидер") else 5)
        m = "count(*)" if by_count else "round(sum(amount_uzs))"
        sub = f"SELECT seller_broker AS code, * FROM deals_enriched {where(w)} UNION ALL SELECT buyer_broker AS code, * FROM deals_enriched {where(w)}"
        sql = f"SELECT x.code, {m} AS v, b.\\"Наименование\\" FROM ({sub}) x JOIN brokers b ON b.\\"Код брокера\\"=x.code GROUP BY 1, 3 ORDER BY v DESC LIMIT {k}"
        r = q(sql)
        n.append("брокер на любой стороне сделки (сделка засчитана обоим брокерам)")
        n.append("по количеству сделок" if by_count else "по сумме сделок в сумах (экспорт пересчитан по курсу)")
        unit = {"ru": " сделок", "uz": " ta bitim"}[lang] if by_count else {"ru": " сум", "uz": " so'm"}[lang]
        return sql, r, {"ru": "Результат:", "uz": "Natija:"}[lang] + "\\n" + "\\n".join(f"{i+1}. {x[0]} {x[2]} — {fmt(x[1])}{unit}" for i, x in enumerate(r)), n''')
rep('''    metric = "count(*)" if by_count else "round(sum(amount_uzs))"
    k = e.top_n''', '''    metric = "count(*)" if by_count else ("round(sum(CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END),2)" if by_vol else "round(sum(amount_uzs))")
    k = e.top_n''')
rep('''    n.append("по количеству сделок" if by_count else "по сумме сделок в сумах (экспорт пересчитан по курсу)")
    if d == "region": n.append("регион = регион склада продавца")
    unit = "" if by_count else {"ru": " сум", "uz": " so'm"}[lang]''', '''    n.append("по количеству сделок" if by_count else ("по объёму (кг переведены в тонны)" if by_vol else "по сумме сделок в сумах (экспорт пересчитан по курсу)"))
    if d == "region": n.append("регион = регион склада продавца")
    unit = "" if by_count else (" т" if by_vol else {"ru": " сум", "uz": " so'm"}[lang])''')
rep('''    if has(t, r"брокер|broker"): return "buyer_broker" if side(t) == "buyer" else "seller_broker"''',
    '''    if has(t, r"брокер|broker"): return {"buyer": "buyer_broker", "seller": "seller_broker"}.get(side(t), "any_broker")''')
rep('''    "product": ("product_base", "product_base", None),''', '''    "product": ("product_base", "product_base", None),
    "any_broker": ("code", "code", None),''')

# by_month: month number column
rep('''    sql = f"SELECT {grp} AS p, {m} AS v FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 2 {order} LIMIT 3"''',
    '''    sql = f"SELECT {grp} AS p, {m} AS v, CAST(right({grp}, 2) AS INT) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 2 {order} LIMIT 3"''')

# rate extremes
rep('''def tpl_rate(t, e, lang):
    if e.exact_date:''', '''def tpl_rate(t, e, lang):
    ext = "DESC" if has(t, r"максимальн|наибольш|самый высок|самым высок|eng yuqori|eng baland|eng katta|\\bmax|пик") else "ASC" if has(t, r"минимальн|наименьш|самый низк|eng past|eng kichik|\\bmin") else None
    if ext:
        w = [f"rate_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'"] if e.date_from else [f"rate_date <= DATE '{DATA_END}'"]
        sql = f"SELECT rate_date, uzs_per_usd FROM usd_rates WHERE {' AND '.join(w)} ORDER BY uzs_per_usd {ext} LIMIT 1"
        r = q(sql)
        word = {"DESC": ("Самый высокий", "Eng yuqori"), "ASC": ("Самый низкий", "Eng past")}[ext]
        txt = {"ru": f"{word[0]} курс: {fmt(r[0][1],2)} сум за 1 USD ({r[0][0]:%d.%m.%Y}).", "uz": f"{word[1]} kurs: 1 USD = {fmt(r[0][1],2)} so'm ({r[0][0]:%d.%m.%Y})."}[lang]
        return sql, r, txt, ["курс ЦБ из usd_rates" if lang == "ru" else "usd_rates jadvalidagi kurs"]
    if e.exact_date:''')

# underdelivery with units + delivered
rep('''    sql = f"SELECT round(sum(qty_lots - delivered_lots)), count(*) FROM deals_enriched {where(w)}"
    r = q(sql)
    txt = {"ru": f"Недопоставлено {fmt(r[0][0])} лотов по {fmt(r[0][1])} частично исполненным сделкам.", "uz": f"{fmt(r[0][1])} ta qisman bajarilgan bitim bo'yicha {fmt(r[0][0])} lot yetkazilmagan."}[lang]
    return sql, r, txt, ["недопоставка = куплено лотов − поставлено лотов"]''',
'''    delivered = not has(t, r"недопостав|не поставлен|yetkazilmagan|осталось|qolgan")
    x = "delivered_lots" if delivered else "(qty_lots - delivered_lots)"
    sql = f"SELECT unit, round(sum({x})), round(sum({x} * lot_size), 2), count(*) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 4 DESC"
    r = q(sql)
    if not r: return sql, r, None, []
    parts = [f"{fmt(a)} лотов = {fmt(b)} {u}" for u, a, b, _ in r]
    cnt = sum(z[3] for z in r)
    txt = ({"ru": "Фактически поставлено: " + "; ".join(parts) + f" ({fmt(cnt)} сделок).", "uz": "Amalda yetkazilgan: " + "; ".join(parts).replace("лотов", "lot") + f" ({fmt(cnt)} ta bitim)."}
           if delivered else {"ru": "Недопоставлено: " + "; ".join(parts) + f" ({fmt(cnt)} частично исполненных сделок).", "uz": "Yetkazilmagan: " + "; ".join(parts).replace("лотов", "lot") + f" ({fmt(cnt)} ta qisman bajarilgan bitim)."})[lang]
    return sql, r, txt, n + ["поставлено = поле «Поставлено» (лоты) × размер лота" if delivered else "недопоставка = (куплено лотов − поставлено лотов) × размер лота"]''')

# self: broker equality / both roles
rep('''def tpl_self(t, e, lang):
    w, n = build_filters(t, e); w.append("seller_inn = buyer_inn")''', '''def tpl_self(t, e, lang):
    w, n = build_filters(t, e)
    if has(t, r"продавц\\w*.{0,40}покупател|покупател\\w*.{0,40}продавц|ham sotuvchi.{0,20}ham xaridor|sotuvchi va xaridor sifatida") and has(t, r"компан|kompaniya|участник|firma"):
        sql = "SELECT count(*) FROM (SELECT DISTINCT seller_inn AS i FROM deals INTERSECT SELECT DISTINCT buyer_inn FROM deals)"
        r = q(sql); v = r[0][0]
        txt = ({"ru": "Нет: ни одна компания не выступает и продавцом, и покупателем (0). Роли участников в данных разделены.", "uz": "Yo'q: hech bir kompaniya ham sotuvchi, ham xaridor bo'lmagan (0)."}[lang]
               if v == 0 else {"ru": f"Да, таких компаний: {fmt(v)}.", "uz": f"Ha, bunday kompaniyalar: {fmt(v)} ta."}[lang])
        return sql, r, txt, ["пересечение множеств ИНН продавцов и покупателей"]
    if has(t, r"брокер|broker"):
        w.append("seller_broker = buyer_broker")
        sql = f"SELECT count(*) FROM deals_enriched {where(w)}"
        r = q(sql); v = r[0][0]
        txt = {"ru": f"Сделок, где у продавца и покупателя один и тот же брокер: {fmt(v)}.", "uz": f"Sotuvchi va xaridor brokeri bir xil bo'lgan bitimlar: {fmt(v)} ta."}[lang]
        return sql, r, txt, n
    w.append("seller_inn = buyer_inn")''')

# new templates
rep("\n\n# ------------------------------------------------------------------ router", r'''

ACTIVITY = [(r"кластер|klaster", "Кластер"), (r"производственн", "Производственное предприятие"), (r"производител|ishlab chiqaruvchi", "Производитель"),
            (r"перерабат|qayta ishla", "Перерабатывающее предприятие"), (r"торгов\w* компан|savdo kompaniya", "Торговая компания"),
            (r"транспорт", "Транспортная компания"), (r"строител|qurilish", "Строительная компания")]


def tpl_directory(t, e, lang):
    w, n = [], []
    if has(t, r"брокер|broker"):
        if e.regions: w.append(f"\"Регион\" IN ({','.join(sqlq(r) for r in e.regions)})"); n.append("регион: " + ", ".join(e.regions))
        sql = f"SELECT count(*) FROM brokers {where(w)}"; r = q(sql)
        txt = {"ru": f"Брокерских контор: {fmt(r[0][0])}.", "uz": f"Broker idoralari: {fmt(r[0][0])} ta."}[lang]
        return sql, r, txt, n + ["справочник брокеров (brokers.csv)"]
    if e.regions: w.append(f"\"Регион\" IN ({','.join(sqlq(r) for r in e.regions)})"); n.append("регион регистрации: " + ", ".join(e.regions))
    if has(t, r"ндс|\bqqs\b|\bnds\b"):
        neg = has(t, r"не (плат|явля)|неплательщик|to'lamaydi|to'lamay|emas|без ндс")
        w.append(f"\"Плательщик НДС\"='{'Нет' if neg else 'Да'}'"); n.append("не плательщики НДС" if neg else "плательщики НДС")
    sd = side(t)
    if sd: w.append(f"\"Роль\"='{'Продавец' if sd == 'seller' else 'Покупатель'}'"); n.append("роль: " + ("продавец" if sd == "seller" else "покупатель"))
    for pat, val in ACTIVITY:
        if has(t, pat): w.append(f"\"Вид деятельности\"='{val}'"); n.append("вид деятельности: " + val); break
    sql = f"SELECT count(*) FROM participants {where(w)}"; r = q(sql)
    txt = {"ru": f"Компаний в справочнике участников: {fmt(r[0][0])}.", "uz": f"Ishtirokchilar ro'yxatidagi kompaniyalar: {fmt(r[0][0])} ta."}[lang]
    return sql, r, txt, n + ["справочник участников (participants.csv)"]


def tpl_list(t, e, lang):
    w, n = build_filters(t, e)
    u = _unit_filter(t) or ("штука" if has(t, r"штук|dona") else "тонна" if has(t, r"тонн|tonna") else None)
    if u: w.append(f"unit='{u}'"); n.append("единица: " + u)
    sql = f"SELECT product_base, count(*) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 2 DESC LIMIT 40"
    r = q(sql)
    if not r: return sql, r, None, n
    head = {"ru": f"Товары ({len(r)}):", "uz": f"Mahsulotlar ({len(r)} ta):"}[lang]
    return sql, r, head + "\n" + "\n".join(f"• {p} — {fmt(c)}" for p, c in r), n


def tpl_extreme(t, e, lang):
    mx = any(has(t, p) for f, p in STAT_FN if f == "max")
    price = has(t, r"цен|narx|стоимост")
    w, n = build_filters(t, e, settled=True)
    if price:
        w.append("currency='Узбекский сум'")
        u = _unit_filter(t)
        if u: w.append(f"unit='{u}'")
    col = "price_per_unit" if price else "amount_uzs"
    sql = f"SELECT deal_date, round({col},2), unit, deal_id, product_name FROM deals_enriched {where(w)} ORDER BY {col} {'DESC' if mx else 'ASC'} LIMIT 1"
    r = q(sql)
    if not r: return sql, r, None, n
    d, v, u, i, pn = r[0]
    what = {"ru": ("Максимальная" if mx else "Минимальная") + (" цена" if price else " сумма сделки"), "uz": ("Eng yuqori" if mx else "Eng past") + (" narx" if price else " bitim summasi")}[lang]
    val = f"{fmt(v,2)} " + ({"ru": "сум", "uz": "so'm"}[lang]) + (f" за 1 {u}" if price else "")
    txt = {"ru": f"{what}: {val} — {d:%d.%m.%Y}, сделка №{i} ({pn}).", "uz": f"{what}: {val.replace(' за 1 ', ' / 1 ')} — {d:%d.%m.%Y}, bitim №{i} ({pn})."}[lang]
    return sql, r, txt, n


def tpl_compare(t, e, lang):
    import copy
    rows_sql = []
    if len(e.regions) >= 2:
        e2 = copy.copy(e); e2.regions = []
        w, n = build_filters(t, e2)
        for rg in e.regions:
            rows_sql.append(f"SELECT {sqlq(rg)}, count(*), round(sum(amount_uzs) FILTER (WHERE status<>{sqlq(ANNUL)})) FROM deals_enriched {where(w + ['region=' + sqlq(rg)])}")
    elif len(e.products) >= 2:
        e2 = copy.copy(e); e2.products = []
        w, n = build_filters(t, e2)
        for pr in e.products:
            rows_sql.append(f"SELECT {sqlq(pr)}, count(*), round(sum(amount_uzs) FILTER (WHERE status<>{sqlq(ANNUL)})) FROM deals_enriched {where(w + [product_sql(pr)])}")
    else:
        return None, None, None, []
    sql = " UNION ALL ".join(rows_sql) + " ORDER BY 2 DESC"
    r = q(sql)
    lines = [f"• {a}: {fmt(b)} " + {"ru": "сделок, сумма ", "uz": "ta bitim, summa "}[lang] + f"{fmt(c or 0)} " + {"ru": "сум", "uz": "so'm"}[lang] for a, b, c in r]
    best = r[0][0] if r and r[0][1] else None
    head = {"ru": f"Сравнение (больше: {best}):" if best else "Сравнение: сделок нет ни в одном варианте.", "uz": f"Taqqoslash (ko'proq: {best}):" if best else "Taqqoslash: bitimlar yo'q."}[lang]
    n.append("сумма — без аннулированных сделок; регион — регион склада продавца")
    return sql, [x for x in r if x[1]] or r, head + "\n" + "\n".join(lines), n


# ------------------------------------------------------------------ router''')
rep("by_month=tpl_by_month, avg_deal=tpl_avg_deal, stat=tpl_stat)",
    "by_month=tpl_by_month, avg_deal=tpl_avg_deal, stat=tpl_stat,\n                 directory=tpl_directory, list=tpl_list, extreme=tpl_extreme, compare=tpl_compare)")

# distinct: product / region; more triggers
rep('''def tpl_distinct(t, e, lang):
    w, n = build_filters(t, e)
    if has(t, r"брокер|broker"):''', '''def tpl_distinct(t, e, lang):
    w, n = build_filters(t, e)
    if has(t, r"товар|продукт|mahsulot|tovar") and not has(t, r"компан|kompaniya|покупат|продав|xaridor|sotuvchi"):
        sql = f"SELECT count(DISTINCT product_base) FROM deals_enriched {where(w)}"; r = q(sql)
        return sql, r, {"ru": f"Различных наименований товара (базовое наименование в карточке контракта): {fmt(r[0][0])}.", "uz": f"Turli mahsulot nomlari: {fmt(r[0][0])} ta."}[lang], n + ["считаются различные базовые наименования; один товар может быть записан по-разному"]
    if has(t, r"регион|region|hudud|viloyat|област"):
        sql = f"SELECT count(DISTINCT region) FROM deals_enriched {where(w)}"; r = q(sql)
        return sql, r, {"ru": f"Регионов со сделками: {fmt(r[0][0])}.", "uz": f"Bitimlar bo'lgan hududlar: {fmt(r[0][0])} ta."}[lang], n + ["регион склада продавца"]
    if has(t, r"брокер|broker"):''')

# router rewrite of the head
rep('''def route(t, e):
    if has(t, r"\\bкурс|\\bkurs"): return "rate"
    if has(t, r"уникальн|различн|разных|''', '''EXT_W = r"когда|qachon|какого числа|в какой день|qaysi kun|в какую дату|какой день|какую дату"
MONEY_W = r"сумм|summa|\\bpul|so'm|\\bсум\\b|сумах|денег|деньг|долл|dollar|выручк|оборот"
UNIT_W = r"тонн|tonna|\\bкг\\b|килограмм|литр|litr|объ[её]м|hajm|\\bлот"


def route(t, e):
    if has(t, r"\\bкурс|\\bkurs"): return "rate"
    if has(t, COUNT_W) and has(t, r"брокер|broker|компан|kompaniya|участник|ishtirokchi|firma|xaridor|sotuvchi|покупател|продавц") and not has(t, MONEY_W + r"|сделк|сделок|bitim|savdo") and (
            has(t, REG_W + r"|справочник|ro'yxat|реестр") or has(t, r"ндс|\\bqqs\\b|\\bnds\\b") or (has(t, r"брокер|broker") and e.regions and not e.date_from) or any(has(t, p) for p, _ in ACTIVITY)): return "directory"
    if has(t, r"какие товары|какие продукты|список товаров|qaysi (tovar|mahsulot)lar|qanday (tovar|mahsulot)lar|qaysi tovar"): return "list"
    if (len(e.regions) >= 2 or len(e.products) >= 2) and has(t, r"сравн|solishtir|taqqosla|где больше|qaysi birida|\\bили\\b|\\byoki\\b|против|\\bvs\\b|\\bчем\\b"): return "compare"
    if has(t, r"продавц\\w*.{0,40}покупател|покупател\\w*.{0,40}продавц|ham sotuvchi.{0,20}ham xaridor") and has(t, r"есть ли|bormi|бывает|существу") and has(t, r"компан|kompaniya|участник"): return "self"
    if has(t, r"bir xil|одинаков|один и тот же|одним и тем же|совпада") and has(t, r"брокер|broker"): return "self"
    if has(t, r"уникальн|различн|разных|turli|har xil|noyob|сколько (видов|наименований|всего )?(товар|продукт|регион)|nechta (mahsulot|tovar|hudud|viloyat)|qancha (kompaniya|xaridor|sotuvchi)|nechta (turli )?(kompaniya|xaridor|sotuvchi)|''')
rep('''    if has(t, r"медиан|median|отклонени|stddev") or''', '''    if has(t, EXT_W) and any(has(t, p) for f, p in STAT_FN if f in ("min", "max")): return "extreme"
    if has(t, r"медиан|median|отклонени|stddev") or''')
rep('''    if e.brokers and has(t, r"назв|наимен|называ|nomi|кто так|что за"): return "broker"''',
    '''    if e.brokers and has(t, r"назв|наимен|называ|nomi|кто так|что за|qanaqa|qanday broker|haqida|какой это|кто это|ma'lumot|информац|расскажи"): return "broker"''')
rep('''    if has(t, r"недопостав|не поставлен|yetkazilmagan"): return "underdelivery"
    if has(t, r"(самая большая|самую большую|крупнейшая|крупнейшую|максимальн\\w*) сделк|eng katta bitim|eng yirik bitim"): return "largest"''',
'''    if has(t, r"недопостав|не поставлен|yetkazilmagan|осталось недо"): return "underdelivery"
    if has(t, r"фактически постав|поставлено|yetkazib berilgan|yetkazilgan") and has(t, r"\\bлот|lot|тонн|литр|tonna|объ|hajm|сколько|qancha"): return "underdelivery"
    if has(t, r"сделк|bitim") and has(t, r"самой крупной|самая крупная|самую крупную|крупнейш|самая большая|самую большую|самой большой|наибольш\\w* (по )?сумм|eng katta|eng yirik|максимальн\\w* (по )?сумм") and not has(t, r"покупател|продавц|брокер|регион|xaridor|sotuvchi|broker"): return "largest"
    if has(t, r"(самая большая|самую большую|крупнейшая|крупнейшую|максимальн\\w*) сделк|eng katta bitim|eng yirik bitim"): return "largest"''')
rep('''    if has(t, r"цен|narx|стоимость за|почём|почем"): return "avg_price"
    if has(t, SUM_W) and not has(t, r"сколько сделок|nechta bitim"): return "amount"''', '''    if has(t, r"цен|narx|стоимость за|почём|почем"): return "avg_price"
    if has(t, UNIT_W) and not has(t, MONEY_W): return "volume"
    if has(t, SUM_W) and not has(t, r"сколько сделок|nechta bitim"): return "amount"''')

# guards
rep('''    if has(t, r"почему|по какой причине|причин[аы]|nima uchun|\\bnega\\b"):''', '''    if has(t, NOT_TRADED) and not e.products:
        return nodata(lang, "product", "not-traded", x=re.search(NOT_TRADED, t).group(0))
    if has(t, FOREIGN):
        return nodata(lang, "foreign", "foreign")
    if has(t, r"почему|по какой причине|причин[аы]|nima uchun|\\bnega\\b|влиян|повлия|ta'sir|таъсир"):''')
rep('''r"\\bакци[ияйю]\\w*|дивиденд''', '''r"\\bакци[ияйю]\\w*|рейтинг|reyting|кредит|kredit|daromad|прибыл|foyda|\\bналог|soliq|зарплат|maosh|сотрудник|xodim|завод|zavod|фабрик|fabrika|погод|ob-havo|урожа|hosil|населени|aholi|\\bввп\\b|инфляц|inflyats|лицензи|litsenziya|сертифик|дивиденд''')
rep('''    "empty": {''', '''    "foreign": {"ru": "в данных только 14 регионов Узбекистана; сведений о других странах и городах, в том числе о странах назначения экспорта, нет", "uz": "ma'lumotlarda faqat O'zbekistonning 14 hududi bor; boshqa davlatlar va shaharlar haqida ma'lumot yo'q"},
    "empty": {''')
rep('''UNSUPPORTED = r"''', '''NOT_TRADED = r"золот|oltin|серебр|kumush|платин|\\bнефт(ь|и|ью)\\b|brent|\\bwti\\b|\\bуголь|\\bугля|ko'mir|кўмир|komir|кофе|qahva|какао|\\bчая?\\b|\\bchoy\\b|алмаз|olmos|биткоин|bitcoin|крипт|электроэнерг|elektr energ|\\bмяс[оа]\\b|go'sht|молок|\\bsut\\b|картоф|kartoshka|овощ|фрукт|\\bmeva|sabzavot|\\bавтомобил(ь|и|ей|я)\\b|avtomobil|недвижим|квартир|\\bхлопков\\w* волокн|paxta tolasi|пахта толаси"
FOREIGN = r"москв|moskva|росси[июя]|rossiya|казахстан|qozog'iston|кыргыз|qirg'iz|таджик|tojikiston|китай|xitoy|турци|turkiya|лондон|нью-йорк|афганистан|туркмен|turkman|европ|yevropa|\\bсша\\b|amerika|америк"
UNSUPPORTED = r"''')
open(p, 'w').write(s)

# ---------------- entities
p = 'agent/entities.py'; s = open(p).read()
rep('''    return " " + t.lower().replace("ё", "е")''', '''    t0 = t.lower()
    if re.search(r"[ўқғҳ]|\\b(энг|нечта|қанча|йил|бўйича|қайси|ким|билан|бўлган|сотган)\\b", t0):
        t0 = t0 + " || " + "".join(CYR2LAT.get(c, c) for c in t0)
    t = t0
    return " " + t.lower().replace("ё", "е")''')
rep('''def _norm(t: str) -> str:''', '''CYR2LAT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъьэюяўқғҳ",
    ["a","b","v","g","d","e","yo","j","z","i","y","k","l","m","n","o","p","r","s","t","u","f","x","ts","ch","sh","sh","'","","e","yu","ya","o'","q","g'","h"]))


def _norm(t: str) -> str:''')
rep('''        q = re.search(r"\\bq([1-4])\\b|([1-4])[\\s-]*(?:-?й|-?ой|-?м)?\\s*(?:квартал|chorak)|(?:квартал|chorak)\\s*([1-4])", t)''',
'''        q = re.search(r"\\bq([1-4])\\b|\\b([1-4])[\\s-]*(?:-?й|-?ой|-?м|-?chi)?\\s*(?:квартал|chora[kg])|(?:квартал\\w*|chora[kg]\\w*)\\s*([1-4])\\b", t)
        if not q:
            mm = re.search(r"\\b(iv|iii|ii|i)[\\s-]*(?:квартал|chora[kg])", t) or re.search(r"(перв|втор|трет|четв|birinchi|ikkinchi|uchinchi|to'rtinchi)\\w*\\s+(?:квартал|chora[kg])", t)
            if mm:
                qmap = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "перв": 1, "втор": 2, "трет": 3, "четв": 4, "birinchi": 1, "ikkinchi": 2, "uchinchi": 3, "to'rtinchi": 4}
                class _Q:
                    def __init__(s, n): s.n = n
                    def group(s, i): return str(s.n) if i == 1 else None
                q = _Q(qmap[mm.group(1)])''')
rep('''NOT_PRODUCT = re.compile(r"^(сумм|''', '''NOT_PRODUCT = re.compile(r"^(исполн|аннул|частич|оплач|состо|спот|форвард|фьюч|экспорт|внутр|shahar|viloyat|hudud|turi|tur\\b|mahsulot|davr|yil|oy\\b|kun|kompaniya|sotuvchi|xaridor|holat|narx|summa|hajm|base|product|bitim|savdo|сумм|''')
rep('''("пряжа хлопковая", "пряжа.*", ["пряж", "ip kalava", "kalava"]),''', '''("пряжа хлопковая", "пряжа.*", ["пряж", "ip kalava", "kalava", "paxta ip", "paxta ipi"]),''')
rep('''("медный катод", "(катод медный|медный катод).*", ["катод", "медн", "mis katod"]),''', '''("медный катод", "(катод медный|медный катод).*", ["катод", "медн", "mis katod", "katod", "мис катод"]),''')
rep('''("сахар", "сахар.*", ["сахар", "shakar"]),''', '''("сахар", "сахар.*", ["сахар", "shakar", " qand", "шакар"]),''')
open(p, 'w').write(s)
print("patched")
