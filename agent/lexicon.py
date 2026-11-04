"""Closed-world vocabulary check.

The data only knows: deals (date, product, quantity, amount, price, region, status, contract type/form, auction,
payment/delivery dates), participants (INN, name, region, activity, role, VAT), brokers, and the USD rate.
A question whose content words are not all explainable by that vocabulary is about something the data does not
contain (insurance, transport mode, founders, GOST, ...). We refuse instead of answering a nearby question.
"""
import re

# word prefixes (lowercase). Russian and Uzbek (Latin; Cyrillic Uzbek is transliterated before this check).
KNOWN = """
скол скок сколь какой какая какое какие каки каков как кто где когда куда что чем это этот эта эти этом этой том тот та те
всего весь всей вся все всех всем итог итого общ был было была были будет есть ли или либо также тоже только лишь именно
данн базе база реестр бирж запис зарегистр отраж прош прох через для про при над под между после перед около более менее
больш меньш само самы наиб наим макс мини сред медиан чаще реже каждый каждо год году года годы годам лет месяц квартал
день дня дней дням дата даты число числу числ время времени период начал конц конец пред перв втор трет четв пят шест
сделк сделок сделка сделки сделку сделкам сделках сделак транзакц операц торг торгов продаж прода купи куп покуп закуп
сумм стоим стои цен оборот объ тонн килограм литр метр штук лот поставк постав оплат оплач аннулир исполн частич подпис
заключ статус состоя доля долю доли процент рост вырос снизил упал измен сравн разниц динамик уникальн различн разн
количеств вид видов наимен назв называ товар продукт сорт марк класс тип форм контракт карточк договор экспорт внутрен
спот форвард фьючерс аукцион обратн английск двусторон регион област город район склад брокер компан участник фирм
продав покупател сторон роль инн ндс плательщ деятельност производ перерабат кластер транспортн строител доллар долл
курс сум сумах сумов узбек usd крупн высок низк дешев дорог полност фактическ недопостав остал пересч эквивалент
среднем занима получа необход учти пишут разному встреча таблиц выступа являет находит относ работ скаж покаж назов
расскаж дай дайте нужн хочу интерес пожалуйст мне нам понят картин сравни где лидер топ один одна одно одни одинаков
тот же тем же совпада сам себе себя друг кажд своих свои разных сколько-то каких-то котор которы которой было-ли
больше-всего чаще-всего возможн вообще примерно около всего-лишь данные данным реестре реестра наш зафиксир прошло
шт кг куб кв пэ вр цем аи бнд дт
qancha nechta necha nechi neча qaysi qanaqa qanday kim nima qachon qayer qaerda jami umumiy hammasi barcha butun birga
qo'shil hisob bo'l bol bo'yicha boyicha bilan uchun davomida mobaynida yil yili yilda yilning yilgi yilga yildan yilgacha
oy oyida oyi kun kuni kunda chorak davr sana vaqt bitim savdo sotil sotgan sotib sot olgan olin sotuv xarid xaridor
sotuvchi summa so'm som pul narx qiymat hajm tonna kilogramm litr metr dona lot yetkaz to'lov tolov to'lan tolan bekor
bajar qisman imzo holat eksport ichki spot forvard fyuchers auksion hudud viloyat shahar tuman ombor broker kompaniya
firma ishtirokchi korxona tashkilot stir qqs nds faoliyat ishlab klaster mahsulot tovar nav marka turi tur ko'p kop kam
eng katta kichik yuqori past arzon qimmat o'rtacha ortacha mediana foiz ulush o'sish o'sdi osdi kamay o'zgar ozgar
solishtir taqqos nisbatan farq dollar kurs valyuta ro'yxat royxat qayd ma'lumot malumot ko'rsat korsat ayt bering
haqida edi ekan qilin qilgan qilingan qilinmagan qilinganlarsiz tushgan tush tuzil yozil uchray topil yetkazilmagan
qolgan amalga oshgan oshmagan jadval birja birjada kiradi hisoblan ishlag ishlay deb shu ushbu bu har xil turli noyob
yoki va ham emas bilan necha-necha qaysi-biri birinchi ikkinchi uchinchi to'rtinchi tortinchi beshinchi oltinchi
bo'yicha-chi nechtasi qanchasi soni sonini qismi ortiq kamida eng-ko'p tez-tez faol yetakchi kodli kod nomi nomlan
tegishli ro'yxatdan sifatida orasida ichida tomon tomoni tomondan boshi boshida oxiri oxirida o'tgan otgan keyin oldin
mart iyun iyul avgust may aprel fevral yanvar sentabr oktabr noyabr dekabr
оказал независим вместе справочн срок плат потрат нов актив денег деньг из-за разн неопл кабел силов указан учтен
учет учёт значени показател информац сведен расскаж подробн списк перечисл размер средн вес весь всё значит итогов
etil birlik kontor joylash shakl qatnash raqam berish davom etadi shahr yoqilg yonilg muddat ro'yxati asosiy holda
xomashyo xom ashyo жд yog yog'
va ham bilan uchun lekin ammo yoki agar chunki qilib qiladi qilmoqda qilsak qilish bo'ladi bo'lsa bo'lib bo'lgan bo'ldi
edi ekan emas yo'q yoq bor kerak mumkin faqat hatto yana endi hozir oldin doim hech hamma ba'zi ko'proq kamroq juda
nechanchi oshadi oshgan amalga ijro to'liq toliq tuliq umuman taxminan sarf ishlat xarid harid sotish savdolar olindi
berildi berilgan tuz imzolang ro'y sodir kiritil ko'rsatkich natija miqdor darajasi tarzda nisbatan solishtir taqqos
farqi qanchaga qanchalik qaysilar kimlar kimga kimdan markasiz markali oddiy alohida biror birorta bormi yo'qmi
o'zi ozi shunday bunday qanchadan nechadan topil chiq tashqari ichki tashqi hozirgi oxirgi birinchi so'nggi songgi
скрыва являет представ находит включа исключа учитыва среди внутри против относительн сравнени выше ниже лучше хуже
целиком отдельн совокупн суммарн валов физическ денежн натуральн стоимостн прошел прошла провел провела провёл заключил
совершил совершен осуществл проведен реализов отгруж закупк закупил приобрел приобрёл продает продаёт торгует торговал
участвов выступал работал получил получен поступил выручк обычно правило сначала потом затем вначале разница отличи
разнит удельн процентн соотношен пропорц какому каким какими каких чей чья чьи насколько приблизительн точно ровно
ещё еще уже пока кроме помимо итоге целом основн главн первую очередь вообще всего-навсего сумме суммам
median product seller buyer base price amount volume count total deals trades
""".split()
KNOWN += [k.replace("'", "") for k in KNOWN if "'" in k]
KNOWN = sorted(set(KNOWN), key=len, reverse=True)

# extra known tokens are added at load time: product base-name words, company-name words, region/month aliases
_extra = set()      # company / broker name words: exact match only
_small = set()      # product, region, month words: stem match
RU2LAT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                  ["a","b","v","g","d","e","yo","zh","z","i","y","k","l","m","n","o","p","r","s","t","u","f","kh","ts","ch","sh","sch","","y","","e","yu","ya"]))
UZC2LAT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъьэюяўқғҳ",
    ["a","b","v","g","d","e","yo","j","z","i","y","k","l","m","n","o","p","r","s","t","u","f","x","ts","ch","sh","sh","'","","e","yu","ya","o'","q","g'","h"]))
LOOKALIKE = str.maketrans("aeopcxyk", "аеорсхук")


def _lat(w, table): return "".join(table.get(c, c) for c in w)


def add_known(words, small=False):
    for w in words:
        w = w.lower().strip()
        if len(w) < 3: continue
        (_small if small else _extra).add(w)
        if small and re.search("[а-я]", w):
            _small.add(_lat(w, RU2LAT)); _small.add(_lat(w, RU2LAT).replace("kh", "x"))


def _init_known():
    global KNOWN
    extra = []
    for k in KNOWN:
        if re.search("[а-я]", k):
            extra += [_lat(k, RU2LAT), _lat(k, RU2LAT).replace("kh", "x").replace("sch", "sh")]
    KNOWN = sorted(set(KNOWN + extra), key=len, reverse=True)


_init_known()


def _known(w):
    if len(w) < 4 or w in _extra: return True
    if any(w.startswith(k) or (len(k) >= 6 and w.startswith(k[:5])) for k in KNOWN): return True
    if w.startswith("не") and len(w) > 5 and _known(w[2:]): return True
    for x in _small:
        if len(x) < 4 or x[:3] != w[:3]: continue
        cp = len(next((i for i in range(min(len(w), len(x))) if w[i] != x[i]), None) and w[:next(i for i in range(min(len(w), len(x))) if w[i] != x[i])] or w[:min(len(w), len(x))])
        if cp >= max(3, min(len(w), len(x)) - 2): return True
    from rapidfuzz.distance import Levenshtein
    for k in KNOWN:
        if len(k) >= 5 and len(w) >= len(k) and Levenshtein.distance(w[:len(k)], k) <= 1: return True
    for x in _small:
        if len(x) >= 6 and Levenshtein.distance(w[:len(x)], x) <= 1: return True
    return False


def unknown_words(t: str):
    """t = normalized text (agent.entities._norm). Returns content words not explained by the data vocabulary."""
    text = t.split(" || ")[0]
    out = []
    for raw in re.findall(r"[a-zа-яё'ўқғҳ][a-zа-яё'ўқғҳ-]{3,}", text):
        for w in raw.strip("'-").split("-"):
            w = w.strip("'")
            if re.search("[а-я]", w) and re.search("[a-z]", w): w = w.translate(LOOKALIKE)   # мукe -> муке
            forms = [w]
            if re.search("[а-яўқғҳ]", w): forms.append(_lat(w, UZC2LAT))                     # Cyrillic Uzbek
            if not any(_known(f) for f in forms): out.append(w)
    return out
