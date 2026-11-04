"""Question -> answer. Order: guards -> rule router + SQL templates -> LLM text-to-SQL fallback.

Every answer carries the SQL that produced it and a route label, so the basis is always visible.
Numbers in answers come only from query results; the LLM never writes a number into an answer.
"""
import json, re, time, urllib.request
from dataclasses import dataclass, field
import duckdb
from .entities import extract, product_sql, _norm, DATA_START, DATA_END, PRODUCTS, REGIONS, MONTHS
from .lexicon import unknown_words, add_known
from . import trace as TR

DB = "data/uzex_v2.duckdb"
LLM_MODEL = "qwen2.5:7b"
ANNUL = "Аннулирован(Не оплачен)"
_con = None


def con():
    global _con
    if _con is None:
        _con = duckdb.connect(DB, read_only=True)
        _load_companies()
    return _con


_companies = []  # (core lowercase name, inn, full name)


def _core(name: str) -> str:
    n = _norm(name).replace('"', " ")
    n = re.sub(r"\b(мчж|ооо|ок|ао|ачж|mchj|mas'uliyati cheklangan jamiyati|masuliyati cheklangan jamiyati|ok|aj|xk|qk|fx|оилавий корхона)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _load_companies():
    for inn, name in _con.execute('select "ИНН","Наименование" from participants').fetchall():
        c = _core(name)
        if len(c) >= 5: _companies.append((c, inn, name))
        add_known(re.findall(r"[a-zа-я'ўқғҳ]{3,}", c))
    for (b,) in _con.execute('select "Наименование" from brokers').fetchall():
        add_known(re.findall(r"[a-zа-я'ўқғҳ]{3,}", _core(b)))
    for (pn,) in _con.execute("select distinct lower(product_name) from deals").fetchall():
        add_known(re.findall(r"[a-zа-я'ёø]{3,}", pn), small=True)
    add_known((w for _, _, al in PRODUCTS for a in al for w in a.split()), small=True)
    add_known((w for al in REGIONS.values() for a in al for w in a.split()), small=True)
    add_known((a for m in MONTHS for a in m), small=True)


@dataclass
class Answer:
    lang: str
    text: str
    sql: str = ""
    rows: list = field(default_factory=list)
    route: str = ""          # template:<name> | fallback:llm | guard:<why>
    confidence: str = "high"  # high | low | none
    no_data: bool = False
    ms: float = 0.0
    trace: dict = field(default_factory=dict)


def lang_of(q: str) -> str:
    lat = len(re.findall(r"[a-zA-Z]", q)); cyr = len(re.findall(r"[а-яА-ЯёЁўқғҳЎҚҒҲ]", q))
    if re.search(r"[ўқғҳЎҚҒҲ]", q): return "uz"
    return "uz" if lat > cyr else "ru"


T = {  # minimal bilingual phrasing
    "nodata": {"ru": "В данных нет ответа на этот вопрос: {why}.", "uz": "Ma'lumotlarda bu savolga javob yo'q: {why}."},
    "range": {"ru": "данные охватывают только период с 03.01.2022 по 30.10.2026", "uz": "ma'lumotlar faqat 03.01.2022 – 30.10.2026 davrini qamraydi"},
    "forecast": {"ru": "в данных только прошлые сделки, прогнозов нет", "uz": "ma'lumotlarda faqat o'tgan bitimlar bor, prognoz yo'q"},
    "field": {"ru": "такого поля в данных нет (есть: название, ИНН, регион, вид деятельности, роль)", "uz": "bunday maydon ma'lumotlarda yo'q (bor: nomi, STIR, hudud, faoliyat turi, roli)"},
    "inn": {"ru": "компании с ИНН {x} в данных нет", "uz": "STIR {x} bo'lgan kompaniya ma'lumotlarda yo'q"},
    "broker": {"ru": "брокера {x} в данных нет (есть BK001–BK120)", "uz": "{x} broker ma'lumotlarda yo'q (BK001–BK120 bor)"},
    "product": {"ru": "товар «{x}» на бирже в этих данных не торговался", "uz": "«{x}» mahsuloti bu ma'lumotlarda savdoga chiqmagan"},
    "why": {"ru": "в данных есть только факты сделок, причины в них не записаны (могу показать, что именно изменилось)", "uz": "ma'lumotlarda faqat bitim faktlari bor, sabablari yozilmagan (nima o'zgarganini ko'rsata olaman)"},
    "foreign": {"ru": "в данных только 14 регионов Узбекистана; сведений о других странах и городах, в том числе о странах назначения экспорта, нет", "uz": "ma'lumotlarda faqat O'zbekistonning 14 hududi bor; boshqa davlatlar va shaharlar haqida ma'lumot yo'q"},
    "unknown": {"ru": "в данных нет сведений о: {x}. В данных есть только сделки (дата, товар, количество, сумма, цена, регион склада, статус, тип и форма контракта, даты оплаты и поставки), участники (ИНН, название, регион, вид деятельности, роль, НДС), брокеры и курс доллара",
                "uz": "ma'lumotlarda quyidagilar yo'q: {x}. Ma'lumotlarda faqat bitimlar (sana, mahsulot, miqdor, summa, narx, ombor hududi, holat, shartnoma turi va shakli, to'lov va yetkazish sanalari), ishtirokchilar (STIR, nomi, hududi, faoliyat turi, roli, QQS), brokerlar va dollar kursi bor"},
    "company": {"ru": "компании «{x}» нет в справочнике участников", "uz": "«{x}» kompaniyasi ishtirokchilar ro'yxatida yo'q"},
    "currency": {"ru": "в данных есть только курс доллара США к суму; курсов других валют нет", "uz": "ma'lumotlarda faqat AQSh dollarining so'mga kursi bor; boshqa valyutalar kursi yo'q"},
    "empty": {"ru": "по заданным условиям сделок не найдено", "uz": "berilgan shartlar bo'yicha bitim topilmadi"},
    "unclear": {"ru": "не удалось надёжно понять вопрос — уточните, пожалуйста, что посчитать (количество, сумму, объём, цену) и за какой период", "uz": "savolni aniq tushunib bo'lmadi — nimani hisoblash kerakligini (soni, summasi, hajmi, narxi) va davrni aniqlashtiring"},
}


NOTE_UZ = [  # longest first
 ("сумма в сумах; долларовые (экспортные) сделки пересчитаны по курсу на дату сделки", "summa so'mda; dollar (eksport) bitimlari bitim sanasidagi kurs bo'yicha qayta hisoblangan"),
 ("сумма в долларах США по экспортным контрактам (без пересчёта)", "eksport shartnomalari bo'yicha AQSh dollarida (qayta hisoblanmagan)"),
 ("средневзвешенная цена = сумма ÷ объём (в валюте контракта)", "o'rtacha tortilgan narx = summa ÷ hajm (shartnoma valyutasida)"),
 ("простое среднее цен по сделкам", "bitimlar narxlarining oddiy o'rtachasi"),
 ("без аннулированных (не состоявшихся) сделок", "bekor qilingan (amalga oshmagan) bitimlarsiz"),
 ("по указанному сорту/марке", "ko'rsatilgan nav/marka bo'yicha"), ("регион регистрации", "ro'yxatdan o'tgan hudud"),
 ("брокер на любой стороне сделки (сделка засчитана обоим брокерам)", "bitimning istalgan tomonidagi broker (bitim ikkala brokerga hisoblangan)"),
 ("справочник участников (participants.csv)", "ishtirokchilar ro'yxati (participants.csv)"), ("справочник брокеров (brokers.csv)", "brokerlar ro'yxati (brokers.csv)"),
 ("по сумме сделок в сумах (экспорт пересчитан по курсу)", "so'mdagi summa bo'yicha (eksport kurs bo'yicha qayta hisoblangan)"),
 ("по сумме в сумах (экспорт пересчитан по курсу)", "so'mdagi summa bo'yicha (eksport kurs bo'yicha qayta hisoblangan)"),
 ("вопрос без явной метрики: показаны количество и сумма", "savolda aniq ko'rsatkich yo'q: soni va summasi ko'rsatildi"),
 ("считаются различные ИНН / коды, участвовавшие в сделках", "bitimlarda qatnashgan turli STIR / kodlar hisoblandi"),
 ("недопоставка = куплено лотов − поставлено лотов", "yetkazilmagan = sotib olingan lotlar − yetkazilgan lotlar"),
 ("вариантов названия в реестре учтены вместе", "nom varianti birga hisoblandi"),
 ("без аннулированных (не состоявшихся) сделок", "bekor qilingan (amalga oshmagan) bitimlarsiz"),
 ("2026 год неполный — данные до 30.10.2026", "2026-yil to'liq emas — ma'lumotlar 30.10.2026 gacha"),
 ("статус: аннулирован (не оплачен)", "holat: bekor qilingan (to'lanmagan)"),
 ("объём = количество лотов × размер лота", "hajm = lotlar soni × lot hajmi"),
 ("только сделки, где дата заполнена", "faqat sanasi to'ldirilgan bitimlar"),
 ("в сумах, экспорт пересчитан по курсу", "so'mda, eksport kurs bo'yicha qayta hisoblangan"),
 ("килограммы переведены в тонны", "kilogrammlar tonnaga o'tkazildi"),
 ("регион = регион склада продавца", "hudud = sotuvchi ombori hududi"),
 ("регион склада продавца", "sotuvchi ombori hududi"), ("регион компании", "kompaniya hududi"),
 ("доля по количеству сделок", "bitimlar soni bo'yicha ulush"), ("доля по сумме в сумах", "so'mdagi summa bo'yicha ulush"),
 ("по количеству сделок", "bitimlar soni bo'yicha"), ("по сумме в сумах", "so'mdagi summa bo'yicha"),
 ("статус: исполнен частично", "holat: qisman bajarilgan"), ("статус: исполнен", "holat: bajarilgan"),
 ("(продавец или покупатель)", "(sotuvchi yoki xaridor)"), ("как продавец", "sotuvchi sifatida"), ("как покупатель", "xaridor sifatida"),
 ("(любая сторона)", "(istalgan tomon)"), ("экспортные", "eksport"), ("внутренние", "ichki"), ("форвард", "forvard"), ("фьючерс", "fyuchers"),
 ("период", "davr"), ("товар:", "mahsulot:"), ("брокер", "broker"), ("ИНН", "STIR"),
]
UNITS = {"ru": {"тонна": "т", "килограмм": "кг", "литр": "л", "штука": "шт.", "куб.метр": "м³", "кв.метр": "м²", "метр": "м"},
         "uz": {"тонна": "t", "килограмм": "kg", "литр": "l", "штука": "dona", "куб.метр": "m³", "кв.метр": "m²", "метр": "m"}}


def localize(txt, lang):
    for k, v in sorted(UNITS[lang].items(), key=lambda x: -len(x[0])):
        txt = re.sub(r"(\d|за 1|1) " + re.escape(k) + r"(?![а-я])", lambda m: f"{m.group(1)} {v}", txt)
    if lang == "uz":
        txt = txt.replace("за 1 ", "1 ") 
        for a, b in NOTE_UZ: txt = txt.replace(a, b)
    return txt


def nodata(lang, why_key, route, **kw):
    why = T[why_key][lang].format(**kw)
    TR.step("refuse", reason=route, why_key=why_key, detail=kw)
    return Answer(lang, T["nodata"][lang].format(why=why), route=f"guard:{route}", confidence="none", no_data=True)


def fmt(x, dec=0):
    if x is None: return "—"
    if isinstance(x, float) or isinstance(x, int):
        s = f"{x:,.{dec}f}".replace(",", " ")
        return s
    return str(x)


# ------------------------------------------------------------------ filters
def has(t, pat): return re.search(pat, t) is not None


def side(t):
    s = has(t, r"продав|sotuvchi|продал|sotgan|продаж|sotish"); b = has(t, r"покупат|xaridor|xarid qil|харид|купил|sotib ol|закуп|покупк|приобр|потратил|sarflag")
    return "seller" if s and not b else "buyer" if b and not s else None


def sqlq(x): return "'" + str(x).replace("'", "''") + "'"


NARROW_STOP = ("перв", "втор", "трет", "четв", "квар", "год", "меся", "сдел", "скол", "всег", "прода", "купи", "цен", "сред", "тонн", "объ")


def narrow(t, product, bases):
    """If the question names a specific grade/variant, keep only base names containing it."""
    from .entities import PRODUCTS as P
    aliases = [a.strip() for n, _, al in P if n == product for a in al]
    tt = re.sub(r"(\d)\.(\d)", r"\1,\2", t)
    tt = re.sub(r"\b(аи|ai)[\s-]?(\d{2})\b", r"аи-\2", tt)
    tt = re.sub(r"\b(pp|hd|ll|pe|м|m|вр)[\s-]?(\d{1,4})\b", lambda m: m.group(1).replace("m", "м") + "-" + m.group(2), tt)
    quals = []
    toks = set(re.findall(r"[а-яa-z0-9ø][а-яa-z0-9ø,/-]*", tt))
    toks |= {p for x in list(toks) if "-" in x and not any(c.isdigit() for c in x) for p in x.split("-") if len(p) >= 3}
    toks = {re.sub(r"(класс|сорт)\w*$", r"\1", x) if any(c.isdigit() for c in x) else x for x in toks}
    for tok in toks:
        if tok.isdigit() or len(tok) < 3 or any(a and a in tok for a in aliases): continue
        if any(c.isdigit() for c in tok):
            stem = tok.translate(str.maketrans("aekmhopctxyb", "аекмнорстхуь"))  # latin look-alikes in grade codes (m00k -> м00к)
        else:
            stem = tok[:max(3, len(tok) - 3)]
        if len(stem) < 3 or stem.startswith(NARROW_STOP): continue
        for cand in [stem, tok]:
            hit = [b for b in bases if cand in b]
            if 0 < len(hit) < len(bases): quals.append(cand); break
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
    if has(t, r"статус\w*\s*«?оплачен|«оплачен»|\bоплаченн\w*\b(?! и)|holati «?to'langan"): return "status='Оплачен'", "статус: оплачен (ещё не поставлен)"
    if has(t, r"статус\w*\s*«?заключ|«заключен»"): return "status='Заключен'", "статус: заключен"
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


def where(w): return ("WHERE " + " AND ".join(w)) if w else ""


def usd_wanted(t):
    if has(t, r"dollar emas|не в долларах|не доллар|в сумах|so'mda|so'mdagi|в сумовом"): return False
    return has(t, r"доллар|dollar|usd|\$")


# ------------------------------------------------------------------ templates
def q(sql):
    t0 = time.perf_counter()
    rows = con().cursor().execute(sql).fetchall()   # cursor per query: safe under a threaded web server
    TR.step("sql", sql=sql, rows=len(rows), db_ms=round((time.perf_counter() - t0) * 1000, 2), sample=rows[:3])
    return rows


def tpl_count(t, e, lang):
    w, n = build_filters(t, e)
    split = not any("status" in x for x in w)
    sql = f"SELECT count(*){', count(*) FILTER (WHERE status<>' + sqlq(ANNUL) + ')' if split else ''} FROM deals_enriched {where(w)}"
    r = q(sql); v = r[0][0]
    txt = {"ru": f"Количество сделок: {fmt(v)}.", "uz": f"Bitimlar soni: {fmt(v)} ta."}[lang]
    if split and v and r[0][1] != v:
        txt += {"ru": f" Из них состоялось {fmt(r[0][1])}, аннулировано (не оплачено) {fmt(v - r[0][1])}.",
                "uz": f" Shundan amalga oshgan {fmt(r[0][1])} ta, bekor qilingan (to'lanmagan) {fmt(v - r[0][1])} ta."}[lang]
    if has(t, r"есть ли|бывают ли|bormi") and v == 0:
        txt = {"ru": "Нет, таких сделок в данных нет (0).", "uz": "Yo'q, bunday bitimlar yo'q (0)."}[lang]
    return sql, r, txt, n


def tpl_amount(t, e, lang):
    w, n = build_filters(t, e, settled=True)
    if usd_wanted(t):
        w.append("currency='Доллар США'")
        sql = f"SELECT round(sum(amount),2), count(*) FROM deals_enriched {where(w)}"
        r = q(sql); n.append("сумма в долларах США по экспортным контрактам (без пересчёта)")
        txt = {"ru": f"Сумма сделок: {fmt(r[0][0],2)} USD ({fmt(r[0][1])} сделок).", "uz": f"Bitimlar summasi: {fmt(r[0][0],2)} USD ({fmt(r[0][1])} ta bitim)."}[lang]
    else:
        sql = f"SELECT round(sum(amount_uzs)), count(*), count(*) FILTER (WHERE currency='Доллар США') FROM deals_enriched {where(w)}"
        r = q(sql); n.append("сумма в сумах; долларовые (экспортные) сделки пересчитаны по курсу на дату сделки")
        txt = {"ru": f"Сумма сделок: {fmt(r[0][0])} сум ({fmt(r[0][1])} сделок).", "uz": f"Bitimlar summasi: {fmt(r[0][0])} so'm ({fmt(r[0][1])} ta bitim)."}[lang]
    return sql, r, txt, n


def _unit_filter(t):
    if has(t, r"килограмм|\bкг\b|\bkg\b|kilogramm"): return "килограмм"
    if has(t, r"литр|litr"): return "литр"
    if has(t, r"куб\.? ?метр|кубометр|m3|м3|kub metr"): return "куб.метр"
    if has(t, r"кв\.? ?метр|m2|м2|kvadrat metr"): return "кв.метр"
    if has(t, r"(?<![\w.])метр|\bmetr\b|\bpog'onametr"): return "метр"
    if has(t, r"\bштук|\bdona\b"): return "штука"
    return None


def tpl_volume(t, e, lang):
    w, n = build_filters(t, e, settled=True)
    tonnes = has(t, r"тонн|tonna|\bт\b")
    uf = _unit_filter(t)
    if uf and uf != "килограмм": w.append(f"unit='{uf}'"); n.append("единица: " + uf)
    elif uf == "килограмм" and not tonnes: w.append("unit='килограмм'"); n.append("единица: килограмм")
    vol = "CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END" if tonnes else "volume_units"
    unit = "CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END" if tonnes else "unit"
    sql = f"SELECT {unit} AS u, round(sum({vol}),2), count(*) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 2 DESC"
    r = q(sql); n.append("объём = количество лотов × размер лота")
    if tonnes: n.append("килограммы переведены в тонны")
    if not r: return sql, r, None, n
    parts = [f"{fmt(v)} {u}" for u, v, _ in r]
    txt = {"ru": "Объём: " + "; ".join(parts) + f" ({fmt(sum(x[2] for x in r))} сделок).",
           "uz": "Hajmi: " + "; ".join(parts) + f" ({fmt(sum(x[2] for x in r))} ta bitim)."}[lang]
    return sql, r, txt, n


def tpl_avg_price(t, e, lang):
    w, n = build_filters(t, e, settled=True)
    cur = "Доллар США" if usd_wanted(t) else "Узбекский сум"
    w.append(f"currency='{cur}'")
    u = _unit_filter(t)
    if u: w.append(f"unit='{u}'")
    sql = f"SELECT unit, round(sum(amount)/sum(volume_units),2), count(*), round(avg(price_per_unit),2) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 3 DESC"
    r = q(sql); n.append("средневзвешенная цена = сумма ÷ объём (в валюте контракта)")
    if r and len(r) == 1: n.append(f"простое среднее цен по сделкам: {fmt(r[0][3],2)}")
    if not r: return sql, r, None, n
    c = "USD" if cur == "Доллар США" else {"ru": "сум", "uz": "so'm"}[lang]
    parts = [f"{fmt(p,2)} {c} за 1 {un}" for un, p, *_ in r]
    txt = {"ru": "Средняя цена: " + "; ".join(parts) + f" ({fmt(sum(x[2] for x in r))} сделок).",
           "uz": "O'rtacha narx: " + "; ".join(p.replace(" за 1 ", " / 1 ") for p in parts) + f" ({fmt(sum(x[2] for x in r))} ta bitim)."}[lang]
    return sql, r, txt, n


DIM = {
    "region": ("region", "region", None),
    "seller": ("p.\"Наименование\"", "seller_inn", "seller_inn"),
    "buyer": ("p.\"Наименование\"", "buyer_inn", "buyer_inn"),
    "seller_broker": ("seller_broker", "seller_broker", None),
    "buyer_broker": ("buyer_broker", "buyer_broker", None),
    "product": ("product_base", "product_base", None),
    "any_broker": ("code", "code", None),
}


def dim_of(t):
    if has(t, r"регион|region|viloyat|област|hudud|\bгде\b|qayerda|qaysi joyda"): return "region"
    if has(t, r"брокер|broker"): return {"buyer": "buyer_broker", "seller": "seller_broker"}.get(side(t), "any_broker")
    if has(t, r"товар|продукт|mahsulot|tovar"): return "product"
    if has(t, r"покупат|xaridor|xarid qil|купил|sotib ol|приобр|закуп"): return "buyer"
    if has(t, r"продав|sotuvchi|продал|sotgan|компан|kompaniya|участник|фирм"): return "seller"
    return None


def tpl_top(t, e, lang):
    d = dim_of(t)
    if not d: return None, None, None, []
    label, key, pjoin = DIM[d]
    by_count = has(t, r"чаще|активн|faol|количеств|nechta|по сделкам|по числу|по количеству|ko'p bitim|сделок больше|tez-tez|savdo qilingan|торгуем")
    by_vol = (not by_count) and has(t, r"тонн|tonna|\bкг\b|килограмм|литр|объ[её]м|hajm") and not has(t, r"сумм|summa|pul|so'm|\bсум\b|денег|долл")
    w, n = build_filters(t, e, dim=d if d in ("buyer", "seller") else None, settled=not by_count)
    if d == "any_broker":
        k = e.top_n or (1 if has(t, r"какой|какая|кто|самы|\beng\b|qaysi|\bkim\b|лидер") else 5)
        m = "count(*)" if by_count else "round(sum(amount_uzs))"
        sub = f"SELECT seller_broker AS code, * FROM deals_enriched {where(w)} UNION ALL SELECT buyer_broker AS code, * FROM deals_enriched {where(w)}"
        sql = f"SELECT x.code, {m} AS v, b.\"Наименование\" FROM ({sub}) x JOIN brokers b ON b.\"Код брокера\"=x.code GROUP BY 1, 3 ORDER BY v DESC LIMIT {k}"
        r = q(sql)
        n.append("брокер на любой стороне сделки (сделка засчитана обоим брокерам)")
        n.append("по количеству сделок" if by_count else "по сумме сделок в сумах (экспорт пересчитан по курсу)")
        unit = {"ru": " сделок", "uz": " ta bitim"}[lang] if by_count else {"ru": " сум", "uz": " so'm"}[lang]
        return sql, r, {"ru": "Результат:", "uz": "Natija:"}[lang] + "\n" + "\n".join(f"{i+1}. {x[0]} {x[2]} — {fmt(x[1])}{unit}" for i, x in enumerate(r)), n
    metric = "count(*)" if by_count else ("round(sum(CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END),2)" if by_vol else "round(sum(amount_uzs))")
    k = e.top_n or (1 if has(t, r"какой|какая|кто|самы|\beng\b|крупнейш(ий|ая|ее)|qaysi|\bkim\b|лидер") else 5)
    join = f"JOIN participants p ON p.\"ИНН\"=d.{pjoin}" if pjoin else ""
    if d.endswith("broker"): join = f"JOIN brokers b ON b.\"Код брокера\"=d.{key}"
    grp = f"{label}, d.{key}" if pjoin else label
    extra = f", d.{key}" if pjoin else ""
    if d.endswith("broker"): grp, extra = f"{label}, b.\"Наименование\"", ", b.\"Наименование\""
    if not by_count: extra += ", count(*)"
    if len(e.products) == 1 and not by_count:
        extra += ", round(sum(CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END),2), round(sum(volume_units),2), round(sum(amount_uzs))"
    sql = f"SELECT {label} AS name, {metric} AS v{extra} FROM deals_enriched d {join} {where(w)} GROUP BY {grp} ORDER BY v DESC LIMIT {k}"
    r = q(sql)
    n.append("по количеству сделок" if by_count else ("по объёму (кг переведены в тонны)" if by_vol else "по сумме сделок в сумах (экспорт пересчитан по курсу)"))
    if d == "region": n.append("регион = регион склада продавца")
    unit = "" if by_count else (" т" if by_vol else {"ru": " сум", "uz": " so'm"}[lang])
    lines = [f"{i+1}. {x[0]}" + (f" (ИНН {x[2]})" if pjoin else f" {x[2]}" if d.endswith("broker") else "") + f" — {fmt(x[1])}{unit}" + ("" if not by_count else {"ru": " сделок", "uz": " ta bitim"}[lang]) for i, x in enumerate(r)]
    head = {"ru": "Результат:", "uz": "Natija:"}[lang]
    return sql, r, head + "\n" + "\n".join(lines), n


def tpl_largest(t, e, lang):
    w, n = build_filters(t, e, settled=True)
    sql = (f"SELECT deal_id, deal_date, product_name, round(amount_uzs), amount, currency, s.\"Наименование\", b.\"Наименование\", qty_lots, lot_size, unit "
           f"FROM deals_enriched d JOIN participants s ON s.\"ИНН\"=d.seller_inn JOIN participants b ON b.\"ИНН\"=d.buyer_inn {where(w)} ORDER BY amount_uzs DESC LIMIT 1")
    r = q(sql)
    if not r: return sql, r, None, n
    x = r[0]; n.append("по сумме в сумах (экспорт пересчитан по курсу)")
    txt = {"ru": f"Крупнейшая сделка №{x[0]} от {x[1]:%d.%m.%Y}: {x[2]}, сумма {fmt(x[3])} сум" + (f" ({fmt(x[4],2)} USD)" if x[5] == 'Доллар США' else "") + f"; {fmt(x[8])} лотов × {fmt(x[9])} {x[10]}. Продавец: {x[6]}, покупатель: {x[7]}.",
           "uz": f"Eng yirik bitim №{x[0]}, {x[1]:%d.%m.%Y}: {x[2]}, summa {fmt(x[3])} so'm" + (f" ({fmt(x[4],2)} USD)" if x[5] == 'Доллар США' else "") + f"; {fmt(x[8])} lot × {fmt(x[9])} {x[10]}. Sotuvchi: {x[6]}, xaridor: {x[7]}."}[lang]
    return sql, r, txt, n


def tpl_status(t, e, lang):
    w, n = build_filters(t, e)
    sql = f"SELECT status, count(*), round(sum(amount_uzs)) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 2 DESC"
    r = q(sql)
    tot = sum(x[1] for x in r) or 1
    lines = [f"• {s}: {fmt(c)} ({c*100/tot:.1f}%)" for s, c, _ in r]
    head = {"ru": f"Состояние сделок ({fmt(tot)} всего), на 30.10.2026:", "uz": f"Bitimlar holati (jami {fmt(tot)}), 30.10.2026 holatiga:"}[lang]
    return sql, r, head + "\n" + "\n".join(lines), n


def tpl_summary(t, e, lang):
    w, n = build_filters(t, e, settled=True)
    sql = f"SELECT count(*), round(sum(amount_uzs)), round(sum(amount_uzs)/count(*)) FROM deals_enriched {where(w)}"
    r = q(sql)
    if not r[0][0]: return sql, [], None, n
    txt = {"ru": f"Сделок: {fmt(r[0][0])}; общая сумма {fmt(r[0][1])} сум; средняя сделка {fmt(r[0][2])} сум.",
           "uz": f"Bitimlar: {fmt(r[0][0])} ta; jami summa {fmt(r[0][1])} so'm; o'rtacha bitim {fmt(r[0][2])} so'm."}[lang]
    n.append("вопрос без явной метрики: показаны количество и сумма")
    return sql, r, txt, n


def tpl_rate(t, e, lang):
    if e.date_from and has(t, r"начал|boshi|первый день"):
        sql = f"SELECT rate_date, uzs_per_usd FROM usd_rates WHERE rate_date >= DATE '{max(e.date_from, DATA_START)}' ORDER BY rate_date LIMIT 1"
        r = q(sql); return sql, r, {"ru": f"Курс доллара на {r[0][0]:%d.%m.%Y} (первый день данных в периоде): {fmt(r[0][1],2)} сум за 1 USD.", "uz": f"{r[0][0]:%d.%m.%Y} holatiga dollar kursi: 1 USD = {fmt(r[0][1],2)} so'm."}[lang], []
    if e.date_from and has(t, r"конце|конец|oxiri|последний день"):
        sql = f"SELECT rate_date, uzs_per_usd FROM usd_rates WHERE rate_date <= DATE '{min(e.date_to, DATA_END)}' ORDER BY rate_date DESC LIMIT 1"
        r = q(sql); return sql, r, {"ru": f"Курс доллара на {r[0][0]:%d.%m.%Y}: {fmt(r[0][1],2)} сум за 1 USD.", "uz": f"{r[0][0]:%d.%m.%Y} holatiga dollar kursi: 1 USD = {fmt(r[0][1],2)} so'm."}[lang], []
    ext = "DESC" if has(t, r"максимальн|наибольш|самый высок|самым высок|eng yuqori|eng baland|eng katta|\bmax|пик") else "ASC" if has(t, r"минимальн|наименьш|самый низк|eng past|eng kichik|\bmin") else None
    if ext:
        w = [f"rate_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'"] if e.date_from else [f"rate_date <= DATE '{DATA_END}'"]
        sql = f"SELECT rate_date, uzs_per_usd FROM usd_rates WHERE {' AND '.join(w)} ORDER BY uzs_per_usd {ext} LIMIT 1"
        r = q(sql)
        word = {"DESC": ("Самый высокий", "Eng yuqori"), "ASC": ("Самый низкий", "Eng past")}[ext]
        txt = {"ru": f"{word[0]} курс: {fmt(r[0][1],2)} сум за 1 USD ({r[0][0]:%d.%m.%Y}).", "uz": f"{word[1]} kurs: 1 USD = {fmt(r[0][1],2)} so'm ({r[0][0]:%d.%m.%Y})."}[lang]
        return sql, r, txt, ["курс ЦБ из usd_rates" if lang == "ru" else "usd_rates jadvalidagi kurs"]
    if e.exact_date:
        sql = f"SELECT rate_date, uzs_per_usd FROM usd_rates WHERE rate_date = DATE '{e.exact_date}'"
    elif e.date_from:
        sql = f"SELECT min(rate_date), round(avg(uzs_per_usd),2), min(uzs_per_usd), max(uzs_per_usd) FROM usd_rates WHERE rate_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'"
    else:
        sql = "SELECT rate_date, uzs_per_usd FROM usd_rates ORDER BY rate_date DESC LIMIT 1"
    r = q(sql)
    if not r or r[0][0] is None: return sql, [], None, []
    if len(r[0]) == 2:
        txt = {"ru": f"Курс доллара на {r[0][0]:%d.%m.%Y}: {fmt(r[0][1],2)} сум за 1 USD.", "uz": f"{r[0][0]:%d.%m.%Y} holatiga dollar kursi: 1 USD = {fmt(r[0][1],2)} so'm."}[lang]
    else:
        txt = {"ru": f"Средний курс за {e.period_label}: {fmt(r[0][1],2)} сум (мин {fmt(r[0][2],2)}, макс {fmt(r[0][3],2)}).", "uz": f"{e.period_label} davridagi o'rtacha kurs: {fmt(r[0][1],2)} so'm (min {fmt(r[0][2],2)}, maks {fmt(r[0][3],2)})."}[lang]
    return sql, r, txt, []


def tpl_company(t, e, lang):
    x = e.inns[0]
    sql = f"SELECT \"Наименование\", \"ИНН\", \"Регион\", \"Вид деятельности\", \"Роль\", \"Плательщик НДС\" FROM participants WHERE \"ИНН\"='{x}'"
    r = q(sql)
    if not r: return sql, [], None, []
    a = r[0]
    txt = {"ru": f"ИНН {a[1]}: {a[0]}. Регион: {a[2]}; вид деятельности: {a[3]}; роль: {a[4]}; плательщик НДС: {a[5]}.",
           "uz": f"STIR {a[1]}: {a[0]}. Hudud: {a[2]}; faoliyat turi: {a[3]}; roli: {a[4]}; QQS to'lovchisi: {a[5]}."}[lang]
    return sql, r, txt, []


def tpl_broker(t, e, lang):
    x = e.brokers[0]
    sql = f"SELECT \"Код брокера\", \"Наименование\", \"ИНН\", \"Регион\" FROM brokers WHERE \"Код брокера\"='{x}'"
    r = q(sql)
    if not r: return sql, [], None, []
    a = r[0]
    txt = {"ru": f"Брокер {a[0]}: {a[1]} (ИНН {a[2]}, регион {a[3]}).", "uz": f"Broker {a[0]}: {a[1]} (STIR {a[2]}, hudud {a[3]})."}[lang]
    return sql, r, txt, []


def tpl_self(t, e, lang):
    w, n = build_filters(t, e)
    if has(t, r"продавц\w*.{0,40}покупател|покупател\w*.{0,40}продавц|ham sotuvchi.{0,20}ham xaridor|sotuvchi va xaridor sifatida") and has(t, r"компан|kompaniya|участник|firma"):
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
    w.append("seller_inn = buyer_inn")
    sql = f"SELECT count(*) FROM deals_enriched {where(w)}"
    r = q(sql); v = r[0][0]
    txt = ({"ru": "Нет: сделок, где продавец и покупатель — одна компания (одинаковый ИНН), в данных нет (0).", "uz": "Yo'q: sotuvchi va xaridor bir xil kompaniya (bir xil STIR) bo'lgan bitim yo'q (0)."}[lang]
           if v == 0 else {"ru": f"Да, таких сделок: {fmt(v)}.", "uz": f"Ha, bunday bitimlar: {fmt(v)} ta."}[lang])
    return sql, r, txt, n


def tpl_underdelivery(t, e, lang):
    w, n = build_filters(t, e)
    if not any("status" in x for x in w): w.append("status='Исполнен(Частично)'")
    delivered = not has(t, r"недопостав|не поставлен|yetkazilmagan|осталось|qolgan")
    x = "delivered_lots" if delivered else "(qty_lots - delivered_lots)"
    sql = f"SELECT unit, round(sum({x})), round(sum({x} * lot_size), 2), count(*) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 4 DESC"
    r = q(sql)
    if not r: return sql, r, None, []
    parts = [f"{fmt(a)} лотов = {fmt(b)} {u}" for u, a, b, _ in r]
    cnt = sum(z[3] for z in r)
    txt = ({"ru": "Фактически поставлено: " + "; ".join(parts) + f" ({fmt(cnt)} сделок).", "uz": "Amalda yetkazilgan: " + "; ".join(parts).replace("лотов", "lot") + f" ({fmt(cnt)} ta bitim)."}
           if delivered else {"ru": "Недопоставлено: " + "; ".join(parts) + f" ({fmt(cnt)} частично исполненных сделок).", "uz": "Yetkazilmagan: " + "; ".join(parts).replace("лотов", "lot") + f" ({fmt(cnt)} ta qisman bajarilgan bitim)."})[lang]
    return sql, r, txt, n + ["поставлено = поле «Поставлено» (лоты) × размер лота" if delivered else "недопоставка = (куплено лотов − поставлено лотов) × размер лота"]


def _base_where(t, e):
    """period-only filters (for denominators of shares)"""
    return [f"deal_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'"] if e.date_from else []


def tpl_distinct(t, e, lang):
    w, n = build_filters(t, e)
    if has(t, r"товар|продукт|mahsulot|tovar") and not has(t, r"компан|kompaniya|покупат|продав|xaridor|sotuvchi"):
        sql = f"SELECT count(DISTINCT product_base) FROM deals_enriched {where(w)}"; r = q(sql)
        return sql, r, {"ru": f"Различных наименований товара (базовое наименование в карточке контракта): {fmt(r[0][0])}.", "uz": f"Turli mahsulot nomlari: {fmt(r[0][0])} ta."}[lang], n + ["считаются различные базовые наименования; один товар может быть записан по-разному"]
    if has(t, r"регион|region|hudud|viloyat|област"):
        sql = f"SELECT count(DISTINCT region) FROM deals_enriched {where(w)}"; r = q(sql)
        return sql, r, {"ru": f"Регионов со сделками: {fmt(r[0][0])}.", "uz": f"Bitimlar bo'lgan hududlar: {fmt(r[0][0])} ta."}[lang], n + ["регион склада продавца"]
    if has(t, r"брокер|broker"):
        col, what = "seller_broker", ("брокеров", "broker")
        expr = "(SELECT count(*) FROM (SELECT seller_broker FROM deals_enriched {w} UNION SELECT buyer_broker FROM deals_enriched {w}))"
        sql = "SELECT " + expr.format(w=where(w))
    else:
        sd = "buyer" if has(t, r"покупат|xaridor") else "seller" if has(t, r"продав|sotuvchi") else None
        if sd:
            sql = f"SELECT count(DISTINCT {sd}_inn) FROM deals_enriched {where(w)}"
            what = ("покупателей", "xaridor") if sd == "buyer" else ("продавцов", "sotuvchi")
        else:
            sql = f"SELECT count(*) FROM (SELECT seller_inn FROM deals_enriched {where(w)} UNION SELECT buyer_inn FROM deals_enriched {where(w)})"
            what = ("компаний (продавцов и покупателей)", "kompaniya (sotuvchi va xaridor)")
    r = q(sql)
    txt = {"ru": f"Уникальных {what[0]}: {fmt(r[0][0])}.", "uz": f"Noyob {what[1]}lar soni: {fmt(r[0][0])} ta."}[lang]
    n.append("считаются различные ИНН / коды, участвовавшие в сделках")
    return sql, r, txt, n


def tpl_share(t, e, lang):
    w, n = build_filters(t, e)
    COND = ("status", "contract_type", "contract_form", "auction_type", "currency")
    b = [x for x in w if not x.startswith(COND)]
    extra = [x for x in w if x.startswith(COND)]
    if not extra:
        b = _base_where(t, e); extra = [x for x in w if x not in b]
    if not extra: return None, None, None, []
    by_sum = has(t, SUM_W + r"|объ[её]м")
    m = "sum(amount_uzs)" if by_sum else "count(*)"
    sql = f"SELECT round(100.0 * {m} FILTER (WHERE {' AND '.join(extra)}) / {m}, 2), {m} FILTER (WHERE {' AND '.join(extra)}), {m} FROM deals_enriched {where(b)}"
    r = q(sql)
    txt = {"ru": f"Доля: {r[0][0]}% ({fmt(r[0][1])} из {fmt(r[0][2])}" + (" сум" if by_sum else " сделок") + ").",
           "uz": f"Ulushi: {r[0][0]}% ({fmt(r[0][2])} dan {fmt(r[0][1])}" + (" so'm" if by_sum else " ta bitim") + ")."}[lang]
    n.append("доля по сумме в сумах" if by_sum else "доля по количеству сделок")
    return sql, r, txt, n


def tpl_growth(t, e, lang):
    import re as _re
    years = sorted({int(y) for y in _re.findall(r"\b(20\d{2})\b", t)})
    if len(years) != 2: return None, None, None, []
    y0, y1 = years
    e2 = type(e)(**{**e.__dict__}); e2.date_from = e2.date_to = None
    w, n = build_filters(t, e2)
    by_sum = has(t, SUM_W)
    m = "sum(amount_uzs)" if by_sum else "count(*)"
    sql = (f"SELECT {m} FILTER (WHERE year(deal_date)={y0}), {m} FILTER (WHERE year(deal_date)={y1}), "
           f"round(100.0*{m} FILTER (WHERE year(deal_date)={y1}) / {m} FILTER (WHERE year(deal_date)={y0}) - 100, 2) FROM deals_enriched {where(w)}")
    r = q(sql); a, b2, pct = r[0]
    if not a: return sql, [], None, n
    unit = " сум" if by_sum else " сделок"
    txt = {"ru": f"{y0}: {fmt(a)}{unit}; {y1}: {fmt(b2)}{unit}. Изменение: {pct:+.2f}%.",
           "uz": f"{y0}: {fmt(a)}; {y1}: {fmt(b2)}. O'zgarish: {pct:+.2f}%."}[lang]
    if 2026 in years: n.append("2026 год неполный — данные до 30.10.2026")
    n.append("по сумме в сумах" if by_sum else "по количеству сделок")
    return sql, r, txt, n


def tpl_delay(t, e, lang):
    w, n = build_filters(t, e)
    if has(t, r"постав|yetkaz") and (not has(t, r"оплат|to'lov") or has(t, r"после оплаты|от оплаты|с оплаты|to'lovdan")):
        expr, nn = "delivery_date - paid_date", ("от оплаты до поставки", "to'lovdan yetkazishgacha")
        w.append("delivery_date IS NOT NULL AND paid_date IS NOT NULL")
    elif has(t, r"постав|yetkaz"):
        expr, nn = "delivery_date - deal_date", ("от сделки до поставки", "bitimdan yetkazishgacha")
        w.append("delivery_date IS NOT NULL")
    else:
        expr, nn = "paid_date - deal_date", ("от сделки до оплаты", "bitimdan to'lovgacha")
        w.append("paid_date IS NOT NULL")
    sql = f"SELECT round(avg({expr}),2), median({expr}), max({expr}), count(*) FROM deals_enriched {where(w)}"
    r = q(sql)
    txt = {"ru": f"Средний срок {nn[0]}: {r[0][0]} дн. (медиана {r[0][1]}, максимум {r[0][2]}; {fmt(r[0][3])} сделок).",
           "uz": f"O'rtacha muddat {nn[1]}: {r[0][0]} kun (mediana {r[0][1]}, maksimum {r[0][2]}; {fmt(r[0][3])} ta bitim)."}[lang]
    n.append("только сделки, где дата заполнена")
    return sql, r, txt, n


def tpl_by_month(t, e, lang):
    w, n = build_filters(t, e)
    by_sum = has(t, SUM_W)
    m = "round(sum(amount_uzs))" if by_sum else "count(*)"
    grp = "strftime(deal_date, '%Y')" if has(t, r"каком году|qaysi yil") else "strftime(deal_date, '%Y-%m')"
    order = "ASC" if has(t, r"меньше всего|наименьш|eng kam") else "DESC"
    sql = f"SELECT {grp} AS p, {m} AS v, CAST(right({grp}, 2) AS INT) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 2 {order} LIMIT 3"
    r = q(sql)
    lines = [f"{i+1}. {x[0]} — {fmt(x[1])}" for i, x in enumerate(r)]
    head = {"ru": "Период-лидер" + (" (по сумме, сум):" if by_sum else " (по числу сделок):"), "uz": "Yetakchi davr:"}[lang]
    return sql, r, head + "\n" + "\n".join(lines), n


def tpl_avg_deal(t, e, lang):
    w, n = build_filters(t, e, settled=True)
    sql = f"SELECT round(avg(amount_uzs)), round(median(amount_uzs)), count(*) FROM deals_enriched {where(w)}"
    r = q(sql)
    txt = {"ru": f"Средняя сумма сделки: {fmt(r[0][0])} сум (медиана {fmt(r[0][1])} сум; {fmt(r[0][2])} сделок).",
           "uz": f"O'rtacha bitim summasi: {fmt(r[0][0])} so'm (mediana {fmt(r[0][1])} so'm; {fmt(r[0][2])} ta bitim)."}[lang]
    n.append("в сумах, экспорт пересчитан по курсу")
    return sql, r, txt, n


STAT_FN = [("median", r"медиан|median|o'rta qiymat"), ("min", r"минимальн|наименьш|самая низк|самую низк|дешев|eng arzon|eng past|\bmin"),
           ("max", r"максимальн|наибольш|самая высок|самую высок|самой высок|дорож|eng qimmat|eng baland|eng yuqori|\bmax"), ("stddev", r"отклонени|stddev|og'ish")]


def tpl_stat(t, e, lang):
    fn = next((f for f, pat in STAT_FN if has(t, pat)), None)
    if not fn: return None, None, None, []
    w, n = build_filters(t, e, settled=True)
    sqlf = {"median": "median", "min": "min", "max": "max", "stddev": "stddev_samp"}[fn]
    name = {"median": ("Медиана", "Mediana"), "min": ("Минимум", "Minimum"), "max": ("Максимум", "Maksimum"), "stddev": ("Стандартное отклонение", "Standart og'ish")}[fn]
    if has(t, r"цен|narx|стоимост"):
        cur = "Доллар США" if usd_wanted(t) else "Узбекский сум"; w.append(f"currency='{cur}'")
        u = _unit_filter(t)
        if u: w.append(f"unit='{u}'")
        sql = f"SELECT unit, round({sqlf}(price_per_unit),2), count(*) FROM deals_enriched {where(w)} GROUP BY 1 ORDER BY 3 DESC"
        r = q(sql)
        if not r: return sql, r, None, n
        c = "USD" if cur == "Доллар США" else {"ru": "сум", "uz": "so'm"}[lang]
        parts = [f"{fmt(v,2)} {c} за 1 {un}" for un, v, _ in r]
        n.append("цена за единицу = сумма ÷ (лоты × размер лота), в валюте контракта")
    else:
        sql = f"SELECT 'сделка', round({sqlf}(amount_uzs)), count(*) FROM deals_enriched {where(w)}"
        r = q(sql)
        if not r or r[0][1] is None: return sql, [], None, n
        parts = [f"{fmt(r[0][1])} " + {"ru": "сум", "uz": "so'm"}[lang]]
        n.append("по сумме сделки в сумах")
    txt = {"ru": f"{name[0]}: " + "; ".join(parts) + f" ({fmt(sum(x[2] for x in r))} сделок).",
           "uz": f"{name[1]}: " + "; ".join(p.replace(' за 1 ', ' / 1 ') for p in parts) + f" ({fmt(sum(x[2] for x in r))} ta bitim)."}[lang]
    return sql, r, txt, n


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
    act = re.search(r"§act=([^§]+)§", t)
    if act: w.append(f"\"Вид деятельности\"={sqlq(act.group(1))}"); n.append("вид деятельности: " + act.group(1))
    else:
        for pat, val in ACTIVITY:
            if has(t, pat): w.append(f"\"Вид деятельности\"='{val}'"); n.append("вид деятельности: " + val); break
    if has(t, r"процент|дол[яю]\b|foiz|ulush") and len(w) >= 1:
        base = [x for x in w if "НДС" not in x and "Вид деятельности" not in x]; cond = [x for x in w if x not in base] or w
        sql = f"SELECT round(100.0 * count(*) FILTER (WHERE {' AND '.join(cond)}) / count(*), 2), count(*) FILTER (WHERE {' AND '.join(cond)}), count(*) FROM participants {where(base)}"
        r = q(sql)
        txt = {"ru": f"Доля: {r[0][0]}% ({fmt(r[0][1])} из {fmt(r[0][2])} компаний справочника).", "uz": f"Ulushi: {r[0][0]}% ({fmt(r[0][2])} tadan {fmt(r[0][1])} ta kompaniya)."}[lang]
        return sql, r, txt, n + ["справочник участников (participants.csv)"]
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
    price = has(t, r"цен|narx|стоимост|дешев|дорог|arzon|qimmat|за тонн|по тонн|за кг|за килограмм")
    w, n = build_filters(t, e, settled=True)
    if price:
        w.append("currency='Узбекский сум'")
        u = _unit_filter(t) or ("тонна" if has(t, r"тонн|tonna") else None)
        if u: w.append(f"unit='{u}'"); n.append("только сделки в единице: " + u)
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


# ------------------------------------------------------------------ router
COUNT_W = r"сколько|скока|скоко|количеств|число|nechta|necha|qancha|soni|есть ли|bormi|неча|нечта|қанча"
SUM_W = r"сумм|сумов|summa|so'mlik|потратил|заплатил|xarajat|sarfla|стоимост|оборот|savdo|\bpul|денег|деньг|выручк|на сколько сум|сколько долларов|necha dollar|qancha dollar"


NOT_TRADED = r"золот|oltin|серебр|kumush|платин|\bнефт(ь|и|ью)\b|brent|\bwti\b|\bуголь|\bугля|ko'mir|кўмир|komir|кофе|qahva|какао|\bчая?\b|\bchoy\b|алмаз|olmos|биткоин|bitcoin|крипт|электроэнерг|elektr energ|\bмяс[оа]\b|go'sht|молок|\bsut\b|картоф|kartoshka|овощ|фрукт|\bmeva|sabzavot|\bавтомобил(ь|и|ей|я)\b|avtomobil|недвижим|квартир|\bхлопков\w* волокн|paxta tolasi|пахта толаси|\bсо[яиюе]\b|соев|\bsoya|кукуруз|makkajo'xori|ячмен|\barpa\b|\bовес|\bовса|семен[аи] |urug'|шерст|\bjun\b|\bкож[аи]\b|шелк|\bipak|табак|tamaki|виноград|\buzum|гречк|чеснок|\bмед\b|\basal\b|рыб[аы]|baliq|яйц|tuxum|\bсыр\b|сливочн|железн\w* руд|temir ruda|\bруд[аы]|\bуран|\bлес\b(?!о)|древесин"
FOREIGN = r"москв|moskva|росси[июя]|rossiya|казахстан|qozog'iston|кыргыз|qirg'iz|таджик|tojikiston|китай|xitoy|турци|turkiya|лондон|нью-йорк|афганистан|туркмен|turkman|европ|yevropa|петербург|peterburg|алмат|бишкек|душанбе|ашхабад|стамбул|istanbul|дубай|dubay|пекин|шанха|минск|киев|баку|тегеран"
UNSUPPORTED = r"почему|причин|nima uchun|корреляц|зависимост|прогноз|аномал|подозр|shubhali"


EXT_W = r"когда|qachon|какого числа|в какой день|qaysi kun|в какую дату|какой день|какую дату"
MONEY_W = r"сумм|сумов|summa|\bpul|so'm|\bсум\b|сумах|денег|деньг|долл|dollar|выручк|оборот"
UNIT_W = r"тонн|tonna|\bкг\b|килограмм|литр|litr|объ[её]м|hajm|\bлот"


def route(t, e):
    if has(t, r"\bкурс|\bkurs|сколько стои\w* доллар|доллар стои|dollar narxi|dollar necha|dollar qancha|1 usd|за 1 доллар"): return "rate"
    if has(t, COUNT_W + r"|процент|дол[яю]\b|foiz|ulush") and has(t, r"брокер|broker|компан|kompaniya|участник|ishtirokchi|firma|xaridor|sotuvchi|покупател|продавц") and not has(t, MONEY_W + "|" + SUM_W + r"|сделк|сделок|bitim|savdo") and (
            has(t, REG_W + r"|справочник|ro'yxat|реестр") or has(t, r"ндс|\bqqs\b|\bnds\b") or (has(t, r"брокер|broker") and e.regions and not e.date_from) or any(has(t, p) for p, _ in ACTIVITY)): return "directory"
    if has(t, r"какие товары|какие продукты|список товаров|qaysi (tovar|mahsulot)lar|qanday (tovar|mahsulot)lar|qaysi tovar"): return "list"
    if (len(e.regions) >= 2 or len(e.products) >= 2) and has(t, r"сравн|solishtir|taqqosla|где больше|qaysi birida|\bили\b|\byoki\b|против|\bvs\b|\bчем\b"): return "compare"
    if has(t, r"продавц\w*.{0,40}покупател|покупател\w*.{0,40}продавц|ham sotuvchi.{0,20}ham xaridor") and has(t, r"есть ли|bormi|бывает|существу") and has(t, r"компан|kompaniya|участник"): return "self"
    if has(t, r"bir xil|одинаков|один и тот же|одним и тем же|совпада") and has(t, r"брокер|broker"): return "self"
    if has(t, r"уникальн|различн|разных|turli|har xil|noyob|сколько (видов|наименований|всего )?(товар|продукт|регион)|nechta (mahsulot|tovar|hudud|viloyat)|qancha (kompaniya|xaridor|sotuvchi)|nechta (turli )?(kompaniya|xaridor|sotuvchi)|сколько (всего )?(покупател|продав|компан|участник|брокер)|nechta (kompaniya|xaridor|sotuvchi|broker)|(kompaniya|xaridor|sotuvchi|broker)lar soni|noyob"): return "distinct"
    if has(t, r"дол[яюи]\b|процент|ulush|foiz|\d+\s*%") and not has(t, r"\bвырос|\bрост|\bснизил|\bупал|o'sdi|kamaydi|o'zgar|nisbatan|изменил|сравнени"): return "share"
    if has(t, r"\bвырос|\bрост|\bснизил|\bупал|\bизменил|сравн|o'sdi|kamaydi|o'zgar|динамик|solishtir|taqqosla"): return "growth"
    if has(t, r"\bсрок|\bдней\b|\bдня\b|\bднях\b|сколько дн|kun ichida|necha kun|\bkun\b|задержк|просроч|kechik"): return "delay"
    if has(t, r"в каком месяце|в какой месяц|каком году|qaysi oy|qaysi yil|по месяцам|oylar bo'yicha|\boy\b.{0,12}qaysi|\byil\b.{0,12}qaysi|какой месяц|месяц.{0,20}(больше|наибол|меньше)"): return "by_month"
    if has(t, UNSUPPORTED): return None
    if has(t, EXT_W) and (any(has(t, p) for f, p in STAT_FN if f in ("min", "max")) or has(t, r"самы\w* (дешев|дорог)|eng (arzon|qimmat)")): return "extreme"
    if has(t, r"медиан|median|отклонени|stddev") or (has(t, r"цен|narx") and any(has(t, p) for f, p in STAT_FN)): return "stat"
    if e.brokers and has(t, r"назв|наимен|называ|nomi|кто так|что за|qanaqa|qanday broker|haqida|какой это|кто это|ma'lumot|информац|расскажи"): return "broker"
    if e.inns and has(t, r"назов|назв|наимен|кто так|что за|nomi|qaysi kompaniya|какая компания|информац|ma'lumot") and not has(t, SUM_W + r"|" + COUNT_W): return "company"
    if e.inns and not has(t, SUM_W + "|" + COUNT_W + r"|объ|тонн|цен|narx|hajm"): return "company"
    if has(t, r"одна (и та же )?компан|сам[аи]? себе|одн[аио]м и тем же|bir xil kompaniya|o'ziga o'zi"): return "self"
    if has(t, r"недопостав|не поставлен|yetkazilmagan|осталось недо"): return "underdelivery"
    if has(t, r"фактически постав|поставлено|yetkazib berilgan|yetkazilgan") and has(t, r"\bлот|lot|тонн|литр|tonna|объ|hajm|сколько|qancha"): return "underdelivery"
    if has(t, r"какая сделка|какую сделку|qaysi bitim|сделка.{0,25}(самой|самая) (крупн|больш)"): return "largest"
    if has(t, r"сделк|bitim") and has(t, r"самой крупной|самая крупная|самую крупную|крупнейш|самая большая|самую большую|самой большой|наибольш\w* (по )?сумм|eng katta|eng yirik|максимальн\w* (по )?сумм") and not has(t, r"покупател|продавц|брокер|регион|xaridor|sotuvchi|broker"): return "largest"
    if has(t, r"(самая большая|самую большую|крупнейшая|крупнейшую|максимальн\w*) сделк|eng katta bitim|eng yirik bitim"): return "largest"
    if has(t, r"средн\w* (чек|сумм\w* сделк|размер)|o'rtacha (summa|bitim|chek)"): return "avg_deal"
    if has(t, r"\bгде\b.{0,30}(больше|меньше)|qayerda.{0,30}(ko'p|kam)") and dim_of(t): return "top"
    if has(t, r"\bтоп|\btop\b|больше всего|больше всех|кто больше|меньше всего|чаще всего|ko'proq|самы[йеая]|крупнейш|лидер|eng ko'p|eng faol|eng yirik|наибольш|какой брокер|какой регион|qaysi") and dim_of(t): return "top"
    if has(t, r"цен|narx|стоимость за|почём|почем"): return "avg_price"
    if has(t, UNIT_W) and not has(t, MONEY_W): return "volume"
    if has(t, SUM_W) and not has(t, r"сколько сделок|nechta bitim"): return "amount"
    if e.inns and not e.products and has(t, r"сколько\s+(он[аи]?\s+)?(продал|купил)|qancha (sotgan|sotib olgan|sotdi)"): return "amount"
    if e.products and has(t, r"тонн|tonna|\bкг\b|килограмм|объ[её]м|hajm|литр|продан|продал|купил|sotil|sotgan|necha") and not has(t, r"сделок|сделк|bitim"): return "volume"
    if has(t, r"тонн|tonna|объ[её]м|hajm"): return "volume"
    if has(t, r"оплат|статус|holat|to'lov|исполнен") and not has(t, COUNT_W + "|" + SUM_W): return "status"
    if has(t, r"сделк|сделок|сделак|сдела|транзакц|bitim|savdolar|торг|операц|количеств|\bчисло|soni|nechta"): return "count"
    if has(t, COUNT_W) and has(t, r"аннулир|bekor|исполнен|bajarilgan|оплачен|to'langan|подписан|форвард|фьючерс|спот|экспорт|eksport"): return "count"
    if not has(t, r"сколько|какой|какая|какие|кто|qancha|qaysi|kim|\?") and (e.products or e.regions or e.brokers or e.inns): return "summary"
    return None


TEMPLATES = dict(count=tpl_count, amount=tpl_amount, volume=tpl_volume, avg_price=tpl_avg_price, top=tpl_top, largest=tpl_largest,
                 status=tpl_status, summary=tpl_summary, rate=tpl_rate, company=tpl_company, broker=tpl_broker, self=tpl_self,
                 underdelivery=tpl_underdelivery, distinct=tpl_distinct, share=tpl_share,
                 growth=tpl_growth, delay=tpl_delay, by_month=tpl_by_month, avg_deal=tpl_avg_deal, stat=tpl_stat,
                 directory=tpl_directory, list=tpl_list, extreme=tpl_extreme, compare=tpl_compare)


def find_company(t):
    hits = [(c, inn, name) for c, inn, name in _companies if f" {c} " in t]
    hits.sort(key=lambda x: -len(x[0]))
    return hits[0] if hits else None


MODE = "hybrid"   # legacy | rules | llm | hybrid | gemini
SPECIAL = {"rate", "company", "broker", "directory", "list", "self"}


def _semantic(question, t, e, lang, use_llm):
    from . import semantic as S
    spec, conf = S.rule_spec(t, e)
    if MODE == "llm" and use_llm:
        ls = S.llm_spec(question)
        if ls == "none": a = nodata(lang, "empty", "llm-none"); a.route = "semantic:llm-none"; return a
        if ls is not None:
            ls.same_company, ls.same_broker = spec.same_company, spec.same_broker
            if spec.unit and not ls.unit: ls.unit = spec.unit
            spec, conf = ls, True
    elif MODE == "hybrid" and not conf and use_llm:
        ls = S.llm_spec(question)
        if ls == "none": a = nodata(lang, "empty", "llm-none"); a.route = "semantic:llm-none"; return a
        if ls is not None: spec, conf = ls, True
    TR.step("rule_spec", spec=spec.__dict__, confident=conf)
    if not conf: return None
    try:
        out = S.run_spec(spec, t, e, lang)
    except Exception as ex:
        print("semantic error:", ex, "|", question[:60]); return None
    if out is None: return None
    sql, rows, txt, notes = out
    route_name = f"semantic:{spec.source}:{spec.metric}" + (f"/{spec.group_by}" if spec.group_by else "") + ("/share" if spec.share else "") + (f"/cmp-{spec.compare}" if spec.compare else "")
    if txt is None or not rows:
        a = nodata(lang, "empty", "empty"); a.sql = sql; a.route = route_name; return a
    basis = {"ru": "Основание", "uz": "Asos"}[lang]
    if e.inns:
        nm = q(f"SELECT \"Наименование\" FROM participants WHERE \"ИНН\"='{e.inns[0]}'")
        if nm: txt = f"{nm[0][0]} (ИНН {e.inns[0]}). " + txt
    if notes: txt += f"\n\n{basis}: " + "; ".join(dict.fromkeys(notes)) + "."
    return Answer(lang, localize(txt, lang), sql, rows, route=route_name, confidence="high" if spec.source == "rules" else "medium")


def answer(question: str, use_llm=True) -> Answer:
    t0 = time.perf_counter()
    tr = TR.start(question)
    try:
        a = _answer(question, use_llm)
    except Exception as ex:
        TR.step("error", error=repr(ex)[:500])
        raise
    a.ms = (time.perf_counter() - t0) * 1000
    TR.step("answer", route=a.route, confidence=a.confidence, no_data=a.no_data, text=a.text, total_ms=round(a.ms, 1))
    tr.data.update(mode=MODE, route=a.route, no_data=a.no_data, total_ms=round(a.ms, 1))
    a.trace = tr.data
    return a


def _answer(question, use_llm):
    con()
    lang = lang_of(question)
    t = _norm(question)
    e = extract(question)
    TR.step("input", lang=lang, normalized=t, mode=MODE)
    TR.step("rule_entities", products=e.products, regions=e.regions, inns=e.inns, brokers=e.brokers,
            period=e.period_label, years=e.years, unknown_product=e.unknown_product, out_of_range=e.out_of_range)
    if MODE == "gemini" and use_llm:
        a = _gemini_answer(question, t, e, lang)
        if a is not None: return a
    # company named in words -> its INN
    if not e.inns:
        c = find_company(_core(question) and " " + _core(question) + " ")
        if c: e.inns = [c[1]]
    # ---- guards: things the data cannot contain
    if has(t, r"прогноз|предскаж|будет стоить|ожида\w* цен|bashorat|prognoz|kelgusi yil|keyingi yil"):
        return nodata(lang, "forecast", "forecast")
    if has(t, r"сколько склад|склад\w* (у|принадлеж)|nechta ombor|omborlari soni|tashkil topgan|tashkil etilgan|asos solingan|основан[аы]?\b|год основания|когда (была )?создан|\bакци[ияйю]\w*|рейтинг|reyting|кредит|kredit|daromad|прибыл|\bадрес компан|адрес офис|юридическ\w* адрес|ofis manzil|yuridik manzil|foyda|\bналог|soliq|зарплат|maosh|сотрудник|xodim|завод|zavod|фабрик|fabrika|погод|ob-havo|урожа|hosil|населени|aholi|\bввп\b|инфляц|inflyats|лицензи|litsenziya|сертифик|дивиденд|капитализац|aksiya|директор|руководител|телефон|e-?mail|почт[аы]|сайт|учредител|владел|direktor|rahbar|telefon|egasi"):
        return nodata(lang, "field", "field")
    if has(t, NOT_TRADED) and not e.products:
        return nodata(lang, "product", "not-traded", x=re.search(NOT_TRADED, t).group(0))
    if has(t, r"\bевро|\beuro|\byevro|\bрубл|\brubl|\bюан|\byuan|\bтенге|\btenge|\bфунт|\bиен|\bwon\b"):
        return nodata(lang, "currency", "currency")
    if has(t, FOREIGN):
        return nodata(lang, "foreign", "foreign")
    if has(t, r"почему|по какой причине|причин[аы]|nima uchun|\bnega\b|влиян|повлия|ta'sir|таъсир"):
        return nodata(lang, "why", "why")
    if e.out_of_range:
        return nodata(lang, "range", "range")
    for x in e.inns:
        if not q(f"SELECT 1 FROM participants WHERE \"ИНН\"='{x}'"): return nodata(lang, "inn", "inn", x=x)
    for b in e.brokers:
        if not q(f"SELECT 1 FROM brokers WHERE \"Код брокера\"='{b}'"): return nodata(lang, "broker", "broker", x=b)
    if e.unknown_product:
        stem = e.unknown_product[:4]
        if not q(f"SELECT 1 FROM contracts WHERE lower(\"Наименование продукта\") LIKE '%{stem}%' LIMIT 1"):
            return nodata(lang, "product", "product", x=e.unknown_product)
    # ---- closed-world vocabulary: refuse questions about things the data does not contain
    unk = unknown_words(t)
    if unk:
        a = nodata(lang, "unknown", "unknown-words", x=", ".join(f"«{w}»" for w in unk[:4]))
        return a
    # ---- lookups and reference-table questions keep their dedicated handlers
    r = route(t, e)
    if MODE != "legacy" and r not in SPECIAL:
        a = _semantic(question, t, e, lang, use_llm)
        if a is not None: return a
        r = None if MODE != "legacy" else r
    # ---- templates (legacy path)
    if r:
        sql, rows, txt, notes = TEMPLATES[r](t, e, lang)
        if sql:
            if txt is None or not rows or rows[0][0] is None:
                a = nodata(lang, "empty", "empty"); a.sql = sql; a.route = f"template:{r}"; return a
            basis = {"ru": "Основание", "uz": "Asos"}[lang]
            if e.inns and r not in ("company",):
                nm = q(f"SELECT \"Наименование\" FROM participants WHERE \"ИНН\"='{e.inns[0]}'")
                if nm: txt = f"{nm[0][0]} (ИНН {e.inns[0]}). " + txt
            if notes: txt += f"\n\n{basis}: " + "; ".join(notes) + "."
            return Answer(lang, localize(txt, lang), sql, rows, route=f"template:{r}")
    # ---- fallback 1: LLM picks a template + filters (code runs it), 2: free SQL
    if use_llm:
        a = llm_plan(question, lang, e, t)
        if a is not None: return a
        return llm_fallback(question, lang, e)
    return Answer(lang, T["unclear"][lang], route="none", confidence="none", no_data=True)


# ------------------------------------------------------------------ LLM fallback
SCHEMA = """DuckDB view deals_enriched (one row per trade; 2022-01-03..2026-10-30):
 deal_id, deal_date DATE, auction_type, qty_lots, amount (contract currency), start_price, contract_id,
 product_name (full name with attributes), product_base (lowercase base product name, e.g. 'хлопок-сырец (спот)', 'мука пшеничная', 'портландцемент'),
 lot_size, unit (тонна/килограмм/литр/штука/куб.метр/кв.метр/метр),
 region (seller warehouse region, Latin Uzbek e.g. 'Namangan viloyati','Toshkent shahri'), contract_type (Внутренний/Экспортный),
 contract_form (Спот/Форвард/Фьючерс),
 status (Исполнен, Исполнен(Частично), Оплачен, Ждёт оплату, Заключен, Не подписан, Новый, 'Аннулирован(Не оплачен)' = deal did not happen),
 seller_inn, buyer_inn, seller_broker, buyer_broker (codes BK001..BK120), paid_date, delivery_date, delivered_lots,
 volume_units (=qty_lots*lot_size), price_per_unit, currency ('Узбекский сум' or 'Доллар США'), amount_uzs (amount converted to UZS).
Other tables: participants("ИНН","Наименование","Регион","Вид деятельности","Роль"), brokers("Код брокера","Наименование","ИНН","Регион"), usd_rates(rate_date, uzs_per_usd).
Rules: money totals use amount_uzs; sum volume_units only within one unit; output ONE read-only SELECT; LIMIT 20 for lists.
If the tables cannot answer the question, output exactly: SELECT 'NO_DATA'"""


def _llm(msgs):
    body = json.dumps({"model": LLM_MODEL, "messages": msgs, "stream": False, "options": {"temperature": 0, "num_predict": 300}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", body, {"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["message"]["content"]


def _clean(s):
    m = re.search(r"```(?:sql)?\s*(.*?)```", s, re.S)
    return (m.group(1) if m else s).strip().rstrip(";")


def llm_fallback(question, lang, e):
    hints = []
    if e.products: hints.append("products: " + "; ".join(product_sql(p) for p in e.products))
    if e.regions: hints.append("region IN " + str(tuple(e.regions)))
    if e.inns: hints.append("INN " + ", ".join(e.inns))
    if e.brokers: hints.append("broker " + ", ".join(e.brokers))
    if e.date_from: hints.append(f"deal_date BETWEEN '{e.date_from}' AND '{e.date_to}'")
    user = question + (("\n[verified filters: " + " | ".join(hints) + "]") if hints else "")
    msgs = [{"role": "system", "content": SCHEMA + "\nAnswer with SQL only."}, {"role": "user", "content": user}]
    sql, rows = None, None
    try:
        for attempt in range(2):
            sql = _clean(_llm(msgs))
            if not re.match(r"(?is)^\s*(select|with)\b", sql) or re.search(r"(?i)\b(insert|update|delete|drop|attach|copy|pragma|install|load)\b", sql):
                rows = None; break
            try:
                rows = q(sql); break
            except Exception as ex:
                msgs += [{"role": "assistant", "content": sql}, {"role": "user", "content": f"Error: {str(ex)[:200]}. Fix it, SQL only."}]
    except Exception:
        return Answer(lang, T["unclear"][lang], route="fallback:llm-unavailable", confidence="none", no_data=True)
    flat = [v for r in (rows or []) for v in r]
    if not rows or all(v is None for v in flat) or "NO_DATA" in [str(v) for v in flat]:
        a = nodata(lang, "empty", "llm-empty"); a.sql = sql or ""; a.route = "fallback:llm"; return a
    cols = len(rows[0])
    lines = ["; ".join(fmt(v, 2 if isinstance(v, float) and abs(v) < 1e6 else 0) for v in r) for r in rows[:10]]
    head = {"ru": "Результат запроса (автоматически составленный запрос — проверьте основание ниже):",
            "uz": "So'rov natijasi (avtomatik tuzilgan so'rov — quyidagi asosni tekshiring):"}[lang]
    return Answer(lang, head + "\n" + "\n".join(lines), sql, rows, route="fallback:llm", confidence="low")


# ------------------------------------------------------------------ LLM planner (semantic layer)
PLAN_PROMPT = """You route questions about a commodity-exchange trades database (2022-01-03..2026-10-30) to ONE predefined metric.
Do not compute anything. Return JSON only:
{"intent": one of [count, amount, volume, avg_price, avg_deal, stat, top, largest, status, distinct, share, growth, delay, by_month, rate, no_data, other],
 "dim": for top: region|seller|buyer|seller_broker|buyer_broker|product,
 "by": count|amount,
 "stat": for stat: median|min|max|stddev, "of": price|amount,
 "who": for distinct: buyer|seller|any|broker,
 "delay": for delay: payment|delivery_after_payment|delivery_after_deal,
 "status": none|annulled|partial|executed|not_annulled,
 "contract_type": none|export|domestic, "form": none|spot|forward|futures,
 "usd": true if the question wants US dollars,
 "side": any|seller|buyer (for INN / broker filters),
 "products": list of product names in Russian if a product is mentioned (e.g. хлопок-сырец, пшеница, мука пшеничная, сахар, цемент, бензин),
 "years": list of years mentioned, "n": number for top-N or null}
Meanings: count = number of deals; amount = money total; volume = quantity traded; avg_deal = average deal size; share = percentage of deals with a filter;
growth = change between two years; by_month = which month/year had most; status = breakdown by deal status.
Use no_data when the database cannot contain the answer (forecasts, reasons, people, phones, stock prices, other exchanges).
Use other when no metric fits."""

KW = {  # JSON field -> keywords the templates already understand
    "dim": {"region": " регион ", "seller": " продавец ", "buyer": " покупатель ", "seller_broker": " брокер продавца ", "buyer_broker": " брокер покупателя ", "product": " товар "},
    "by": {"count": " по количеству ", "amount": " сумма "},
    "stat": {"median": " медиана ", "min": " минимальная ", "max": " максимальная ", "stddev": " отклонение "},
    "of": {"price": " цена ", "amount": ""},
    "who": {"buyer": " покупателей ", "seller": " продавцов ", "any": "", "broker": " брокеров "},
    "delay": {"payment": " срок оплаты ", "delivery_after_payment": " срок поставки после оплаты ", "delivery_after_deal": " срок поставки "},
    "status": {"annulled": " аннулирован ", "partial": " частично ", "executed": " исполнен ", "not_annulled": " состоялись "},
    "contract_type": {"export": " экспорт ", "domestic": " внутренний "},
    "form": {"spot": " спот ", "forward": " форвард ", "futures": " фьючерс "},
    "side": {"seller": " продавец ", "buyer": " покупатель "},
}
PLAN_INTENTS = {"count", "amount", "volume", "avg_price", "avg_deal", "stat", "top", "largest", "status", "distinct", "share", "growth", "delay", "by_month", "rate"}


def llm_plan(question, lang, e, t):
    try:
        body = json.dumps({"model": LLM_MODEL, "format": "json", "stream": False, "options": {"temperature": 0, "num_predict": 200},
                           "messages": [{"role": "system", "content": PLAN_PROMPT}, {"role": "user", "content": question}]}).encode()
        req = urllib.request.Request("http://localhost:11434/api/chat", body, {"content-type": "application/json"})
        plan = json.loads(json.load(urllib.request.urlopen(req, timeout=60))["message"]["content"])
    except Exception:
        return None
    intent = plan.get("intent")
    if intent == "no_data":
        a = nodata(lang, "empty", "llm-no-data"); a.route = "planner:no_data"; return a
    if intent not in PLAN_INTENTS: return None
    # entities: rules win; the planner may only add values that exist in the closed vocabularies
    known = {n for n, _, _ in PRODUCTS}
    if not e.products:
        e.products = [p for p in (plan.get("products") or []) if isinstance(p, str) and p.lower() in known]
    if not e.date_from and plan.get("years"):
        ys = [int(y) for y in plan["years"] if str(y).isdigit()]
        if ys:
            from datetime import date as _d
            e.date_from, e.date_to, e.period_label = _d(min(ys), 1, 1), _d(max(ys), 12, 31), str(min(ys)) if min(ys) == max(ys) else f"{min(ys)}–{max(ys)}"
            if e.date_to < DATA_START or e.date_from > DATA_END: return nodata(lang, "range", "range")
    if plan.get("n") and str(plan["n"]).isdigit(): e.top_n = int(plan["n"])
    t2 = t
    for k, m in KW.items():
        v = plan.get(k)
        if isinstance(v, str) and v in m: t2 += m[v]
    if plan.get("usd") is True: t2 += " доллар "
    if intent == "growth" and len(set(re.findall(r"20\d{2}", t2))) < 2:
        t2 += " " + " ".join(str(y) for y in (plan.get("years") or []))
    sql, rows, txt, notes = TEMPLATES[intent](t2, e, lang)
    if not sql: return None
    if txt is None or not rows or rows[0][0] is None:
        a = nodata(lang, "empty", "empty"); a.sql = sql; a.route = f"planner:{intent}"; return a
    basis = {"ru": "Основание", "uz": "Asos"}[lang]
    note = {"ru": "вопрос распознан моделью как «" + intent + "» — проверьте основание", "uz": "savol model tomonidan «" + intent + "» deb aniqlandi — asosni tekshiring"}[lang]
    txt += f"\n\n{basis}: " + "; ".join(notes + [note]) + "."
    return Answer(lang, localize(txt, lang), sql, rows, route=f"planner:{intent}", confidence="medium")


# ------------------------------------------------------------------ Gemini path: model plans, code verifies and computes
_UZ = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
    ["a","b","v","g","d","e","e","j","z","i","y","k","l","m","n","o","p","r","s","t","u","f","h","s","ch","sh","sh","","i","","e","yu","ya","o","k","g","h"]))
_names = None


def name_key(s):
    s = s.lower().replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("`", "'")
    s = "".join(_UZ.get(c, c) for c in s)
    s = s.replace("o'", "o").replace("g'", "g").replace("'", "").replace('"', " ").replace("«", " ").replace("»", " ")
    s = s.replace("kh", "h").replace("ts", "s").replace("q", "k").replace("x", "h").replace("w", "v")
    s = re.sub(r"\b(mchj|mchzh|ooo|ok|ao|ak|aj|xk|qk|hk|fx|kk|chk|masuliyati cheklangan jamiyati|masuliyati cheklangan|oilaviy korhona|jamiyati)\b", " ", s)
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\s+", " ", s)).strip()


def match_company(name):
    global _names
    from rapidfuzz import process, fuzz
    if _names is None:
        _names = {}
        for inn, nm in con().execute('select "ИНН","Наименование" from participants').fetchall():
            _names.setdefault(name_key(nm), (inn, nm))
    k = name_key(name)
    if not k: return None
    if k in _names: return _names[k]
    hit = process.extractOne(k, list(_names.keys()), scorer=fuzz.ratio, score_cutoff=88)
    return _names[hit[0]] if hit else None


def _region_of(x):
    if x in REGIONS: return x
    xl = _norm(x)
    for reg, al in REGIONS.items():
        if reg.lower() in xl or any(a in xl for a in al): return reg
    return None


def _gemini_answer(question, t, e, lang):
    from . import gemini as G, semantic as S
    from datetime import date as _d
    if not G.available(): return None
    try:
        from .semantic import BASES
        from .values import V as _V
        p = G.plan(question, [], _V().regions, bases=_V().products, lang=lang)
    except Exception as ex:
        print("gemini error:", str(ex)[:120]); return None
    if not p:
        TR.step("llm_error", error="no plan returned"); return None
    interp = (p.get("interpretation") or "").strip()
    if p.get("answerable") is False:
        why = (p.get("missing") or "").strip() or T["unknown"][lang].format(x="—").split(".")[0]
        a = Answer(lang, T["nodata"][lang].format(why=why.rstrip(".")), route="gemini:not-answerable", confidence="none", no_data=True)
        return a
    # ---- entities: verified against closed lists / the database
    # ---- products and regions: resolved against values read from the database (no hand-written lists)
    from .values import V as _V, resolve_product, resolve_region
    vb = _V()
    category_set = any((p.get(f) or "none") != "none" for f in ("product_category", "product_group", "industry"))
    sets = []
    for m in p.get("mentioned_products") or []:
        raw, term = (m.get("as_written", ""), m.get("registry_term", "")) if isinstance(m, dict) else (str(m), str(m))
        names, how = resolve_product(term or raw)
        TR.step("product_resolution", written=raw, registry_term=term, resolved=names[:25], n=len(names), how=how)
        if not names:
            return nodata(lang, "product", "unresolved-product", x=raw or term)
        sets.append((term or raw, names))
    if not sets:
        picks = [b for b in (p.get("product_bases") or []) if b in vb.products]
        if picks: sets.append(("товар", picks))
    e.product_sets = sets
    e.products = [lab for lab, _ in sets]
    p["product_bases"] = sorted({b for _, n in sets for b in n})
    regs = [r for r in (resolve_region(x) for x in (p.get("regions") or [])) if r]
    e.regions = list(dict.fromkeys(regs))
    inns = set(e.inns) | {x for x in (p.get("inns") or []) if re.fullmatch(r"\d{9}", str(x))}
    for nm in p.get("company_names") or []:
        m = match_company(nm)
        TR.step("company_match", written=nm, key=name_key(nm), matched=m)
        if m: inns.add(m[0])
        else: return nodata(lang, "company", "company", x=nm)
    e.inns = sorted(inns)
    brs = set(e.brokers)
    for b in p.get("brokers") or []:
        m = re.search(r"(\d{1,3})", str(b))
        if m: brs.add(f"BK{int(m.group(1)):03d}")
    e.brokers = sorted(brs)
    for x in e.inns:
        if not q(f"SELECT 1 FROM participants WHERE \"ИНН\"='{x}'"): return nodata(lang, "inn", "inn", x=x)
    for b in e.brokers:
        if not q(f"SELECT 1 FROM brokers WHERE \"Код брокера\"='{b}'"): return nodata(lang, "broker", "broker", x=b)
    try:
        if p.get("date_from") and p.get("date_to"):
            df, dt = _d.fromisoformat(p["date_from"][:10]), _d.fromisoformat(p["date_to"][:10])
            e.date_from, e.date_to = min(df, dt), max(df, dt)
            e.period_label = e.date_from.strftime("%d.%m.%Y") + ("" if df == dt else "–" + e.date_to.strftime("%d.%m.%Y"))
    except ValueError:
        pass
    ys = [int(y) for y in (p.get("years") or []) if str(y).isdigit() and 2000 <= int(y) <= 2100]
    if p.get("compare") == "year" and ys: e.years = sorted(set(ys))
    if e.date_from and (e.date_to < DATA_START or e.date_from > DATA_END): return nodata(lang, "range", "range")
    if e.years and all(y < 2022 or y > 2026 for y in e.years): return nodata(lang, "range", "range")
    # ---- entity accounting: every product/place the question names must resolve, otherwise refuse (never widen the question)
    from .semantic import BASES
    resolved_products = p.get("product_bases") or []
    if (p.get("mentioned_places") or []) and not e.regions and not (p.get("region_scope") or "").endswith("company"):
        return nodata(lang, "foreign", "unresolved-place")
    kind = p.get("kind") or "deals"
    TR.step("verify", kind=kind, products=resolved_products, product_bases=[b for b in (p.get("product_bases") or []) if b in BASES()],
            dropped_bases=[b for b in (p.get("product_bases") or []) if b not in BASES()], regions=e.regions, inns=e.inns,
            brokers=e.brokers, period=e.period_label, years=e.years, mentioned_products=p.get("mentioned_products"),
            mentioned_places=p.get("mentioned_places"))
    basis = {"ru": "Основание", "uz": "Asos"}[lang]
    understood = {"ru": "Понято как", "uz": "Tushunilgan savol"}[lang]
    def wrap(sql, rows, txt, notes, route):
        if txt is None or not rows or rows[0][0] is None:
            a = nodata(lang, "empty", "empty"); a.sql = sql or ""; a.route = route; return a
        if e.inns and kind == "deals":
            nm = q(f"SELECT \"Наименование\" FROM participants WHERE \"ИНН\"='{e.inns[0]}'")
            if nm: txt = f"{nm[0][0]} (ИНН {e.inns[0]}). " + txt
        if interp: txt += f"\n\n{understood}: {interp}"
        if notes: txt += f"\n{basis}: " + "; ".join(dict.fromkeys(notes)) + "."
        return Answer(lang, localize(txt, lang), sql, rows, route=route, confidence="high")
    try:
        if kind == "usd_rate":
            mode = p.get("rate_mode") or "on_date"
            rd = (p.get("rate_date") or "")[:10]
            if mode == "on_date" and rd:
                sql = f"SELECT rate_date, uzs_per_usd FROM usd_rates WHERE rate_date = DATE '{rd}'"
            else:
                w = f"WHERE rate_date BETWEEN DATE '{e.date_from}' AND DATE '{e.date_to}'" if e.date_from else f"WHERE rate_date <= DATE '{DATA_END}'"
                sql = {"avg": f"SELECT min(rate_date), round(avg(uzs_per_usd),2), min(uzs_per_usd), max(uzs_per_usd) FROM usd_rates {w}",
                       "max": f"SELECT rate_date, uzs_per_usd FROM usd_rates {w} ORDER BY uzs_per_usd DESC LIMIT 1",
                       "min": f"SELECT rate_date, uzs_per_usd FROM usd_rates {w} ORDER BY uzs_per_usd ASC LIMIT 1",
                       "first": f"SELECT rate_date, uzs_per_usd FROM usd_rates {w} ORDER BY rate_date ASC LIMIT 1",
                       "last": f"SELECT rate_date, uzs_per_usd FROM usd_rates {w} ORDER BY rate_date DESC LIMIT 1",
                       "change": f"SELECT min(rate_date), max(rate_date), arg_min(uzs_per_usd, rate_date), arg_max(uzs_per_usd, rate_date), round(100.0*(arg_max(uzs_per_usd, rate_date)/arg_min(uzs_per_usd, rate_date)-1),4) FROM usd_rates {w}"}.get(mode, f"SELECT rate_date, uzs_per_usd FROM usd_rates {w} ORDER BY rate_date DESC LIMIT 1")
            r = q(sql)
            if not r or r[0][0] is None: return wrap(sql, [], None, [], "gemini:usd_rate")
            if mode == "change":
                d0, d1, v0, v1, pct = r[0]
                txt = {"ru": f"Курс доллара: {fmt(v0,2)} сум ({d0:%d.%m.%Y}) → {fmt(v1,2)} сум ({d1:%d.%m.%Y}); изменение {pct:+.2f}%.",
                       "uz": f"Dollar kursi: {fmt(v0,2)} so'm ({d0:%d.%m.%Y}) → {fmt(v1,2)} so'm ({d1:%d.%m.%Y}); o'zgarish {pct:+.2f}%."}[lang]
                return wrap(sql, [(pct, v0, v1)], txt, ["курс из usd_rates: первый и последний день периода"], "gemini:usd_rate")
            if len(r[0]) == 2:
                txt = {"ru": f"Курс доллара на {r[0][0]:%d.%m.%Y}: {fmt(r[0][1],2)} сум за 1 USD.", "uz": f"{r[0][0]:%d.%m.%Y} holatiga dollar kursi: 1 USD = {fmt(r[0][1],2)} so'm."}[lang]
            else:
                txt = {"ru": f"Средний курс: {fmt(r[0][1],2)} сум (мин {fmt(r[0][2],2)}, макс {fmt(r[0][3],2)}).", "uz": f"O'rtacha kurs: {fmt(r[0][1],2)} so'm (min {fmt(r[0][2],2)}, maks {fmt(r[0][3],2)})."}[lang]
            return wrap(sql, r, txt, ["курс из usd_rates"], "gemini:usd_rate")
        if kind == "company_info" and e.inns:
            sql, r, txt, n = tpl_company(t, e, lang); return wrap(sql, r, txt, n, "gemini:company_info")
        if kind == "broker_info" and e.brokers:
            sql, r, txt, n = tpl_broker(t, e, lang); return wrap(sql, r, txt, n, "gemini:broker_info")
        if kind in ("participants_directory", "brokers_directory"):
            t2 = t + (" брокер " if kind == "brokers_directory" else "")
            if p.get("vat") == "no": t2 += " не плательщик ндс "
            elif p.get("vat") == "yes": t2 += " плательщик ндс "
            if p.get("side") == "seller": t2 += " продавец "
            elif p.get("side") == "buyer": t2 += " покупатель "
            if p.get("share"): t2 += " процент "
            if p.get("activity") and p.get("activity") != "none": t2 += f" §act={p['activity']}§ "
            sql, r, txt, n = tpl_directory(t2, e, lang); return wrap(sql, r, txt, n, "gemini:" + kind)
        out = S.run_plan(p, t, e, lang)
        if out is None: return None
        s, sql, rows, txt, notes = out
        if s is None: return wrap(sql, rows, txt, notes, "gemini:contracts:" + str(p.get("metric")))
        route = f"gemini:{s.metric}" + (f"/{s.group_by}" if s.group_by else "") + ("/share" if s.share else "") + (f"/cmp-{s.compare}" if s.compare else "")
        return wrap(sql, rows, txt, notes, route)
    except Exception as ex:
        print("gemini plan error:", str(ex)[:160], "|", question[:60]); return None
