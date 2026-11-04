import duckdb, json, datetime, decimal

con = duckdb.connect('/Users/omarmdv/Documents/projects/uzexQualify/data/uzex_v2.duckdb', read_only=True)

def conv(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return str(v)
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float):
        return round(v, 2)
    return v

ITEMS = []

def add(id_, lang, kind, question, sql, note, limit=5):
    if sql is None:
        gold = []
    else:
        rows = con.execute(sql).fetchall()
        gold = [[conv(v) for v in row] for row in rows[:limit]]
    ITEMS.append({
        "id": id_, "lang": lang, "kind": kind, "question": question,
        "gold_sql": sql if sql is not None else "-- нет данных: поле/сущность отсутствует в схеме",
        "gold": gold, "note": note
    })

# ---------------- EXACT (i001-i012) ----------------
add("i001","ru","exact",
    "Подскажите, пожалуйста, какой код ТН ВЭД указан в карточке контракта №100003?",
    "SELECT \"Код ТН ВЭД\" FROM contracts WHERE \"Номер контракта\"='100003'",
    "Простой лукап по карточке контракта.")

add("i002","uz","exact",
    "100004-sonli shartnoma bo'yicha to'lovdan keyin tovarni necha kun ichida yetkazib berish kerak (Срок поставки)?",
    "SELECT \"Срок поставки\" FROM contracts WHERE \"Номер контракта\"='100004'",
    "Срок поставки для контракта 100004 (подшипник).")

add("i003","ru","exact",
    "Какой базис поставки указан в контракте №100005?",
    "SELECT \"Базис поставки\" FROM contracts WHERE \"Номер контракта\"='100005'",
    "Контракт 100005 — битум нефтяной.")

add("i004","uz","exact",
    "100006-son shartnomada mahsulot qanday qadoqlanishi (upakovka turi) ko'rsatilgan?",
    "SELECT \"Упаковка\" FROM contracts WHERE \"Номер контракта\"='100006'",
    "Контракт 100006 — кирпич керамический М-100.")

add("i005","ru","exact",
    "По контракту №100007 (кабель силовой) — сколько процентов составляет сумма залога и за сколько дней покупатель должен оплатить сделку?",
    "SELECT \"Сумма залога\", \"Срок оплаты\" FROM contracts WHERE \"Номер контракта\"='100007'",
    "Многочастный вопрос — оба значения в одной строке карточки контракта.")

add("i006","uz","exact",
    "100008-сон шартнома қачон рўйхатдан ўтган ва у қайси санагача амал қилади?",
    "SELECT \"Дата регистрации\", \"Действует до\" FROM contracts WHERE \"Номер контракта\"='100008'",
    "Контракт 100008 — портландцемент ЦЕМ I 42,5 Н.")

add("i007","ru","exact",
    "Какой курс доллара (сум за 1 USD) был установлен 15 июля 2025 года?",
    "SELECT uzs_per_usd FROM usd_rates WHERE rate_date = DATE '2025-07-15'",
    "Прямой лукап курса на дату.")

add("i008","uz","exact",
    "BK112 kodli brokerning rasmiy nomi qanday va u qaysi hududda ro'yxatdan o'tgan?",
    "SELECT \"Наименование\", \"Регион\" FROM brokers WHERE \"Код брокера\"='BK112'",
    "Лукап брокера по коду.")

add("i009","uz","exact",
    "ИНН раками 962079415 бўлган компания қандай номланади, у қайси ҳудудда рўйхатдан ўтган ва сотувчими ёки харидорми?",
    "SELECT \"Наименование\", \"Регион\", \"Роль\" FROM participants WHERE \"ИНН\"='962079415'",
    "Лукап участника по ИНН.")

add("i010","uz","exact",
    "\"Chotal Invest\" XK kompaniyasining STIR (INN) raqami nima va u savdolarda sotuvchi sifatida yoki xaridor sifatida qatnashadi?",
    "SELECT \"ИНН\", \"Роль\", \"Регион\" FROM participants WHERE \"Наименование\" LIKE '%Chotal Invest%'",
    "Обратный лукап по названию компании.")

add("i011","ru","exact",
    "Сколько контрактов (не сделок) зарегистрировано на продавца с ИНН 931612820?",
    "SELECT COUNT(*) FROM contracts WHERE \"ИНН продавца\"='931612820'",
    "Количество карточек контрактов у конкретного продавца (FAQEN OIL MCHJ).")

add("i012","uz","exact",
    "Navoiy viloyatida nechta brokerlik kontorasi ro'yxatdan o'tgan?",
    "SELECT COUNT(*) FROM brokers WHERE \"Регион\"='Navoiy viloyati'",
    "Количество брокеров в регионе.")

# ---------------- LOOSE (i013-i032) ----------------
add("i013","ru","loose",
    "скок всего штук кирпича керамического продали за все время, не считая аннулированные сделки",
    "SELECT SUM(volume_units) FROM deals_enriched WHERE product_base LIKE '%кирпич%' AND status != 'Аннулирован(Не оплачен)'",
    "Кирпич измеряется в штуках, не в тоннах.")

add("i014","uz","loose",
    "izvest (известь) butun davr davomida jami necha tonna sotilgan, bekor qilinganlarsiz?",
    "SELECT SUM(volume_units) FROM deals_enriched WHERE product_base LIKE '%известь%' AND status != 'Аннулирован(Не оплачен)'",
    "Известь негашеная + строительная, тонны.")

add("i015","ru","loose",
    "когда портландцемент продавался дешевле всего и по какой цене за тонну (без аннулированных)?",
    "SELECT deal_date, price_per_unit FROM deals_enriched WHERE product_base LIKE '%портландцемент%' AND status != 'Аннулирован(Не оплачен)' ORDER BY price_per_unit ASC LIMIT 1",
    "Минимальная цена, а не средняя.")

add("i016","uz","loose",
    "2024 yilda alyuminiy profil (barcha yozilishi) jami necha kilogramm sotilgan, bekor qilinganlarsiz?",
    "SELECT SUM(volume_units) FROM deals_enriched WHERE (product_base LIKE '%профиль алюминиевый%' OR product_base LIKE '%алюминиевый профиль%') AND extract(year from deal_date)=2024 AND status != 'Аннулирован(Не оплачен)'",
    "Единица измерения — килограмм, не тонна.")

add("i017","ru","loose",
    "какая доля сделок (в процентах) заключена в форме «Спот» от всех сделок в базе?",
    "SELECT ROUND(100.0*SUM(CASE WHEN contract_form='Спот' THEN 1 ELSE 0 END)/COUNT(*),2) FROM deals",
    "Доля считается от всех сделок (включая аннулированные) — это не денежный показатель.")

add("i018","uz","loose",
    "Qoraqalpog'iston Respublikasida nechta xil mahsulot turi (product_base) bo'yicha savdo qilingan?",
    "SELECT COUNT(DISTINCT product_base) FROM deals_enriched WHERE region='Qoraqalpog‘iston Respublikasi'",
    "Количество уникальных наименований товаров в регионе.")

add("i019","ru","loose",
    "какая в среднем цена за кубометр у газообразного аргона (без учёта аннулированных сделок)?",
    "SELECT AVG(price_per_unit) FROM deals_enriched WHERE product_base LIKE '%аргон%' AND status != 'Аннулирован(Не оплачен)'",
    "Средняя цена за единицу измерения.")

add("i020","uz","loose",
    "qaysi tovar toifasi (категория товара) bo'yicha eng kam bitim qilingan va nechta bitim bo'lgan?",
    "SELECT product_category, COUNT(*) c FROM deals_enriched GROUP BY 1 ORDER BY c ASC LIMIT 1",
    "Ранжирование категорий по количеству сделок (по возрастанию).")

add("i021","uz","loose",
    "буғдой бўйича энг кам савдо қилинган ҳудуд қайси ва у ерда нечта битим бўлган (бекор қилинганларсиз)?",
    "SELECT region, COUNT(*) c FROM deals_enriched WHERE product_base LIKE '%пшеница%' AND status != 'Аннулирован(Не оплачен)' GROUP BY 1 ORDER BY c ASC LIMIT 1",
    "Регион с наименьшим числом сделок по пшенице (не наибольшим).")

add("i022","uz","loose",
    "qaysi o'lchov birligida (unit) eng kam bitim qilingan va nechta bitim bo'lgan?",
    "SELECT unit, COUNT(*) c FROM deals GROUP BY 1 ORDER BY c ASC LIMIT 1",
    "Единица измерения с наименьшим числом сделок — 'метр'.")

add("i023","ru","loose",
    "в среднем на сколько дней задерживается поставка у тех сделок, где поставка пришла позже установленного срока?",
    "SELECT AVG(delivery_date - paid_date) FROM deals_enriched WHERE delivered_late",
    "Используется готовый признак delivered_late.")

add("i024","uz","loose",
    "2026-yilda nechta yangi shartnoma (kontrakt) ro'yxatdan o'tgan?",
    "SELECT COUNT(*) FROM contracts WHERE strptime(\"Дата регистрации\",'%d.%m.%Y') >= DATE '2026-01-01'",
    "Речь про дату регистрации контракта, а не про сделки.")

add("i025","ru","loose",
    "сколько разных брокеров выступали брокером продавца в сделках по хлопку-сырцу (все формы)?",
    "SELECT COUNT(DISTINCT seller_broker) FROM deals_enriched WHERE product_base LIKE '%хлопок%сырец%'",
    "distinct seller_broker по всем формам хлопка-сырца.")

add("i026","uz","loose",
    "2023 yilda dollar kursi o'rtacha qancha bo'lgan (yil bo'yicha o'rtacha qiymat)?",
    "SELECT AVG(uzs_per_usd) FROM usd_rates WHERE rate_date>=DATE '2023-01-01' AND rate_date<DATE '2024-01-01'",
    "Средний курс за календарный год, а не курс на конкретный день.")

add("i027","ru","loose",
    "когда в 2026 году доллар был дешевле всего (минимальный курс за год) и сколько он стоил?",
    "SELECT rate_date, uzs_per_usd FROM usd_rates WHERE rate_date>=DATE '2026-01-01' AND rate_date<DATE '2026-11-01' ORDER BY uzs_per_usd ASC LIMIT 1",
    "2026 год в данных есть только по 30.10.2026.")

add("i028","uz","loose",
    "неча компания «Транспортная компания» турига киради (иштирокчилар рўйхатида)?",
    "SELECT COUNT(*) FROM participants WHERE \"Вид деятельности\"='Транспортная компания'",
    "Справочник участников, поле «Вид деятельности».")

add("i029","ru","loose",
    "сравните экспорт в долларах — в 2023 и в 2026 году — в каком году экспортировали на бОльшую сумму?",
    "SELECT extract(year from deal_date) y, SUM(amount) s FROM deals_enriched WHERE contract_type='Экспортный' AND extract(year from deal_date) IN (2023,2026) AND status != 'Аннулирован(Не оплачен)' GROUP BY 1 ORDER BY 1",
    "Экспортные сделки в валюте контракта — это доллары (не сумы); 2026 год в данных неполный (по 30.10).")

add("i030","uz","loose",
    "qaysi broker xaridor brokeri sifatida eng ko'p bitimda qatnashgan (nechta bitim)?",
    "SELECT buyer_broker, COUNT(*) c FROM deals_enriched WHERE buyer_broker IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 1",
    "Ранжирование брокеров покупателя по числу сделок.")

add("i031","ru","loose",
    "какая отрасль промышленности дала наибольший оборот в сумах за весь период (без аннулированных)?",
    "SELECT industry, SUM(amount_uzs) s FROM deals_enriched WHERE status != 'Аннулирован(Не оплачен)' GROUP BY 1 ORDER BY s DESC LIMIT 1",
    "Используется amount_uzs (экспорт переведён в сумы по курсу на дату сделки).")

add("i032","uz","loose",
    "kimyo mahsulotlari (Химическая продукция) kategoriyasi bo'yicha jami savdo summasi (so'mda) qancha bo'lgan, bekor qilinganlarsiz?",
    "SELECT SUM(amount_uzs) FROM deals_enriched WHERE product_category='Химическая продукция' AND status != 'Аннулирован(Не оплачен)'",
    "Денежная сумма по категории товара.")

# ---------------- TRAP (i033-i044) ----------------
add("i033","ru","trap",
    "сколько тонн дизельного топлива (оба варианта написания — «дизельное топливо» и «топливо дизельное») продали в 2026 году?",
    "SELECT SUM(CASE WHEN unit='тонна' THEN volume_units WHEN unit='килограмм' THEN volume_units/1000.0 ELSE 0 END) FROM deals_enriched WHERE (product_base LIKE '%дизельное топливо%' OR product_base LIKE '%топливо дизельное%') AND extract(year from deal_date)=2026 AND status != 'Аннулирован(Не оплачен)'",
    "Ловушка: часть сделок по дизелю записана в тоннах, часть — в килограммах; килограммы нужно перевести в тонны и сложить оба варианта написания товара.")

add("i034","uz","trap",
    "2024-yilda ammiachnaya selitra (barcha yozilishi: 'аммиачная селитра' va 'селитра аммиачная') jami necha tonna sotilgan (bekor qilinganlarsiz)?",
    "SELECT SUM(volume_units) FROM deals_enriched WHERE (product_base LIKE '%аммиачная селитра%' OR product_base LIKE '%селитра аммиачная%') AND extract(year from deal_date)=2024 AND status != 'Аннулирован(Не оплачен)'",
    "Ловушка: один товар записан продавцами двумя разными словопорядками.")

add("i035","ru","trap",
    "сколько всего сделок по карбамиду (включая вариант «мочевина (карбамид)») было в 2025 году?",
    "SELECT COUNT(*) FROM deals_enriched WHERE (product_base LIKE '%карбамид%' OR product_base LIKE '%мочевина%') AND extract(year from deal_date)=2025",
    "Ловушка на объединение написаний; счётчик сделок — аннулированные не исключаются по умолчанию.")

add("i036","uz","trap",
    "2022-yilda faqat oziq-ovqatlik (продовольственная) bug'doy nechi tonna sotilgan — oddiy bug'doy va 3/4-klass bug'doyni bu hisobga qo'shmang, bekor qilinganlarsiz?",
    "SELECT SUM(volume_units) FROM deals_enriched WHERE product_base LIKE '%пшеница продовольственная%' AND extract(year from deal_date)=2022 AND status != 'Аннулирован(Не оплачен)'",
    "Ловушка: нужно взять только подмножество с словом «продовольственная», а не весь «пшеница».")

add("i037","ru","trap",
    "сколько всего составила сумма сделок (в сумах) по портландцементу за весь период, если считать вместе с аннулированными сделками?",
    "SELECT SUM(amount_uzs) FROM deals_enriched WHERE product_base LIKE '%портландцемент%'",
    "Ловушка: явно указано включить аннулированные — это меняет стандартное правило исключения.")

add("i038","uz","trap",
    "2024-yilda armatura po'lat bo'yicha jami necha tonna sotilgan — bekor qilingan bitimlarni ham qo'shib hisoblang?",
    "SELECT SUM(volume_units) FROM deals_enriched WHERE product_base LIKE '%арматура%' AND extract(year from deal_date)=2024",
    "Ловушка: явно включить аннулированные сделки в объём.")

add("i039","uz","trap",
    "хлопок-сырец бўйича фақат тўлиқ ижро этилган (ҳолати айнан «Исполнен», қисман ижро этилганларсиз) нечта битим бўлган?",
    "SELECT COUNT(*) FROM deals_enriched WHERE (product_base LIKE '%хлопок%сырец%') AND status='Исполнен'",
    "Ловушка: «Исполнен(Частично)» не считается, хотя обычно его включают в «выполненные».")

add("i040","uz","trap",
    "белгиланган тўлов муддатидан кечикиб тўланган битимлар умуман нечта бўлган?",
    "SELECT COUNT(*) FROM deals_enriched WHERE paid_late",
    "Ловушка: в этих данных ни одна сделка не оплачена с опозданием — правильный ответ 0, а не придуманное число.")

add("i041","ru","trap",
    "сколько всего бензина (всех марок) продали за весь период, без аннулированных сделок?",
    "SELECT unit, SUM(volume_units) FROM deals_enriched WHERE product_base LIKE '%бензин%' AND status != 'Аннулирован(Не оплачен)' GROUP BY 1",
    "Ловушка: бензин продаётся и в тоннах, и в литрах — единицы нельзя складывать вместе, нужно показать раздельно.")

add("i042","uz","trap",
    "2024-yilda paxta (barcha shakllarda birgalikda — спот, форвард, фьючерс) necha tonna sotilgan (tonnada, kilogrammda emas, bekor qilinganlarsiz)?",
    "SELECT SUM(volume_units)/1000.0 FROM deals_enriched WHERE (product_base LIKE '%хлопок%сырец%') AND extract(year from deal_date)=2024 AND status != 'Аннулирован(Не оплачен)'",
    "Ловушка: хлопок-сырец в базе записан в килограммах, нужно перевести в тонны.")

add("i043","ru","trap",
    "сколько действующих контрактов на профиль алюминиевый (любой словопорядок в названии) зарегистрировано со складом в Ферганской области?",
    "SELECT COUNT(*) FROM contracts WHERE \"Регион\"='Ферганская область' AND (\"Наименование продукта\" ILIKE '%проф%алюмин%' OR \"Наименование продукта\" ILIKE '%алюмин%проф%')",
    "Ловушка: регион в contracts.csv записан по-русски («Ферганская область»), а не как в deals («Farg‘ona viloyati»).")

add("i044","uz","trap",
    "oq shakar (сахар белый) bitimlarining necha foizi hali «Ждёт оплату» yoki «Оплачен» holatida — ya'ni tovar hali yetkazib berilmagan?",
    "SELECT ROUND(100.0*SUM(CASE WHEN status IN ('Ждёт оплату','Оплачен') THEN 1 ELSE 0 END)/COUNT(*),2) FROM deals_enriched WHERE product_base='сахар белый'",
    "Ловушка: нужно объединить именно два статуса, не один; знаменатель — все сделки по товару.")

# ---------------- NO_DATA (i045-i060) ----------------
add("i045","ru","no_data",
    "сколько тонн мёда было продано на бирже за весь период?",
    "SELECT COUNT(*) FROM deals_enriched WHERE (product_base LIKE '%мёд%' OR product_base LIKE '%мед%') AND product_base NOT LIKE '%медн%'",
    "Товар «мёд» отсутствует в реестре (0 — не выдуманное число, а реальный результат пустого поиска).")

add("i046","uz","no_data",
    "ipak-xomashyo (shёлк-сырец) savdosi UZEXda bo'lganmi, jami necha tonna sotilgan?",
    "SELECT COUNT(*) FROM deals_enriched WHERE product_base LIKE '%шелк%' OR product_base LIKE '%шёлк%' OR product_base LIKE '%ipak%'",
    "Шёлк-сырец в номенклатуре товаров отсутствует.")

add("i047","uz","no_data",
    "чой (чай) савдоси тўғрисида маълумот борми, жами қанча тонна сотилган?",
    "SELECT COUNT(*) FROM deals_enriched WHERE product_base LIKE '%чай%'",
    "Чай в номенклатуре товаров отсутствует.")

add("i048","ru","no_data",
    "сколько кубометров природного (магистрального, не сжиженного) газа было продано в 2024 году?",
    "SELECT COUNT(*) FROM deals_enriched WHERE product_base LIKE '%природный газ%'",
    "В данных есть только сжиженный газ (пропан-бутан), трубопроводного природного газа нет.")

add("i049","uz","no_data",
    "teri-xomashyo (кожа, шкуры) savdosi bo'yicha jami nechta bitim bo'lgan?",
    "SELECT COUNT(*) FROM deals_enriched WHERE product_base LIKE '%кожа%' OR product_base LIKE '%шкур%'",
    "Кожевенное сырьё в номенклатуре товаров отсутствует.")

add("i050","ru","no_data",
    "сколько сделок по газообразному аргону было заключено в Бухарской области?",
    "SELECT COUNT(*) FROM deals_enriched WHERE product_base LIKE '%аргон%' AND region='Buxoro viloyati'",
    "Легитимная нулевая комбинация: аргон торгуется только в 5 регионах, Бухарской области среди них нет.")

add("i051","uz","no_data",
    "gips (гипс) bo'yicha «Фьючерс» shaklida nechta bitim tuzilgan?",
    "SELECT COUNT(*) FROM deals_enriched WHERE product_base LIKE '%гипс%' AND contract_form='Фьючерс'",
    "Легитимная нулевая комбинация: фьючерсы в данных заключаются только по хлопку-сырцу.")

add("i052","ru","no_data",
    "сколько экспортных сделок было заключено по кабелю силовому?",
    "SELECT COUNT(*) FROM deals_enriched WHERE product_base LIKE '%кабель%' AND contract_type='Экспортный'",
    "Легитимная нулевая комбинация: кабель силовой нигде не продавался на экспорт.")

add("i053","uz","no_data",
    "nechta klaster turidagi (Вид деятельности='Кластер') kompaniya xaridor sifatida ro'yxatdan o'tgan?",
    "SELECT COUNT(*) FROM participants WHERE \"Вид деятельности\"='Кластер' AND \"Роль\"='Покупатель'",
    "Легитимная нулевая комбинация: компании вида «Кластер» в справочнике зарегистрированы только как продавцы.")

add("i054","ru","no_data",
    "кто подписал сделку №250000 со стороны покупателя (ФИО представителя)?",
    None,
    "ФИО подписанта/представителя не входит в состав данных сделки.")

add("i055","uz","no_data",
    "shartnomalar matni qaysi tilda (o'zbek, rus yoki ingliz) tuzilgan bo'ladi?",
    None,
    "Язык текста договора не фиксируется в данных.")

add("i056","ru","no_data",
    "какая ставка НДС (в процентах) применяется к сделкам по сахару белому?",
    None,
    "В данных есть только признак «Плательщик НДС» (Да/Нет), самой ставки налога нет.")

add("i057","uz","no_data",
    "UZEXнинг расмий мобиль иловаси (mobile app) борми?",
    None,
    "Информации о мобильном приложении биржи в данных нет.")

add("i058","ru","no_data",
    "сколько голов скота было продано через биржу за всё время?",
    "SELECT COUNT(*) FROM deals_enriched WHERE product_base LIKE '%скот%' OR product_base LIKE '%голов%'",
    "Живой скот в номенклатуре товаров отсутствует.")

add("i059","uz","no_data",
    "\"Rozi Tekstil\" OK kompaniyasining kredit reytingi qanday?",
    None,
    "Кредитный рейтинг компаний не входит в состав данных участников.")

add("i060","uz","no_data",
    "BK066 brokerlik kontorasi ISO sifat sertifikatiga egami?",
    None,
    "Сведения о сертификации ISO брокерских контор в данных отсутствуют.")

print(len(ITEMS))
with open('/Users/omarmdv/Documents/projects/uzexQualify/eval/bigset8.json', 'w', encoding='utf-8') as f:
    json.dump(ITEMS, f, ensure_ascii=False, indent=2)

from collections import Counter
print(Counter((x['lang'] for x in ITEMS)))
print(Counter((x['kind'] for x in ITEMS)))
