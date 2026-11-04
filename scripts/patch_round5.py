def patch(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert a in s, f"{p}: MISSING {a[:80]}"
        s = s.replace(a, b)
    open(p, 'w').write(s)

patch('agent/entities.py', [
 ('''def _norm(t: str) -> str:''', '''RU_ORD = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6}
UZ_ORD = {"birinchi": 1, "ikkinchi": 2, "uchinchi": 3, "to'rtinchi": 4, "tortinchi": 4, "beshinchi": 5, "oltinchi": 6,
          "bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "tort": 4, "besh": 5, "olti": 6}


def _years_from_words(t: str) -> str:
    """'две тысячи двадцать четвертом' / 'ikki ming yigirma to'rtinchi' -> 2024"""
    t = re.sub(r"(?:две|двух)\\s+тысяч\\w*\\s+двадцат\\w*\\s+(перв|втор|трет|четв|пят|шест)\\w*",
               lambda m: str(2020 + RU_ORD[m.group(1)]), t)
    t = re.sub(r"ikki\\s+ming\\s+yigirma\\s+(birinchi|ikkinchi|uchinchi|to'rtinchi|tortinchi|beshinchi|oltinchi|bir|ikki|uch|to'rt|tort|besh|olti)\\b",
               lambda m: str(2020 + UZ_ORD[m.group(1)]), t)
    return t


def _norm(t: str) -> str:'''),
 ('''    t0 = t.lower()
''', '''    t0 = t.lower()
    t0 = t0.replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("`", "'")
'''),
 ('''        t0 = t0 + " || " + "".join(CYR2LAT.get(c, c) for c in t0)
    t = t0''', '''        t0 = t0 + " || " + "".join(CYR2LAT.get(c, c) for c in t0)
    t = _years_from_words(_years_from_words(t0).replace("ё", "е"))'''),
])

patch('agent/core.py', [
 # closed-world check before templates
 ('''    # ---- templates
    r = route(t, e)''', '''    # ---- closed-world vocabulary: refuse questions about things the data does not contain
    unk = unknown_words(t)
    if unk:
        a = nodata(lang, "unknown", "unknown-words", x=", ".join(f"«{w}»" for w in unk[:4]))
        return a
    # ---- templates
    r = route(t, e)'''),
 ('''    "empty": {''', '''    "unknown": {"ru": "в данных нет сведений о: {x}. В данных есть только сделки (дата, товар, количество, сумма, цена, регион склада, статус, тип и форма контракта, даты оплаты и поставки), участники (ИНН, название, регион, вид деятельности, роль, НДС), брокеры и курс доллара",
                "uz": "ma'lumotlarda quyidagilar yo'q: {x}. Ma'lumotlarda faqat bitimlar (sana, mahsulot, miqdor, summa, narx, ombor hududi, holat, shartnoma turi va shakli, to'lov va yetkazish sanalari), ishtirokchilar (STIR, nomi, hududi, faoliyat turi, roli, QQS), brokerlar va dollar kursi bor"},
    "currency": {"ru": "в данных есть только курс доллара США к суму; курсов других валют нет", "uz": "ma'lumotlarda faqat AQSh dollarining so'mga kursi bor; boshqa valyutalar kursi yo'q"},
    "empty": {'''),
 ('''from .entities import extract, product_sql, _norm, DATA_START, DATA_END, PRODUCTS''', '''from .entities import extract, product_sql, _norm, DATA_START, DATA_END, PRODUCTS, REGIONS, MONTHS
from .lexicon import unknown_words, add_known'''),
 ('''def _load_companies():
    for inn, name in _con.execute('select "ИНН","Наименование" from participants').fetchall():
        c = _core(name)
        if len(c) >= 5: _companies.append((c, inn, name))''', '''def _load_companies():
    for inn, name in _con.execute('select "ИНН","Наименование" from participants').fetchall():
        c = _core(name)
        if len(c) >= 5: _companies.append((c, inn, name))
        add_known(re.findall(r"[a-zа-я'ўқғҳ]{3,}", c))
    for (b,) in _con.execute('select "Наименование" from brokers').fetchall():
        add_known(re.findall(r"[a-zа-я'ўқғҳ]{3,}", _core(b)))
    for (pn,) in _con.execute("select distinct lower(product_name) from deals").fetchall():
        add_known(re.findall(r"[a-zа-я'ёø]{3,}", pn))
    add_known(a.strip() for _, _, al in PRODUCTS for a in al)
    add_known(a for al in REGIONS.values() for a in al)
    add_known(a for m in MONTHS for a in m)'''),
 # other currencies
 ('''    if has(t, FOREIGN):''', '''    if has(t, r"\\bевро|\\beuro|\\byevro|\\bрубл|\\brubl|\\bюан|\\byuan|\\bтенге|\\btenge|\\bфунт|\\bиен|\\bwon\\b"):
        return nodata(lang, "currency", "currency")
    if has(t, FOREIGN):'''),
 # share: denominator keeps entity filters, numerator = status/type/form condition
 ('''    w, n = build_filters(t, e)
    b = _base_where(t, e)
    extra = [x for x in w if x not in b]''', '''    w, n = build_filters(t, e)
    COND = ("status", "contract_type", "contract_form", "auction_type", "currency")
    b = [x for x in w if not x.startswith(COND)]
    extra = [x for x in w if x.startswith(COND)]
    if not extra:
        b = _base_where(t, e); extra = [x for x in w if x not in b]'''),
 # top lists always carry the deal count too
 ('''    if len(e.products) == 1 and not by_count:
        extra += ", round(sum(CASE''', '''    if not by_count: extra += ", count(*)"
    if len(e.products) == 1 and not by_count:
        extra += ", round(sum(CASE'''),
 # grade codes like "pp 0350", "hd 5502", "м 100"
 ('''    tt = re.sub(r"\\b(аи|ai)[\\s-]?(\\d{2})\\b", r"аи-\\2", tt)''', '''    tt = re.sub(r"\\b(аи|ai)[\\s-]?(\\d{2})\\b", r"аи-\\2", tt)
    tt = re.sub(r"\\b(pp|hd|ll|pe|м|m|вр)[\\s-]?(\\d{1,4})\\b", lambda m: m.group(1).replace("m", "м") + "-" + m.group(2), tt)'''),
])
print("ok")
