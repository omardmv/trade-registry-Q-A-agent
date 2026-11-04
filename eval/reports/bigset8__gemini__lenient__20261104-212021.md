# Failure report — bigset8__gemini__lenient__20261104-212021.jsonl

Correct: 51/60

| Stage | Count |
|---|---|
| value differs (definition, filter detail or compiler) — inspect | 8 |
| plan: missing grouping/ranking | 1 |

Latency: median 2428 ms, max 4460 ms. Gemini tokens per question: median 6677.

## i005 [exact, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** По контракту №100007 (кабель силовой) — сколько процентов составляет сумма залога и за сколько дней покупатель должен оплатить сделку?

**Gold:** `[['10 %', '10']]`  
**Gold SQL:** `SELECT "Сумма залога", "Срок оплаты" FROM contracts WHERE "Номер контракта"='100007'`  
**Note:** Многочастный вопрос — оба значения в одной строке карточки контракта.

**Route:** `gemini:contracts:count`  
**Got rows:** `[[100007, 10.0, 10]]`

**Plan:** `{"analysis": "contract lookup for specific contract ID 100007, asking for deposit percentage and payment term in days", "answerable": true, "kind": "contract_lookup", "metric": "count", "order": "desc", "contract_ids": ["100007"], "lookup_fields": ["deposit_pct", "pay_term_days"], "mentioned_products": [{"as_written": "кабель силовой", "registry_term": "кабель силовой"}], "product_bases": ["кабель силовой"], "region_scope": "warehouse", "side": "any"}`

**Filters:** `[] cond=[]`  
**SQL:** `SELECT c.contract_id, c.deposit_pct, c.pay_term_days FROM contracts_clean c LEFT JOIN participants ps ON ps."ИНН"=c.seller_inn WHERE c.contract_id IN (100007) ORDER BY 1`

**Answer:**
```
Контракт №100007: Сумма залога, %: 10.00; Срок оплаты, дней: 10

Понято как: Для контракта №100007 указать сумму залога в процентах и срок оплаты в днях.
Основание: карточки контрактов (contracts.csv).
```

## i006 [exact, uz] — value differs (definition, filter detail or compiler) — inspect

**Q:** 100008-сон шартнома қачон рўйхатдан ўтган ва у қайси санагача амал қилади?

**Gold:** `[['03.01.2022', '24.05.2022']]`  
**Gold SQL:** `SELECT "Дата регистрации", "Действует до" FROM contracts WHERE "Номер контракта"='100008'`  
**Note:** Контракт 100008 — портландцемент ЦЕМ I 42,5 Н.

**Route:** `gemini:contracts:count`  
**Got rows:** `[[100008, '2022-01-03', '2022-05-24']]`

**Plan:** `{"analysis": "contract lookup for registration date and validity date of contract 100008", "answerable": true, "kind": "contract_lookup", "metric": "count", "order": "desc", "contract_ids": ["100008"], "lookup_fields": ["reg_date", "valid_to"], "region_scope": "warehouse", "side": "any"}`

**Filters:** `[] cond=[]`  
**SQL:** `SELECT c.contract_id, c.reg_date, c.valid_to FROM contracts_clean c LEFT JOIN participants ps ON ps."ИНН"=c.seller_inn WHERE c.contract_id IN (100008) ORDER BY 1`

**Answer:**
```
Контракт №100008: Дата регистрации: 2022-01-03; Действует до: 2022-05-24

Tushunilgan savol: 100008-сон шартноманинг рўйхатдан ўтган санаси ва ама қилиш муддати
Asos: карточки контрактов (contracts.csv).
```

## i013 [loose, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** скок всего штук кирпича керамического продали за все время, не считая аннулированные сделки

**Gold:** `[[116999000.0]]`  
**Gold SQL:** `SELECT SUM(volume_units) FROM deals_enriched WHERE product_base LIKE '%кирпич%' AND status != 'Аннулирован(Не оплачен)'`  
**Note:** Кирпич измеряется в штуках, не в тоннах.

**Route:** `gemini:volume`  
**Got rows:** `[[105041000.0, 4063, 95931043000.0], ['кирпич керамический', None, None, 85981000.0, 3326, 78846590000.0], ['кирпич керамический м-125', None, None, 11556000.0, 439, 11101540000.0], ['кирпич керамический м-100', None, None, 5590000.0, 212, 4106655000.0]]`

**Plan:** `{"analysis": "metric volume, product ceramic brick, status not_annulled, unit штука, all time", "answerable": true, "kind": "deals", "metric": "volume", "order": "desc", "unit": "штука", "mentioned_products": [{"as_written": "кирпича керамического", "registry_term": "кирпич керамический"}], "product_bases": ["кирпич керамический"], "region_scope": "warehouse", "side": "any", "status": "not_annulled"}`

**Filters:** `["product_base IN ('кирпич керамический','кирпич керамический м-100','кирпич керамический м-125','кирпич керамический м-150')"] cond=["status<>'Аннулирован(Не оплачен)'"]`  
**SQL:** `SELECT round(sum(volume_units),2), count(*), round(sum(amount_uzs)) FROM deals_enriched WHERE product_base IN ('кирпич керамический','кирпич керамический м-100','кирпич керамический м-125','кирпич керамический м-150') AND unit='штука' AND status<>'Аннулирован(Не оплачен)';
SELECT product_base AS g, NULL AS k, NULL AS u, round(sum(volume_units),2), count(*), round(sum(amount_uzs)) FROM deals_enriched d  WHERE product_base IN ('кирпич керамический','кирпич керамический м-100','кирпич керамический `

**Answer:**
```
Объём: 105 041 000 шт. (4 063 сделок, 95 931 043 000 сум).
В том числе по наименованиям в реестре:
1. кирпич керамический — 85 981 000  (3 326 сделок, 78 846 590 000 сум)
2. кирпич керамический м-125 — 11 556 000  (439 сделок, 11 101 540 000 сум)
3. кирпич керамический м-100 — 5 590 000  (212 сделок, 4 106 655 000 сум)
4. кирпич керамический м-150 — 1 914 000  (86 сделок, 1 876 258 000 сум)

Понято как: Сколько всего штук кирпича керамического продали за все время, кроме аннулированных сделок
Основание: товар (наименования в реестре): кирпич керамический; кирпич керамический м-100; кирпич керамический м-125; кирпич керамический м-150; без аннулированных (не состоявшихся) сделок (состояние на
```

## i014 [loose, uz] — value differs (definition, filter detail or compiler) — inspect

**Q:** izvest (известь) butun davr davomida jami necha tonna sotilgan, bekor qilinganlarsiz?

**Gold:** `[[70160.0]]`  
**Gold SQL:** `SELECT SUM(volume_units) FROM deals_enriched WHERE product_base LIKE '%известь%' AND status != 'Аннулирован(Не оплачен)'`  
**Note:** Известь негашеная + строительная, тонны.

**Route:** `gemini:volume`  
**Got rows:** `[['тонна', 11690.0, 294, 9551474502.0]]`

**Plan:** `{"analysis": "Calculate total volume in tonnes for lime (известь) over all time, excluding cancelled deals (status not_annulled).", "answerable": true, "kind": "deals", "metric": "volume", "order": "desc", "tonnes": true, "unit": "тонна", "mentioned_products": [{"as_written": "izvest", "registry_term": "известь строительная"}], "product_bases": ["известь негашеная", "известь строительная"], "region_scope": "warehouse", "side": "any", "status": "not_annulled"}`

**Filters:** `["product_base IN ('известь строительная')"] cond=["status<>'Аннулирован(Не оплачен)'"]`  
**SQL:** `SELECT CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END AS u, round(sum(CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END),2), count(*), round(sum(amount_uzs)) FROM deals_enriched WHERE product_base IN ('известь строительная') AND status<>'Аннулирован(Не оплачен)' GROUP BY 1 ORDER BY 3 DESC`

**Answer:**
```
Hajmi: 11 690 t (294 ta bitim, 9 551 474 502 so'm).

Tushunilgan savol: Barcha davr davomida bekor qilinganlarni hisobga olmaganda jami qancha tonna izvest sotilgan?
Asos: товар (наименования в реестре): известь строительная; bekor qilingan (amalga oshmagan) bitimlarsiz (состояние на конец дня 30.10.2026); hajm = lotlar soni × lot hajmi; kilogrammlar tonnaga o'tkazildi.
```

## i022 [loose, uz] — plan: missing grouping/ranking

**Q:** qaysi o'lchov birligida (unit) eng kam bitim qilingan va nechta bitim bo'lgan?

**Gold:** `[['метр', 3863]]`  
**Gold SQL:** `SELECT unit, COUNT(*) c FROM deals GROUP BY 1 ORDER BY c ASC LIMIT 1`  
**Note:** Единица измерения с наименьшим числом сделок — 'метр'.

**Route:** `gemini:count`  
**Got rows:** `[[500000]]`

**Plan:** `{"analysis": "Finding the unit of measurement with the minimum number of deals and the count of those deals.", "answerable": true, "kind": "deals", "metric": "count", "order": "asc", "limit": 1, "region_scope": "warehouse", "side": "any"}`

**Filters:** `[] cond=[]`  
**SQL:** `SELECT count(*) FROM deals_enriched `

**Answer:**
```
Bitimlar soni: 500 000 ta bitim.

Tushunilgan savol: Eng kam bitim qilingan o'lchov birligi va bitimlar soni.
```

## i025 [loose, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** сколько разных брокеров выступали брокером продавца в сделках по хлопку-сырцу (все формы)?

**Gold:** `[[75]]`  
**Gold SQL:** `SELECT COUNT(DISTINCT seller_broker) FROM deals_enriched WHERE product_base LIKE '%хлопок%сырец%'`  
**Note:** distinct seller_broker по всем формам хлопка-сырца.

**Route:** `gemini:distinct`  
**Got rows:** `[[120]]`

**Plan:** `{"analysis": "count distinct seller brokers in deals for raw cotton across all forms", "answerable": true, "kind": "deals", "metric": "distinct", "distinct_of": "broker", "order": "desc", "mentioned_products": [{"as_written": "хлопку-сырцу", "registry_term": "хлопок-сырец"}], "product_bases": ["хлопок сырец (спот)", "хлопок сырец (спот) 1-сорт 2 класс", "хлопок сырец (спот) 1-сорта 1 класс", "хлопок сырец (спот) 1-сорта 2 класс", "хлопок сырец (спот) 2 сорт 2 класс", "хлопок сырец (спот) 2-сорта 1 класс", "хлопок сырец (спот) 3 сорт i класс", "хлопок сырец (спот) 3 сорт ii класс", "хлопок сырец (спот) 3-сорта 1 класс", "хлопок сырец (спот) 3-сорта 2 класс", "хлопок сырец (спот) i сорт 1 класс", "хлопок сырец (спот) ii сорт i класс", "хлопок сырец (спот) ii сорт ii класс", "хлопок сырец (спот) второй сорт 1 класс", "хлопок сырец (спот) второй сорт 2 класс", "хлопок сырец (спот) первый сор`

**Filters:** `["product_base IN ('хлопок сырец (спот)','хлопок сырец (спот) 1-сорт 2 класс','хлопок сырец (спот) 1-сорта 1 класс','хлопок сырец (спот) 1-сорта 2 класс','хлопок сырец (спот) 2 сорт 2 класс','хлопок сырец (спот) 2-сорта 1 класс','хлопок сырец (спот) 3 сорт i класс','хлопок сырец (спот) 3 сорт ii класс','хлопок сырец (спот) 3-сорта 1 класс','хлопок сырец (спот) 3-сорта 2 класс','хлопок сырец (спот) i сорт 1 класс','хлопок сырец (спот) ii сорт i класс','хлопок сырец (спот) ii сорт ii класс','хлопок сырец (спот) второй сорт 1 класс','хлопок сырец (спот) второй сорт 2 класс','хлопок сырец (спот) первый сорт i класс','хлопок сырец (спот) первый сорт ii класс','хлопок сырец (спот) третий сорт 1 класс','хлопок сырец (форвард)','хлопок сырец (форвард) 1-сорт 1 класс','хлопок сырец (форвард) 2-сорта 2 класс','хлопок сырец (форвард) 3 сорт i класс','хлопок сырец (форвард) 3 сорт ii класс','хлопок сырец (форвард) 3-сорта 1 класс','хлопок сырец (форвард) ii сорт i класс','хлопок сырец (форвард) ii сорт ii класс','хлопок сырец (форвард) второй сорт 2 класс','хлопок сырец (форвард) первый сорт ii класс','хлопок сырец (фьючерс)','хлопок сырец (фьючерс) первый сорт i класс','хлопок сырец (фьючерс) первый сорт ii класс','хлопок сырец (фьючерс) третий сорт 1 класс','хлопок-сырец (спот)','хлопок-сырец (спот) 2 сорт 1-класс','хлопок-сырец (спот) 2 сорт 2-класс','хлопок-сырец (спот) 2 сорт i класс','хлопок-сырец (спот) 2 сорт ii класс','хлопок-сырец (спот) 3-сорта 1-класс','хлопок-сырец (спот) 3-сорта 2-класс','хлопок-сырец (спот) 3-сорта i класс','хлопок-сырец (спот) 3-сорта ii класс','хлопок-сырец (спот) i сорт 1-класс','хлопок-сырец (спот) i сорт i класс','хлопок-сырец (спот) i сорт ii класс','хлопок-сырец (форвард)','хлопок-сырец (форвард) 2 сорт 1-класс','хлопок-сырец (форвард) 2 сорт 2-класс','хлопок-сырец (форвард) 2 сорт i класс','хлопок-сырец (форвард) 3-сорта 1-класс','хлопок-сырец (форвард) 3-сорта ii класс','хлопок-сырец (форвард) i сорт 1-класс','хлопок-сырец (форвард) i сорт i класс','хлопок-сырец (форвард) i сорт ii класс','хлопок-сырец (фьючерс)','хлопок-сырец (фьючерс) 3-сорта i класс','хлопок-сырец (фьючерс) i сорт 1-класс','хлопок-сырец (фьючерс) i сорт i класс')"] cond=[]`  
**SQL:** `SELECT count(*) FROM (SELECT seller_broker FROM deals_enriched WHERE product_base IN ('хлопок сырец (спот)','хлопок сырец (спот) 1-сорт 2 класс','хлопок сырец (спот) 1-сорта 1 класс','хлопок сырец (спот) 1-сорта 2 класс','хлопок сырец (спот) 2 сорт 2 класс','хлопок сырец (спот) 2-сорта 1 класс','хлопок сырец (спот) 3 сорт i класс','хлопок сырец (спот) 3 сорт ii класс','хлопок сырец (спот) 3-сорта 1 класс','хлопок сырец (спот) 3-сорта 2 класс','хлопок сырец (спот) i сорт 1 класс','хлопок сырец (с`

**Answer:**
```
Уникальных брокеров: 120.

Понято как: Сколько разных брокеров выступали брокером продавца в сделках по хлопку-сырцу (все формы)
Основание: товар (наименования в реестре): хлопок сырец (спот); хлопок сырец (спот) 1-сорт 2 класс; хлопок сырец (спот) 1-сорта 1 класс; хлопок сырец (спот) 1-сорта 2 класс; хлопок сырец (спот) 2 сорт 2 класс; хлопок сырец (спот) 2-сорта 1 класс; хлопок сырец (спот) 3 сорт i класс; хлопок сырец (спот) 3 сорт ii класс и ещё 49; считаются различные коды брокеров обеих сторон.
```

## i037 [trap, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** сколько всего составила сумма сделок (в сумах) по портландцементу за весь период, если считать вместе с аннулированными сделками?

**Gold:** `[[4333213190022.0]]`  
**Gold SQL:** `SELECT SUM(amount_uzs) FROM deals_enriched WHERE product_base LIKE '%портландцемент%'`  
**Note:** Ловушка: явно указано включить аннулированные — это меняет стандартное правило исключения.

**Route:** `gemini:amount`  
**Got rows:** `[[4031227894311.0, 28446], ['портландцемент', None, None, 3582864201044.0, 25155], ['портландцемент цем i 42,5 н', None, None, 412096817268.0, 3054], ['портландцемент цем ii/в-к 32,5 н', None, None, 34823943127.0, 231]]`

**Plan:** `{"analysis": "Calculate total amount of all deals for portlant cement across the entire period, including cancelled deals.", "answerable": true, "kind": "deals", "metric": "amount", "order": "desc", "mentioned_products": [{"as_written": "портландцементу", "registry_term": "портландцемент"}], "product_bases": ["портландцемент", "цемент портландский", "цемент портландский цем i 42,5н", "цемент портландский цем ii/а-ш 32,5н", "цемент портландский цем ii/в-к 32,5н", "портландцемент цем i 42,5 н", "портландцемент цем ii/а-ш 32,5 н", "портландцемент цем ii/в-к 32,5 н"], "region_scope": "warehouse", "side": "any"}`

**Filters:** `["product_base IN ('портландцемент','портландцемент цем i 42,5 н','портландцемент цем ii/а-ш 32,5 н','портландцемент цем ii/в-к 32,5 н')"] cond=[]`  
**SQL:** `SELECT round(sum(amount_uzs)), count(*) FROM deals_enriched WHERE product_base IN ('портландцемент','портландцемент цем i 42,5 н','портландцемент цем ii/а-ш 32,5 н','портландцемент цем ii/в-к 32,5 н') AND status<>'Аннулирован(Не оплачен)';
SELECT product_base AS g, NULL AS k, NULL AS u, round(sum(amount_uzs)), count(*) FROM deals_enriched d  WHERE product_base IN ('портландцемент','портландцемент цем i 42,5 н','портландцемент цем ii/а-ш 32,5 н','портландцемент цем ii/в-к 32,5 н') AND status<>'Ан`

**Answer:**
```
Сумма сделок: 4 031 227 894 311 сум (28 446 сделок).
В том числе по наименованиям в реестре:
1. портландцемент — 3 582 864 201 044 сум (25 155 сделок)
2. портландцемент цем i 42,5 н — 412 096 817 268 сум (3 054 сделок)
3. портландцемент цем ii/в-к 32,5 н — 34 823 943 127 сум (231 сделок)
4. портландцемент цем ii/а-ш 32,5 н — 1 442 932 872 сум (6 сделок)

Понято как: Какова общая сумма сделок по портландцементу за весь период, включая аннулированные сделки?
Основание: товар (наименования в реестре): портландцемент; портландцемент цем i 42,5 н; портландцемент цем ii/а-ш 32,5 н; портландцемент цем ii/в-к 32,5 н; без аннулированных (не состоявшихся) сделок; сумма в сумах; долларовые (экспортные)
```

## i038 [trap, uz] — value differs (definition, filter detail or compiler) — inspect

**Q:** 2024-yilda armatura po'lat bo'yicha jami necha tonna sotilgan — bekor qilingan bitimlarni ham qo'shib hisoblang?

**Gold:** `[[91960.0]]`  
**Gold SQL:** `SELECT SUM(volume_units) FROM deals_enriched WHERE product_base LIKE '%арматура%' AND extract(year from deal_date)=2024`  
**Note:** Ловушка: явно включить аннулированные сделки в объём.

**Route:** `gemini:volume`  
**Got rows:** `[['тонна', 19165.0, 625, 201927686999.0]]`

**Plan:** `{"analysis": "metric volume, product reinforcement steel (арматура стальная), year 2024, including cancelled deals (so status is none to include all)", "answerable": true, "kind": "deals", "metric": "volume", "order": "desc", "tonnes": true, "unit": "тонна", "mentioned_products": [{"as_written": "armatura po'lat", "registry_term": "арматура стальная"}], "product_bases": ["арматура стальная"], "region_scope": "warehouse", "side": "any", "date_from": "2024-01-01", "date_to": "2024-12-31"}`

**Filters:** `["deal_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'", "product_base IN ('арматура стальная')"] cond=[]`  
**SQL:** `SELECT CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END AS u, round(sum(CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END),2), count(*), round(sum(amount_uzs)) FROM deals_enriched WHERE deal_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31' AND product_base IN ('арматура стальная') AND status<>'Аннулирован(Не оплачен)' GROUP BY 1 ORDER BY 3 DESC`

**Answer:**
```
Hajmi: 19 165 t (625 ta bitim, 201 927 686 999 so'm).

Tushunilgan savol: 2024-yilda armatura po'lat bo'yicha jami necha tonna sotilgan (bekor qilingan bitimlar bilan birga)
Asos: davr 01.01.2024–31.12.2024; товар (наименования в реестре): арматура стальная; bekor qilingan (amalga oshmagan) bitimlarsiz; hajm = lotlar soni × lot hajmi; kilogrammlar tonnaga o'tkazildi.
```

## i044 [trap, uz] — value differs (definition, filter detail or compiler) — inspect

**Q:** oq shakar (сахар белый) bitimlarining necha foizi hali «Ждёт оплату» yoki «Оплачен» holatida — ya'ni tovar hali yetkazib berilmagan?

**Gold:** `[[1.11]]`  
**Gold SQL:** `SELECT ROUND(100.0*SUM(CASE WHEN status IN ('Ждёт оплату','Оплачен') THEN 1 ELSE 0 END)/COUNT(*),2) FROM deals_enriched WHERE product_base='сахар белый'`  
**Note:** Ловушка: нужно объединить именно два статуса, не один; знаменатель — все сделки по товару.

**Route:** `gemini:count/share`  
**Got rows:** `[[0.1435, 17, 11845]]`

**Plan:** `{"analysis": "Calculate the percentage of deals for white sugar that are in 'Ждёт оплату' or 'Оплачен' status, meaning undelivered.", "answerable": true, "kind": "deals", "metric": "count", "order": "desc", "share": true, "share_condition": "status", "mentioned_products": [{"as_written": "oq shakar", "registry_term": "сахар белый"}], "product_bases": ["сахар белый"], "region_scope": "warehouse", "side": "any", "status": "Ждёт оплату"}`

**Filters:** `["product_base IN ('сахар белый')"] cond=["status='Ждёт оплату'"]`  
**SQL:** `SELECT round(100.0 * count(*) FILTER (WHERE status='Ждёт оплату') / NULLIF(count(*), 0), 4), count(*) FILTER (WHERE status='Ждёт оплату'), count(*) FROM deals_enriched WHERE product_base IN ('сахар белый')`

**Answer:**
```
Ulushi: 0.1435% (11 845 dan 17).

Tushunilgan savol: Oq shakar bitimlarining necha foizi 'Ждёт оплату' yoki 'Оплачен' holatida ekanligini hisoblash.
Asos: товар (наименования в реестре): сахар белый; статус: Ждёт оплату (состояние на конец дня 30.10.2026).
```
