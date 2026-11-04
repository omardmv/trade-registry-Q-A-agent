"""Entity layer: rules + closed vocabularies from the data. No model.

Everything extracted here is checked against the database before a query runs, so an unknown
company, broker, product or period becomes an explicit "not in the data" instead of a guess.
"""
import re
from dataclasses import dataclass, field
from datetime import date

DATA_START, DATA_END = date(2022, 1, 3), date(2026, 10, 30)

# Canonical product -> (SQL regex on lower(contract base name), aliases in ru/uz).
# Order matters: compound products first, so "хлопковое масло" never becomes "хлопок".
PRODUCTS = [
    ("масло хлопковое", "(масло хлопков|хлопковое масло).*", ["хлопков масл", "хлопковое масл", "хлопкового масл", "масло хлопков", "масла хлопков", "paxta yog"]),
    ("масло подсолнечное", "(масло подсолн|подсолнечное масло).*", ["подсолн", "kungaboqar"]),
    ("шрот хлопковый", "шрот.*", ["шрот", "shrot", "kunjara"]),
    ("пряжа хлопковая", "пряжа.*", ["пряж", "ip kalava", "kalava", "paxta ip", "paxta ipi"]),
    ("хлопок-сырец", "хлопок.?сырец.*", ["хлоп", "пахта", "paxta", "xlopok"]),
    ("мука пшеничная", "мука.*", ["мук", " un ", " uni "]),
    ("пшеница", "пшениц.*", ["пшениц", "bug'doy", "bugdoy", "bug‘doy", "bug’doy"]),
    ("рис", "рис.*", ["рис ", "рису ", "риса ", "рисом ", "рисе ", "guruch"]),
    ("сахар", "сахар.*", ["сахар", "shakar", "qand ", "шакар"]),
    ("соль", "соль.*", ["соль", "соли", " tuz ", " tuzi "]),
    ("цемент", "(портландцемент|цемент).*", ["цемент", "портландцемент", "sement", "portlandsement"]),
    ("бензин", "бензин.*", ["бензин", "benzin"]),
    ("дизельное топливо", "(дизельное топливо|топливо дизельное).*", ["дизел", "dizel", "солярк"]),
    ("мазут", "мазут.*", ["мазут", "mazut"]),
    ("битум", "битум.*", ["битум", "bitum"]),
    ("сжиженный газ", "(газ сжиженный|сжиженный).*", ["сжижен", "пропан", "suyultirilgan gaz", "propan"]),
    ("трубы", "труб.*", ["труб", "quvur", "truba", "polietilen truba", "полиэтиленовые труб", "полиэтиленовых труб"]),
    ("полиэтилен", "полиэтилен.*", ["полиэтилен", "polietilen"]),
    ("полипропилен", "полипропилен.*", ["полипропилен", "polipropilen"]),
    ("поливинилхлорид", "поливинилхлорид.*", ["поливинилхлорид", "пвх", "pvx"]),
    ("карбамид", "(карбамид|мочевина).*", ["карбамид", "мочевин", "karbamid"]),
    ("аммиачная селитра", "(аммиачная селитра|селитра аммиачная).*", ["селитр", "selitra"]),
    ("кирпич", "кирпич.*", ["кирпич", "g'isht", "gisht", "g‘isht"]),
    ("стекло", "стекло.*", ["стекл", "shisha"]),
    ("арматура", "арматура.*", ["арматур", "armatura"]),
    ("проволока", "проволока.*", ["проволок", " sim "]),
    ("алюминиевый профиль", "(алюминиевый профиль|профиль алюминиевый).*", ["алюмин", "профил", "alyumin"]),
    ("медный катод", "(катод медный|медный катод).*", ["катод", "медн", "mis katod", "katod", "мис катод"]),
    ("гипс", "гипс.*", ["гипс", "gips"]),
    ("известь", "известь.*", ["известь", "извести", "известк", "ohak"]),
    ("эмаль", "эмаль.*", ["эмал", "emal"]),
    ("пиломатериалы", "пиломатериал.*", ["пиломатериал", "yog'och", "taxta"]),
    ("кабель", "кабель.*", ["кабел", "kabel"]),
    ("подшипник", "подшипник.*", ["подшипник", "podshipnik"]),
    ("комбикорм", "(комбикорм|кормосмесь).*", ["комбикорм", "корм", " yem "]),
    ("питьевая вода", "(вода питьевая|питьевая вода).*", ["вода", "воды", " suv ", " suvi "]),
    ("серная кислота", "(кислота серная|серная кислота).*", ["кислот", "kislota"]),
    ("сода каустическая", "сода.*", ["сода", "soda"]),
    ("азот", "азот.*", ["азот", "azot"]),
    ("аргон", "аргон.*", ["аргон", "argon"]),
    ("стальной лист", "лист стальной.*", ["лист сталь", "стальной лист", "po'lat list"]),
]

def _latin_variants(a):
    m = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                 ["a","b","v","g","d","e","yo","zh","z","i","y","k","l","m","n","o","p","r","s","t","u","f","x","ts","ch","sh","sch","","y","","e","yu","ya"]))
    base = "".join(m.get(c, c) for c in a)
    return {base, base.replace("x", "kh"), base.replace("x", "h"), base.replace("ts", "s"), base.replace("y", "i")}


for _i, (_n, _rx, _al) in enumerate(PRODUCTS):
    _extra = []
    for _a in _al:
        if re.search("[а-я]", _a):
            for _v in _latin_variants(_a.strip()):
                if len(_v) >= 4 and _v not in _al: _extra.append(_v + (" " if _a.endswith(" ") else ""))
    PRODUCTS[_i] = (_n, _rx, _al + _extra)

REGIONS = {
    "Toshkent shahri": ["ташкент", "toshkent", "tashkent"],
    "Toshkent viloyati": ["ташкентск", "toshkent viloyat"],
    "Namangan viloyati": ["наманган", "namangan"],
    "Buxoro viloyati": ["бухар", "buxoro", "bukhara"],
    "Samarqand viloyati": ["самарканд", "samarqand", "samarkand"],
    "Sirdaryo viloyati": ["сырдарь", "sirdaryo"],
    "Qashqadaryo viloyati": ["кашкадарь", "qashqadaryo"],
    "Xorazm viloyati": ["хорезм", "xorazm"],
    "Andijon viloyati": ["андижан", "andijon"],
    "Farg‘ona viloyati": ["ферган", "farg'ona", "farg‘ona", "fargona", "farg’ona"],
    "Surxondaryo viloyati": ["сурхандарь", "surxondaryo", "surxandaryo", "surhandaryo"],
    "Navoiy viloyati": ["навои", "navoiy"],
    "Qoraqalpog‘iston Respublikasi": ["каракалпак", "qoraqalpog"],
    "Jizzax viloyati": ["джизак", "jizzax"],
}

MONTHS = [  # index = month number - 1
    ["январ", "yanvar"], ["феврал", "fevral"], ["март", "mart"], ["апрел", "aprel"], ["мая", "май", "may"],
    ["июн", "iyun"], ["июл", "iyul"], ["август", "avgust"], ["сентябр", "sentabr", "sentyabr"],
    ["октябр", "oktabr", "oktyabr"], ["ноябр", "noyabr"], ["декабр", "dekabr"],
]

# words after "по" that are not products (so "по сумме" is not an unknown product)
NOT_PRODUCT = re.compile(r"^(исполн|аннул|частич|оплач|состо|спот|форвард|фьюч|экспорт|внутр|shahar|viloyat|hudud|turi|tur\b|mahsulot|davr|yil|oy\b|kun|kompaniya|sotuvchi|xaridor|holat|narx|summa|hajm|base|product|bitim|savdo|сумм|объ|количеств|числ|сделк|бирж|регион|брокер|компан|год|месяц|квартал|итог|всем|стат|цен|"
                         r"продавц|покупат|тип|форм|видам|дням|дат|оплат|поставк|област|город|экспорт|импорт|участ|товар|продукт|"
                         r"тонн|кг|килограмм|литр|штук|метр|частич|договор|контракт|счет|счёт|курс|доллар|сум|сравн|отнош|причин|итог|состоян|данн|повод|времен|дн|недел|полугод|сторон|уровн|средн|сравнен|всей|всех|всему|одн|кажд|разн|этом|прошл)")


@dataclass
class Entities:
    products: list = field(default_factory=list)      # canonical names
    regions: list = field(default_factory=list)
    inns: list = field(default_factory=list)
    brokers: list = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    period_label: str = ""
    exact_date: date | None = None
    unknown_product: str | None = None
    out_of_range: bool = False
    top_n: int | None = None
    years: list = field(default_factory=list)


CYR2LAT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъьэюяўқғҳ",
    ["a","b","v","g","d","e","yo","j","z","i","y","k","l","m","n","o","p","r","s","t","u","f","x","ts","ch","sh","sh","'","","e","yu","ya","o'","q","g'","h"]))


RU_ORD = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6}
UZ_ORD = {"birinchi": 1, "ikkinchi": 2, "uchinchi": 3, "to'rtinchi": 4, "tortinchi": 4, "beshinchi": 5, "oltinchi": 6,
          "bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "tort": 4, "besh": 5, "olti": 6}


def _years_from_words(t: str) -> str:
    """'две тысячи двадцать четвертом' / 'ikki ming yigirma to'rtinchi' -> 2024"""
    t = re.sub(r"(?:две|двух)\s+тысяч\w*\s+двадцат\w*\s+(перв|втор|трет|четв|пят|шест)\w*",
               lambda m: str(2020 + RU_ORD[m.group(1)]), t)
    t = re.sub(r"ikki\s+ming\s+yigirma\s+(birinchi|ikkinchi|uchinchi|to'rtinchi|tortinchi|beshinchi|oltinchi|bir|ikki|uch|to'rt|tort|besh|olti)\b",
               lambda m: str(2020 + UZ_ORD[m.group(1)]), t)
    return t


def _norm(t: str) -> str:
    t = re.sub(r"(?<!\d)[.,](?!\d)|[?!;:()\"«»]", " ", t)
    t0 = t.lower()
    t0 = t0.replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("`", "'")
    if re.search(r"[ўқғҳ]|\b(энг|нечта|қанча|йил|бўйича|қайси|ким|билан|бўлган|сотган)\b", t0):
        t0 = t0 + " || " + "".join(CYR2LAT.get(c, c) for c in t0)
    t = _years_from_words(_years_from_words(t0).replace("ё", "е"))
    return " " + t.lower().replace("ё", "е").replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("`", "'") + " "


def extract(text: str) -> Entities:
    t = _norm(text)
    e = Entities()
    e.inns = re.findall(r"\b(\d{9})\b", t)
    t_b = re.sub(r"\bбк", "bk", t)
    e.brokers = sorted({b.upper() for b in re.findall(r"\b(bk\s?\d{1,3})\b", t_b)})
    e.brokers = [re.sub(r"\s", "", b) for b in e.brokers]
    e.brokers = [f"BK{int(b[2:]):03d}" for b in e.brokers]
    m = re.search(r"(?:топ|top)[\s-]*(\d+)|(\d+)\s*(?:крупнейш|самых|ta eng|eng)", t)
    if m: e.top_n = int(m.group(1) or m.group(2))

    # products: compound aliases first, and consume the matched text
    rest = t
    for name, _, aliases in PRODUCTS:
        for a in aliases:
            # alias = word prefix; a trailing space in the alias means "whole word"
            pat = r"(?<![a-zа-я])" + re.escape(a.strip()) + (r"(?![a-zа-я'])" if a.endswith(" ") else "")
            if re.search(pat, rest):
                if name not in e.products: e.products.append(name)
                rest = re.sub(pat, " ", rest)
    # "хлопковое масло" also contains "хлоп": handled by consuming above
    for reg, al in REGIONS.items():
        if any(a in t for a in al): e.regions.append(reg)
    if "Toshkent viloyati" in e.regions: e.regions = [r for r in e.regions if r != "Toshkent shahri"]

    # unknown product: "по X" / "X bo'yicha" where X is no known product
    if not e.products:
        m = re.search(r"\bпо\s+([а-яa-z'-]{3,})", t) or re.search(r"([a-z'а-я-]{3,})\s+bo'yicha", t)
        if m and not NOT_PRODUCT.match(m.group(1)) and not any(any(a.strip() in m.group(1) for a in al) for al in REGIONS.values()):
            e.unknown_product = m.group(1)

    # "хлопковый шрот / хлопковая пряжа / хлопковое масло": the adjective is not raw cotton
    if "хлопок-сырец" in e.products and len(e.products) > 1 and not re.search(r"хлопок|хлопка|хлопку|хлопком|хлопк[уе]|пахта|paxta(?! (ip|yog|shrot|kunjara))|xlopok", t):
        e.products.remove("хлопок-сырец")
    _periods(t, e)
    return e


def _periods(t: str, e: Entities):
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", t)]
    e.years = sorted(set(years))
    if re.search(r"этом году|текущем году|bu yil|joriy yil", t): years.append(2026)
    if re.search(r"прошлом году|прошлый год|o'tgan yil", t): years.append(2025)
    # exact date: 15 марта 2024 / 15.03.2024 / 2024-03-15
    m = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](20\d{2})\b", t) or None
    if m:
        e.exact_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m2 = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", t)
    if m2: e.exact_date = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
    month = None
    for i, al in enumerate(MONTHS):
        if any(re.search(r"(?<![a-zа-я])" + a, t) for a in al): month = i + 1; break
    if month and not e.exact_date:
        m3 = re.search(r"\b(\d{1,2})[\s-]*(?:-?(?:го|e|chi))?\s*(?:" + "|".join(MONTHS[month - 1]) + ")", t)
        if m3 and years: e.exact_date = date(years[0], month, int(m3.group(1)))
    if e.exact_date:
        e.date_from = e.date_to = e.exact_date
        e.period_label = e.exact_date.strftime("%d.%m.%Y")
    elif years:
        q = re.search(r"\bq([1-4])\b|\b([1-4])[\s-]*(?:-?й|-?ой|-?м|-?chi)?\s*(?:квартал|chora[kg])|(?:квартал\w*|chora[kg]\w*)\s*([1-4])\b", t)
        if not q:
            mm = re.search(r"\b(iv|iii|ii|i)[\s-]*(?:квартал|chora[kg])", t) or re.search(r"(перв|втор|трет|четв|birinchi|ikkinchi|uchinchi|to'rtinchi)\w*\s+(?:квартал|chora[kg])", t)
            if mm:
                qmap = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "перв": 1, "втор": 2, "трет": 3, "четв": 4, "birinchi": 1, "ikkinchi": 2, "uchinchi": 3, "to'rtinchi": 4}
                class _Q:
                    def __init__(s, n): s.n = n
                    def group(s, i): return str(s.n) if i == 1 else None
                q = _Q(qmap[mm.group(1)])
        y0, y1 = min(years), max(years)
        if q:
            qn = int(q.group(1) or q.group(2) or q.group(3))
            e.date_from, e.date_to = date(y0, 3 * qn - 2, 1), _month_end(y0, 3 * qn)
            e.period_label = f"Q{qn} {y0}"
        elif month:
            e.date_from, e.date_to = date(y0, month, 1), _month_end(y0, month)
            e.period_label = f"{month:02d}.{y0}"
        else:
            e.date_from, e.date_to = date(y0, 1, 1), date(y1, 12, 31)
            e.period_label = str(y0) if y0 == y1 else f"{y0}–{y1}"
    if e.date_from and (e.date_to < DATA_START or e.date_from > DATA_END):
        e.out_of_range = True


def _month_end(y, m):
    from calendar import monthrange
    return date(y, m, monthrange(y, m)[1])


def product_sql(name: str) -> str:
    rx = next(r for n, r, _ in PRODUCTS if n == name)
    return f"product_base SIMILAR TO '{rx}'"
