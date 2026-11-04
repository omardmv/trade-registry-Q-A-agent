import re
def patch(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert a in s, f"{p}: MISSING {a[:80]}"
        s = s.replace(a, b)
    open(p, 'w').write(s)

patch('agent/entities.py', [
 # trubы before polyethylene; latin aliases
 ('''    ("полиэтилен", "полиэтилен.*", ["полиэтилен", "polietilen"]),''',
  '''    ("трубы", "труб.*", ["труб", "quvur", "truba", "polietilen truba", "полиэтиленовые труб", "полиэтиленовых труб"]),
    ("полиэтилен", "полиэтилен.*", ["полиэтилен", "polietilen"]),'''),
 ('''    ("трубы", "труб.*", ["труб", "quvur"]),\n''', ''),
 ('''    _periods(t, e)
    return e''', '''    # "хлопковый шрот / хлопковая пряжа / хлопковое масло": the adjective is not raw cotton
    if "хлопок-сырец" in e.products and len(e.products) > 1 and not re.search(r"хлопок|хлопка|хлопку|хлопком|хлопк[уе]|пахта|paxta(?! (ip|yog|shrot|kunjara))|xlopok", t):
        e.products.remove("хлопок-сырец")
    _periods(t, e)
    return e'''),
])
patch('agent/core.py', [
 # statuses
 ('''    if has(t, r"исполнен|bajarilgan"): return "status='Исполнен'", "статус: исполнен"''',
  '''    if has(t, r"статус\\w*\\s*«?оплачен|«оплачен»|\\bоплаченн\\w*\\b(?! и)|holati «?to'langan"): return "status='Оплачен'", "статус: оплачен (ещё не поставлен)"
    if has(t, r"статус\\w*\\s*«?заключ|«заключен»"): return "status='Заключен'", "статус: заключен"
    if has(t, r"исполнен|bajarilgan"): return "status='Исполнен'", "статус: исполнен"'''),
 # foreign: no USA (dollar context), more cities
 ('''|афганистан|туркмен|turkman|европ|yevropa|\\bсша\\b|amerika|америк"''', '''|афганистан|туркмен|turkman|европ|yevropa|петербург|peterburg|алмат|бишкек|душанбе|ашхабад|стамбул|istanbul|дубай|dubay|пекин|шанха|минск|киев|баку|тегеран"'''),
 # not traded: more commodities
 ('''|недвижим|квартир|\\bхлопков\\w* волокн|paxta tolasi|пахта толаси"''', '''|недвижим|квартир|\\bхлопков\\w* волокн|paxta tolasi|пахта толаси|\\bсо[яиюе]\\b|соев|\\bsoya|кукуруз|makkajo'xori|ячмен|\\barpa\\b|\\bовес|\\bовса|семен[аи] |urug'|шерст|\\bjun\\b|\\bкож[аи]\\b|шелк|\\bipak|табак|tamaki|виноград|\\buzum|гречк|чеснок|\\bмед\\b|\\basal\\b|рыб[аы]|baliq|яйц|tuxum|\\bсыр\\b|сливочн|железн\\w* руд|temir ruda|\\bруд[аы]|\\bуран|\\bлес\\b(?!о)|древесин"'''),
 # rate route wording + boundaries
 ('''    if has(t, r"\\bкурс|\\bkurs"): return "rate"''', '''    if has(t, r"\\bкурс|\\bkurs|сколько стои\\w* доллар|доллар стои|dollar narxi|dollar necha|dollar qancha|1 usd|за 1 доллар"): return "rate"'''),
 ('''def tpl_rate(t, e, lang):
    ext =''', '''def tpl_rate(t, e, lang):
    if e.date_from and has(t, r"начал|boshi|первый день"):
        sql = f"SELECT rate_date, uzs_per_usd FROM usd_rates WHERE rate_date >= DATE '{max(e.date_from, DATA_START)}' ORDER BY rate_date LIMIT 1"
        r = q(sql); return sql, r, {"ru": f"Курс доллара на {r[0][0]:%d.%m.%Y} (первый день данных в периоде): {fmt(r[0][1],2)} сум за 1 USD.", "uz": f"{r[0][0]:%d.%m.%Y} holatiga dollar kursi: 1 USD = {fmt(r[0][1],2)} so'm."}[lang], []
    if e.date_from and has(t, r"конце|конец|oxiri|последний день"):
        sql = f"SELECT rate_date, uzs_per_usd FROM usd_rates WHERE rate_date <= DATE '{min(e.date_to, DATA_END)}' ORDER BY rate_date DESC LIMIT 1"
        r = q(sql); return sql, r, {"ru": f"Курс доллара на {r[0][0]:%d.%m.%Y}: {fmt(r[0][1],2)} сум за 1 USD.", "uz": f"{r[0][0]:%d.%m.%Y} holatiga dollar kursi: 1 USD = {fmt(r[0][1],2)} so'm."}[lang], []
    ext ='''),
 # month leader wording
 ('''    if has(t, r"в каком месяце|в какой месяц|каком году|qaysi oy|qaysi yil|по месяцам|oylar bo'yicha"): return "by_month"''',
  '''    if has(t, r"в каком месяце|в какой месяц|каком году|qaysi oy|qaysi yil|по месяцам|oylar bo'yicha|\\boy\\b.{0,12}qaysi|\\byil\\b.{0,12}qaysi|какой месяц|месяц.{0,20}(больше|наибол|меньше)"): return "by_month"'''),
 # count words (Cyrillic uz)
 ('''COUNT_W = r"сколько|количеств|число|nechta|qancha|soni|есть ли|bormi"''', '''COUNT_W = r"сколько|количеств|число|nechta|necha|qancha|soni|есть ли|bormi|неча|нечта|қанча"'''),
 ('''SUM_W = r"сумм|summa|''', '''SUM_W = r"сумм|сумов|summa|so'mlik|потратил|заплатил|xarajat|sarfla|'''),
 ('''MONEY_W = r"сумм|summa|''', '''MONEY_W = r"сумм|сумов|summa|'''),
 # usd negation
 ('''def usd_wanted(t): return has(t, r"доллар|dollar|usd|\\$")''', '''def usd_wanted(t):
    if has(t, r"dollar emas|не в долларах|не доллар|в сумах|so'mda|so'mdagi|в сумовом"): return False
    return has(t, r"доллар|dollar|usd|\\$")'''),
 # extreme: cheap/expensive means price; per-unit filter
 ('''    price = has(t, r"цен|narx|стоимост")
    w, n = build_filters(t, e, settled=True)''', '''    price = has(t, r"цен|narx|стоимост|дешев|дорог|arzon|qimmat|за тонн|по тонн|за кг|за килограмм")
    w, n = build_filters(t, e, settled=True)'''),
 ('''        u = _unit_filter(t)
        if u: w.append(f"unit='{u}'")
    col = "price_per_unit" if price else "amount_uzs"''', '''        u = _unit_filter(t) or ("тонна" if has(t, r"тонн|tonna") else None)
        if u: w.append(f"unit='{u}'"); n.append("только сделки в единице: " + u)
    col = "price_per_unit" if price else "amount_uzs"'''),
 ('''    if has(t, EXT_W) and any(has(t, p) for f, p in STAT_FN if f in ("min", "max")): return "extreme"''',
  '''    if has(t, EXT_W) and (any(has(t, p) for f, p in STAT_FN if f in ("min", "max")) or has(t, r"самы\\w* (дешев|дорог)|eng (arzon|qimmat)")): return "extreme"'''),
 # volume: explicit unit (metres, litres, pieces...)
 ('''def _unit_filter(t):
    if has(t, r"килограмм|\\bкг\\b|\\bkg\\b|kilogramm"): return "килограмм"
    if has(t, r"литр|litr"): return "литр"
    return None''', '''def _unit_filter(t):
    if has(t, r"килограмм|\\bкг\\b|\\bkg\\b|kilogramm"): return "килограмм"
    if has(t, r"литр|litr"): return "литр"
    if has(t, r"куб\\.? ?метр|кубометр|m3|м3|kub metr"): return "куб.метр"
    if has(t, r"кв\\.? ?метр|m2|м2|kvadrat metr"): return "кв.метр"
    if has(t, r"(?<![\\w.])метр|\\bmetr\\b|\\bpog'onametr"): return "метр"
    if has(t, r"\\bштук|\\bdona\\b"): return "штука"
    return None'''),
 ('''    tonnes = has(t, r"тонн|tonna|\\bт\\b")''', '''    tonnes = has(t, r"тонн|tonna|\\bт\\b")
    uf = _unit_filter(t)
    if uf and uf != "килограмм": w.append(f"unit='{uf}'"); n.append("единица: " + uf)
    elif uf == "килограмм" and not tonnes: w.append("unit='килограмм'"); n.append("единица: килограмм")'''),
 # top: "где" means region
 ('''    if has(t, r"регион|region|viloyat|област|hudud"): return "region"''', '''    if has(t, r"регион|region|viloyat|област|hudud|\\bгде\\b|qayerda|qaysi joyda"): return "region"'''),
 ('''    if has(t, r"\\bтоп|\\btop\\b|больше всего|''', '''    if has(t, r"\\bгде\\b.{0,30}(больше|меньше)|qayerda.{0,30}(ko'p|kam)") and dim_of(t): return "top"
    if has(t, r"\\bтоп|\\btop\\b|больше всего|'''),
 # largest: lots
 ('''    sql = (f"SELECT deal_id, deal_date, product_name, round(amount_uzs), amount, currency, s.\\"Наименование\\", b.\\"Наименование\\" "''',
  '''    sql = (f"SELECT deal_id, deal_date, product_name, round(amount_uzs), amount, currency, s.\\"Наименование\\", b.\\"Наименование\\", qty_lots, lot_size, unit "'''),
 ('''+ f". Продавец: {x[6]}, покупатель: {x[7]}.",''', '''+ f"; {fmt(x[8])} лотов × {fmt(x[9])} {x[10]}. Продавец: {x[6]}, покупатель: {x[7]}.",'''),
 ('''+ f". Sotuvchi: {x[6]}, xaridor: {x[7]}."}[lang]''', '''+ f"; {fmt(x[8])} lot × {fmt(x[9])} {x[10]}. Sotuvchi: {x[6]}, xaridor: {x[7]}."}[lang]'''),
 # narrowing: split hyphenated tokens, normalise grade endings
 ('''    for tok in set(re.findall(r"[а-яa-z0-9ø][а-яa-z0-9ø,/-]*", tt)):''', '''    toks = set(re.findall(r"[а-яa-z0-9ø][а-яa-z0-9ø,/-]*", tt))
    toks |= {p for x in list(toks) if "-" in x and not any(c.isdigit() for c in x) for p in x.split("-") if len(p) >= 3}
    toks = {re.sub(r"(класс|сорт)\\w*$", r"\\1", x) if any(c.isdigit() for c in x) else x for x in toks}
    for tok in toks:'''),
 # directory: percent + count word variants
 ('''    if has(t, COUNT_W) and has(t, r"брокер|broker|компан|kompaniya|участник|ishtirokchi|firma|xaridor|sotuvchi|покупател|продавц") and not has(t, MONEY_W + r"|сделк|сделок|bitim|savdo") and (''',
  '''    if has(t, COUNT_W + r"|процент|дол[яю]\\b|foiz|ulush") and has(t, r"брокер|broker|компан|kompaniya|участник|ishtirokchi|firma|xaridor|sotuvchi|покупател|продавц") and not has(t, MONEY_W + r"|сделк|сделок|bitim|savdo") and ('''),
 ('''    sql = f"SELECT count(*) FROM participants {where(w)}"; r = q(sql)
    txt = {"ru": f"Компаний в справочнике участников: {fmt(r[0][0])}.", "uz": f"Ishtirokchilar ro'yxatidagi kompaniyalar: {fmt(r[0][0])} ta."}[lang]''',
  '''    if has(t, r"процент|дол[яю]\\b|foiz|ulush") and len(w) >= 1:
        base = [x for x in w if "НДС" not in x and "Вид деятельности" not in x]; cond = [x for x in w if x not in base] or w
        sql = f"SELECT round(100.0 * count(*) FILTER (WHERE {' AND '.join(cond)}) / count(*), 2), count(*) FILTER (WHERE {' AND '.join(cond)}), count(*) FROM participants {where(base)}"
        r = q(sql)
        txt = {"ru": f"Доля: {r[0][0]}% ({fmt(r[0][1])} из {fmt(r[0][2])} компаний справочника).", "uz": f"Ulushi: {r[0][0]}% ({fmt(r[0][2])} tadan {fmt(r[0][1])} ta kompaniya)."}[lang]
        return sql, r, txt, n + ["справочник участников (participants.csv)"]
    sql = f"SELECT count(*) FROM participants {where(w)}"; r = q(sql)
    txt = {"ru": f"Компаний в справочнике участников: {fmt(r[0][0])}.", "uz": f"Ishtirokchilar ro'yxatidagi kompaniyalar: {fmt(r[0][0])} ta."}[lang]'''),
 ('''|кредит|kredit|daromad|прибыл|''', '''|кредит|kredit|daromad|прибыл|\\bадрес компан|адрес офис|юридическ\\w* адрес|ofis manzil|yuridik manzil|'''),
])
print("ok")
