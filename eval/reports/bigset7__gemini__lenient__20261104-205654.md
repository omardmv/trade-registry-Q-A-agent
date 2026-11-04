# Failure report — bigset7__gemini__lenient__20261104-205654.jsonl

Correct: 44/60

| Stage | Count |
|---|---|
| value differs (definition, filter detail or compiler) — inspect | 8 |
| plan: wrongly judged not answerable | 4 |
| plan: missing product | 1 |
| plan: missing period | 1 |
| plan/feature: contract-card question | 1 |
| verify: refused (unknown-words) | 1 |

Latency: median 2365 ms, max 10665 ms. Gemini tokens per question: median 6143.

## h001 [exact, ru] — plan: wrongly judged not answerable

**Q:** Какой код ТН ВЭД присвоен хлопку-сырцу на бирже?

**Gold:** `[['520100']]`  
**Gold SQL:** `select distinct "Код ТН ВЭД" from contracts where "Наименование продукта" ilike '%хлопок%сырец%'`  
**Note:** 

**Route:** `gemini:not-answerable`  
**Got rows:** `[]`

**Plan:** `{"analysis": "question asks for the customs TN VED code for raw cotton. The database contains contract cards which include TN VED codes, but querying specific code lookup is not supported as a metric.", "missing": "в базе данных нет информации о кодах ТН ВЭД по конкретным товарам", "kind": "contracts", "metric": "contracts", "order": "desc", "mentioned_products": ["хлопок-сырец"], "products": ["хлопок-сырец"], "product_bases": ["хлопок-сырец (спот)", "хлопок-сырец (форвард)", "хлопок-сырец (фьючерс)", "хлопок-сырец (спот) 1-сорт 2 класс", "хлопок-сырец (спот) 1-сорта 1 класс", "хлопок-сырец (спот) 1-сорта 2 класс", "хлопок-сырец (спот) 2 сорт 2 класс", "хлопок-сырец (спот) 2-сорта 1 класс", "хлопок-сырец (спот) 3 сорт i класс", "хлопок-сырец (спот) 3 сорт ii класс", "хлопок-сырец (спот) 3-сорта 1 класс", "хлопок-сырец (спот) 3-сорта 2 класс", "хлопок-сырец (спот) i сорт 1 класс", "хлопок`

**Filters:** `[] cond=[]`  
**SQL:** ``

**Answer:**
```
В данных нет ответа на этот вопрос: в базе данных нет информации о кодах ТН ВЭД по конкретным товарам.
```

## h002 [exact, uz] — value differs (definition, filter detail or compiler) — inspect

**Q:** Portlandsement shartnomalarida garov summasi necha foiz qilib belgilangan?

**Gold:** `[['10 %']]`  
**Gold SQL:** `select distinct "Сумма залога" from contracts where "Наименование продукта" ilike '%портландцемент%'`  
**Note:** 

**Route:** `gemini:contracts:deposit`  
**Got rows:** `[[10.0, 336]]`

**Plan:** `{"analysis": "Question asks for the deposit percentage in contract cards for Portland cement. Metric is deposit, kind is contracts, product base is portlandcement.", "answerable": true, "kind": "contracts", "metric": "deposit", "order": "desc", "mentioned_products": ["Portlandsement"], "products": ["цемент"], "product_bases": ["портландцемент", "портландцемент цем i 42,5 н", "портландцемент цем ii/а-ш 32,5 н", "портландцемент цем ii/в-к 32,5 н"], "region_scope": "warehouse", "side": "any", "contract_date_mode": "registered"}`

**Filters:** `[] cond=[]`  
**SQL:** `SELECT round(avg(deposit_pct),2), count(*) FROM contracts_clean WHERE product_base IN ('портландцемент','портландцемент цем i 42,5 н','портландцемент цем ii/а-ш 32,5 н','портландцемент цем ii/в-к 32,5 н')`

**Answer:**
```
Garov summasi, % (o'rtacha): 10.00 (336 ta shartnoma).

Tushunilgan savol: Portlandsement shartnomalaridagi garov summasining foizi
Asos: карточки контрактов (contracts.csv); mahsulot: портландцемент; портландцемент цем i 42,5 н; портландцемент цем ii/а-ш 32,5 н; портландцемент цем ii/в-к 32,5 н.
```

## h009 [loose, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** сколько компаний-продавцов относится к виду деятельности 'производитель' (не производственное предприятие, а именно производитель)

**Gold:** `[[157]]`  
**Gold SQL:** `select count(*) from participants where "Вид деятельности"='Производитель' and "Роль"='Продавец'`  
**Note:** 

**Route:** `gemini:participants_directory`  
**Got rows:** `[[0]]`

**Plan:** `{"analysis": "Count distinct sellers by activity type producer in participants directory.", "answerable": true, "kind": "participants_directory", "metric": "count", "distinct_of": "company", "order": "desc", "region_scope": "warehouse", "side": "seller"}`

**Filters:** `[] cond=[]`  
**SQL:** `SELECT count(*) FROM participants WHERE "Роль"='Продавец' AND "Вид деятельности"='Производственное предприятие'`

**Answer:**
```
Компаний в справочнике участников: 0.

Понято как: Сколько компаний-продавцов имеют вид деятельности 'производитель'.
Основание: роль: продавец; вид деятельности: Производственное предприятие; справочник участников (participants.csv).
```

## h012 [exact, uz] — value differs (definition, filter detail or compiler) — inspect

**Q:** 100037-son shartnomada to'lov muddati necha kun qilib belgilangan?

**Gold:** `[['5']]`  
**Gold SQL:** `select "Срок оплаты" from contracts where "Номер контракта"='100037'`  
**Note:** 

**Route:** `gemini:contracts:pay_term`  
**Got rows:** `[[10.59, 6616]]`

**Plan:** `{"analysis": "User asks for the payment term in days for contract number 100037. This is a contract property query. Metric is pay_term.", "answerable": true, "kind": "contracts", "metric": "pay_term", "order": "desc", "limit": 1, "region_scope": "warehouse", "side": "any", "contract_date_mode": "registered"}`

**Filters:** `[] cond=[]`  
**SQL:** `SELECT round(avg(pay_term_days),2), count(*) FROM contracts_clean `

**Answer:**
```
To'lov muddati, kun (o'rtacha): 10.59 (6 616 ta shartnoma).

Tushunilgan savol: 100037-son shartnomadagi to'lov muddati (kunlarda) aniqlanmoqda.
Asos: карточки контрактов (contracts.csv).
```

## h013 [exact, ru] — plan: wrongly judged not answerable

**Q:** Какой базис поставки указан в контракте №100068?

**Gold:** `[['EXW']]`  
**Gold SQL:** `select "Базис поставки" from contracts where "Номер контракта"='100068'`  
**Note:** 

**Route:** `gemini:not-answerable`  
**Got rows:** `[]`

**Plan:** `{"analysis": "The user is asking for the delivery basis specified in contract number 100068, which is a contract-card level question.", "missing": "в данных отсутствует поиск контрактов по их номеру", "kind": "contracts", "metric": "contracts", "order": "desc", "region_scope": "warehouse", "side": "any"}`

**Filters:** `[] cond=[]`  
**SQL:** ``

**Answer:**
```
В данных нет ответа на этот вопрос: в данных отсутствует поиск контрактов по их номеру.
```

## h014 [loose, uz] — value differs (definition, filter detail or compiler) — inspect

**Q:** paxta-xomashyo shartnomalarining necha foizi FCA bazasida yetkazib berishni ko'zda tutadi?

**Gold:** `[[10.267534345625451]]`  
**Gold SQL:** `select sum(case when "Базис поставки"='FCA' then 1 else 0 end)*100.0/count(*) from contracts where "Наименование продукта" ilike '%хлопок%'`  
**Note:** 

**Route:** `gemini:contracts:contracts`  
**Got rows:** `[[142, 142]]`

**Plan:** `{"analysis": "Calculating the percentage of raw cotton contracts with FCA delivery basis, share calculation over contracts.", "answerable": true, "kind": "contracts", "metric": "contracts", "order": "desc", "share": true, "share_condition": "contract_type", "mentioned_products": ["paxta-xomashyo"], "products": ["хлопок-сырец"], "product_bases": ["хлопок сырец (спот)", "хлопок сырец (спот) 1-сорт 2 класс", "хлопок сырец (спот) 1-сорта 1 класс", "хлопок сырец (спот) 1-сорта 2 класс", "хлопок сырец (спот) 2 сорт 2 класс", "хлопок сырец (спот) 2-сорта 1 класс", "хлопок сырец (спот) 3 сорт i класс", "хлопок сырец (спот) 3 сорт ii класс", "хлопок сырец (спот) 3-сорта 1 класс", "хлопок сырец (спот) 3-сорта 2 класс", "хлопок сырец (спот) i сорт 1 класс", "хлопок сырец (спот) ii сорт i класс", "хлопок сырец (спот) ii сорт ii класс", "хлопок сырец (спот) второй сорт 1 класс", "хлопок сырец (спот) `

**Filters:** `[] cond=[]`  
**SQL:** `SELECT count(*), count(*) FROM contracts_clean WHERE product_base IN ('хлопок сырец (спот)','хлопок сырец (спот) 1-сорт 2 класс','хлопок сырец (спот) 1-сорта 1 класс','хлопок сырец (спот) 1-сорта 2 класс','хлопок сырец (спот) 2 сорт 2 класс','хлопок сырец (спот) 2-сорта 1 класс','хлопок сырец (спот) 3 сорт i класс','хлопок сырец (спот) 3 сорт ii класс','хлопок сырец (спот) 3-сорта 1 класс','хлопок сырец (спот) 3-сорта 2 класс','хлопок сырец (спот) i сорт 1 класс','хлопок сырец (спот) ii сорт i к`

**Answer:**
```
Shartnomalar: 142.

Tushunilgan savol: Paxta-xomashyo bo'yicha FCA yetkazib berish bazasiga ega shartnomalar ulushi necha foizni tashkil etishini hisoblash.
Asos: карточки контрактов (contracts.csv); mahsulot: хлопок сырец (спот); хлопок сырец (спот) 1-сорт 2 класс; хлопок сырец (спот) 1-сорта 1 класс; хлопок сырец (спот) 1-сорта 2 класс; хлопок сырец (спот) 2 сорт 2 класс; хлопок сырец (спот) 2-сорта 1 класс; delivery_basis: FCA.
```

## h017 [trap, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** сколько сделок по портландцементу аннулировано и на какую заявленную сумму (включая сами аннулированные сделки)?

**Gold:** `[[2726, 386397263758.0]]`  
**Gold SQL:** `select count(*), sum(amount_uzs) from deals_enriched where (product_base like '%портландцемент%' or product_base like '%цемент портландск%') and status='Аннулирован(Не оплачен)'`  
**Note:** Вопрос явно просит учесть аннулированные сделки, поэтому сумма берётся без обычного исключения статуса «Аннулирован(Не оплачен)».

**Route:** `gemini:count`  
**Got rows:** `[[2487], [358500707035.0, 2487], ['портландцемент', None, None, 1903], ['цемент портландский', None, None, 301]]`

**Plan:** `{"analysis": "metric count and extra_metrics amount for cancelled portland cement deals, status annulled, product portland cement", "answerable": true, "kind": "deals", "metric": "count", "extra_metrics": ["amount"], "order": "desc", "mentioned_products": ["портландцемент"], "products": ["цемент"], "product_bases": ["портландцемент", "цемент портландский", "цемент портландский цем i 42,5н", "цемент портландский цем ii/а-ш 32,5н", "цемент портландский цем ii/в-к 32,5н"], "region_scope": "warehouse", "side": "any", "status": "Аннулирован(Не оплачен)"}`

**Filters:** `["product_base IN ('портландцемент','цемент портландский','цемент портландский цем i 42,5н','цемент портландский цем ii/а-ш 32,5н','цемент портландский цем ii/в-к 32,5н')"] cond=["status='Аннулирован(Не оплачен)'"]`  
**SQL:** `SELECT count(*) FROM deals_enriched WHERE product_base IN ('портландцемент','цемент портландский','цемент портландский цем i 42,5н','цемент портландский цем ii/а-ш 32,5н','цемент портландский цем ii/в-к 32,5н') AND status='Аннулирован(Не оплачен)';
SELECT round(sum(amount_uzs)), count(*) FROM deals_enriched WHERE product_base IN ('портландцемент','цемент портландский','цемент портландский цем i 42,5н','цемент портландский цем ii/а-ш 32,5н','цемент портландский цем ii/в-к 32,5н') AND status='Анну`

**Answer:**
```
Количество сделок: 2 487 сделок.
Сумма сделок: 358 500 707 035 сум (2 487 сделок).
В том числе по наименованиям в реестре:
1. портландцемент — 1 903 сделок
2. цемент портландский — 301 сделок
3. цемент портландский цем i 42,5н — 180 сделок
4. цемент портландский цем ii/в-к 32,5н — 77 сделок
5. цемент портландский цем ii/а-ш 32,5н — 26 сделок

Понято как: Сколько сделок по портландцементу аннулировано и на какую сумму.
Основание: товар (наименования в реестре): портландцемент; цемент портландский; цемент портландский цем i 42,5н; цемент портландский цем ii/а-ш 32,5н; цемент портландский цем ii/в-к 32,5н; статус: Аннулирован(Не оплачен).
```

## h036 [exact, uz] — plan: wrongly judged not answerable

**Q:** 100001-son shartnoma bo'yicha ombor qayerda joylashganligi (manzili) qanday yozilgan?

**Gold:** `[['"Go‘qe Prokat" MChJ STIR: 953758401, Farg‘ona sh., Tinchlik ko‘chasi 115-uy']]`  
**Gold SQL:** `select "Адрес склада" from contracts where "Номер контракта"='100001'`  
**Note:** 

**Route:** `gemini:not-answerable`  
**Got rows:** `[]`

**Plan:** `{"analysis": "The user is asking for the address/location of the warehouse for contract number 100001, which refers to contract card details.", "missing": "ma'lumotlar bazasida shartnoma raqamlari bo'yicha qidiruv yoki ombor manzillari mavjud emas", "kind": "contracts", "metric": "contracts", "order": "desc", "region_scope": "warehouse", "side": "any", "contract_date_mode": "registered"}`

**Filters:** `[] cond=[]`  
**SQL:** ``

**Answer:**
```
Ma'lumotlarda bu savolga javob yo'q: ma'lumotlar bazasida shartnoma raqamlari bo'yicha qidiruv yoki ombor manzillari mavjud emas.
```

## h040 [loose, ru] — plan: wrongly judged not answerable

**Q:** курс доллара с начала 2022 по конец 2026 вырос на сколько процентов примерно?

**Gold:** `[[23.35134036992917]]`  
**Gold SQL:** `select ((select uzs_per_usd from usd_rates order by rate_date desc limit 1) - (select uzs_per_usd from usd_rates order by rate_date asc limit 1))*100.0/(select uzs_per_usd from usd_rates order by rate_date asc limit 1)`  
**Note:** 

**Route:** `gemini:not-answerable`  
**Got rows:** `[]`

**Plan:** `{"analysis": "USD exchange rate growth in percentage from early 2022 to late 2026, which requires calculating price change or rate difference over the period, but rate changes are not supported as percentage metrics.", "missing": "в системе нет расчёта процентного изменения курса валют за период", "kind": "usd_rate", "metric": "count", "order": "desc", "region_scope": "warehouse", "side": "any", "date_from": "2022-01-03", "date_to": "2026-10-30"}`

**Filters:** `[] cond=[]`  
**SQL:** ``

**Answer:**
```
В данных нет ответа на этот вопрос: в системе нет расчёта процентного изменения курса валют за период.
```

## h041 [loose, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** в среднем сколько тонн хлопка-сырца продают за одну сделку?

**Gold:** `[[59.66518136629432]]`  
**Gold SQL:** `select avg(volume_units)/1000 from deals_enriched where product_base like '%хлопок%сырец%' and status!='Аннулирован(Не оплачен)'`  
**Note:** 

**Route:** `gemini:volume`  
**Got rows:** `[['тонна', 7622047.92, 127747, 61025220620240.0], ['хлопок сырец (спот)', None, 'тонна', 2677553.06, 44709, 20842286257152.0], ['хлопок-сырец (спот)', None, 'тонна', 2624417.59, 44172, 21358234334538.0], ['хлопок сырец (форвард)', None, 'тонна', 655541.44, 10810, 5284144848037.0]]`

**Plan:** `{"analysis": "metric volume stat avg per deal, product raw cotton, tonnes true", "answerable": true, "kind": "deals", "metric": "volume", "stat": "avg", "order": "desc", "tonnes": true, "unit": "тонна", "mentioned_products": ["хлопок-сырец"], "products": ["хлопок-сырец"], "product_bases": ["хлопок сырец (спот)", "хлопок сырец (спот) 1-сорт 2 класс", "хлопок сырец (спот) 1-сорта 1 класс", "хлопок сырец (спот) 1-сорта 2 класс", "хлопок сырец (спот) 2 сорт 2 класс", "хлопок сырец (спот) 2-сорта 1 класс", "хлопок сырец (спот) 3 сорт i класс", "хлопок сырец (спот) 3 сорт ii класс", "хлопок сырец (спот) 3-сорта 1 класс", "хлопок сырец (спот) 3-сорта 2 класс", "хлопок сырец (спот) i сорт 1 класс", "хлопок сырец (спот) ii сорт i класс", "хлопок сырец (спот) ii сорт ii класс", "хлопок сырец (спот) второй сорт 1 класс", "хлопок сырец (спот) второй сорт 2 класс", "хлопок сырец (спот) первый сорт i `

**Filters:** `["product_base IN ('хлопок сырец (спот)','хлопок сырец (спот) 1-сорт 2 класс','хлопок сырец (спот) 1-сорта 1 класс','хлопок сырец (спот) 1-сорта 2 класс','хлопок сырец (спот) 2 сорт 2 класс','хлопок сырец (спот) 2-сорта 1 класс','хлопок сырец (спот) 3 сорт i класс','хлопок сырец (спот) 3 сорт ii класс','хлопок сырец (спот) 3-сорта 1 класс','хлопок сырец (спот) 3-сорта 2 класс','хлопок сырец (спот) i сорт 1 класс','хлопок сырец (спот) ii сорт i класс','хлопок сырец (спот) ii сорт ii класс','хлопок сырец (спот) второй сорт 1 класс','хлопок сырец (спот) второй сорт 2 класс','хлопок сырец (спот) первый сорт i класс','хлопок сырец (спот) первый сорт ii класс','хлопок сырец (спот) третий сорт 1 класс','хлопок сырец (форвард)','хлопок сырец (форвард) 1-сорт 1 класс','хлопок сырец (форвард) 2-сорта 2 класс','хлопок сырец (форвард) 3 сорт i класс','хлопок сырец (форвард) 3 сорт ii класс','хлопок сырец (форвард) 3-сорта 1 класс','хлопок сырец (форвард) ii сорт i класс','хлопок сырец (форвард) ii сорт ii класс','хлопок сырец (форвард) второй сорт 2 класс','хлопок сырец (форвард) первый сорт ii класс','хлопок сырец (фьючерс)','хлопок сырец (фьючерс) первый сорт i класс','хлопок сырец (фьючерс) первый сорт ii класс','хлопок сырец (фьючерс) третий сорт 1 класс','хлопок-сырец (спот)','хлопок-сырец (спот) 2 сорт 1-класс','хлопок-сырец (спот) 2 сорт 2-класс','хлопок-сырец (спот) 2 сорт i класс','хлопок-сырец (спот) 2 сорт ii класс','хлопок-сырец (спот) 3-сорта 1-класс','хлопок-сырец (спот) 3-сорта 2-класс','хлопок-сырец (спот) 3-сорта i класс','хлопок-сырец (спот) 3-сорта ii класс','хлопок-сырец (спот) i сорт 1-класс','хлопок-сырец (спот) i сорт i класс','хлопок-сырец (спот) i сорт ii класс','хлопок-сырец (форвард)','хлопок-сырец (форвард) 2 сорт 1-класс','хлопок-сырец (форвард) 2 сорт 2-класс','хлопок-сырец (форвард) 2 сорт i класс','хлопок-сырец (форвард) 3-сорта 1-класс','хлопок-сырец (форвард) 3-сорта ii класс','хлопок-сырец (форвард) i сорт 1-класс','хлопок-сырец (форвард) i сорт i класс','хлопок-сырец (форвард) i сорт ii класс','хлопок-сырец (фьючерс)','хлопок-сырец (фьючерс) 3-сорта i класс','хлопок-сырец (фьючерс) i сорт 1-класс','хлопок-сырец (фьючерс) i сорт i класс')"] cond=[]`  
**SQL:** `SELECT CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END AS u, round(sum(CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END),2), count(*), round(sum(amount_uzs)) FROM deals_enriched WHERE product_base IN ('хлопок сырец (спот)','хлопок сырец (спот) 1-сорт 2 класс','хлопок сырец (спот) 1-сорта 1 класс','хлопок сырец (спот) 1-сорта 2 класс','хлопок сырец (спот) 2 сорт 2 класс','хлопок сырец (спот) 2-сорта 1 класс','хлопок сырец (спот) 3 сорт i класс','хлопок сырец (спот) 3 `

**Answer:**
```
Объём: 7 622 048 т (127 747 сделок, 61 025 220 620 240 сум).
В том числе по наименованиям в реестре:
1. хлопок сырец (спот) — 2 677 553 т (44 709 сделок, 20 842 286 257 152 сум)
2. хлопок-сырец (спот) — 2 624 418 т (44 172 сделок, 21 358 234 334 538 сум)
3. хлопок сырец (форвард) — 655 541 т (10 810 сделок, 5 284 144 848 037 сум)
4. хлопок-сырец (форвард) — 599 617 т (10 173 сделок, 4 891 500 357 280 сум)
5. хлопок-сырец (фьючерс) — 329 569 т (5 536 сделок, 2 774 439 668 130 сум)
6. хлопок-сырец (спот) i сорт 1-класс — 138 911 т (2 341 сделок, 1 130 831 793 412 сум)
7. хлопок-сырец (спот) 2 сорт 1-класс — 66 197 т (1 073 сделок, 584 132 923 645 сум)
8. хлопок-сырец (форвард) 2 сорт 1-класс —
```

## h043 [loose, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** средний срок поставки по контрактам на портландцемент сколько дней получается

**Gold:** `[[17.81336405529954]]`  
**Gold SQL:** `select avg(cast("Срок поставки" as int)) from contracts where "Наименование продукта" ilike '%портландцемент%' or "Наименование продукта" ilike '%цемент портландск%'`  
**Note:** 

**Route:** `gemini:contracts:delivery_term`  
**Got rows:** `[[17.95, 409]]`

**Plan:** `{"analysis": "Calculating the average delivery term in days for contract cards of cement (portland cement).", "answerable": true, "kind": "contracts", "metric": "delivery_term", "stat": "avg", "order": "desc", "mentioned_products": ["портландцемент"], "products": ["цемент"], "product_bases": ["портландцемент", "цемент портландский", "цемент портландский цем i 42,5н", "цемент портландский цем ii/а-ш 32,5н", "цемент портландский цем ii/в-к 32,5н"], "region_scope": "warehouse", "side": "any", "contract_date_mode": "registered"}`

**Filters:** `[] cond=[]`  
**SQL:** `SELECT round(avg(delivery_term_days),2), count(*) FROM contracts_clean WHERE product_base IN ('портландцемент','цемент портландский','цемент портландский цем i 42,5н','цемент портландский цем ii/а-ш 32,5н','цемент портландский цем ii/в-к 32,5н')`

**Answer:**
```
Срок поставки, дней (среднее): 17.95 (409 контрактов).

Понято как: Средний срок поставки по контрактам на портландцемент в днях
Основание: карточки контрактов (contracts.csv); товар: портландцемент; цемент портландский; цемент портландский цем i 42,5н; цемент портландский цем ii/а-ш 32,5н; цемент портландский цем ii/в-к 32,5н.
```

## h044 [loose, ru] — value differs (definition, filter detail or compiler) — inspect

**Q:** какая доля сделок находится в статусе ждёт оплату от всех сделок в базе

**Gold:** `[[0.3428]]`  
**Gold SQL:** `select sum(case when status='Ждёт оплату' then 1 else 0 end)*100.0/count(*) from deals`  
**Note:** 

**Route:** `gemini:count/share`  
**Got rows:** `[[0.34, 1714, 500000]]`

**Plan:** `{"analysis": "Calculate the percentage (share) of deals with the status 'Ждёт оплату' relative to all deals in the database, without any period or product filters.", "answerable": true, "kind": "deals", "metric": "count", "order": "desc", "share": true, "share_condition": "status", "region_scope": "warehouse", "side": "any", "status": "Ждёт оплату"}`

**Filters:** `[] cond=["status='Ждёт оплату'"]`  
**SQL:** `SELECT round(100.0 * count(*) FILTER (WHERE status='Ждёт оплату') / NULLIF(count(*), 0), 2), count(*) FILTER (WHERE status='Ждёт оплату'), count(*) FROM deals_enriched `

**Answer:**
```
Доля: 0.34% (1 714 из 500 000).

Понято как: Какова доля сделок в статусе 'Ждёт оплату' от общего числа сделок?
Основание: статус: Ждёт оплату.
```

## h050 [loose, uz] — plan: missing product

**Q:** Бензин билан дизель қайсиси кўпроқ сотилган - тонна ҳисобида?

**Gold:** `[[332740.0, 371778.0]]`  
**Gold SQL:** `select (select sum(volume_units) from deals_enriched where product_base like '%бензин%' and unit='тонна' and status!='Аннулирован(Не оплачен)'), (select sum(volume_units) from deals_enriched where product_base like '%дизель%' and unit='тонна' and status!='Аннулирован(Не оплачен)')`  
**Note:** 

**Route:** `gemini:volume/product/cmp-product`  
**Got rows:** `[['бензин', None, 'тонна', 332740.0, 12142, 4213326453086.0], ['бензин', None, 'литр', 440718000.0, 11591, 4497786362000.0], ['дизельное топливо', None, 'тонна', 799989.34, 26631, 9933345633277.0]]`

**Plan:** `{"analysis": "Compare total volume in tonnes between gasoline (бензин) and diesel (дизель), product comparison, metric volume.", "answerable": true, "kind": "deals", "metric": "volume", "group_by": "product", "order": "desc", "limit": 1, "compare": "product", "tonnes": true, "unit": "тонна", "mentioned_products": ["бензин", "дизель"], "products": ["бензин", "дизельное топливо"], "product_bases": ["бензин", "бензин автомобильный", "бензин автомобильный аи-80", "бензин автомобильный аи-92", "бензин автомобильный аи-95", "дизельное топливо", "топливо дизельное"], "region_scope": "warehouse", "side": "any"}`

**Filters:** `[] cond=[]`  
**SQL:** `SELECT 'бензин' AS g, NULL AS k, CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END AS u, round(sum(CASE WHEN unit='килограмм' THEN volume_units/1000 ELSE volume_units END),2), count(*), round(sum(amount_uzs)) FROM deals_enriched WHERE status<>'Аннулирован(Не оплачен)' AND product_base SIMILAR TO 'бензин.*' GROUP BY CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END UNION ALL SELECT 'дизельное топливо' AS g, NULL AS k, CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END AS u, round(sum(C`

**Answer:**
```
Hajmi — taqqoslash:
1. бензин — 332 740 t (12 142 ta bitim, 4 213 326 453 086 so'm)
2. бензин — 440 718 000 l (11 591 ta bitim, 4 497 786 362 000 so'm)
3. дизельное топливо — 799 989 t (26 631 ta bitim, 9 933 345 633 277 so'm)

Tushunilgan savol: Benzin va dizel yoqilg'isining qays biri tonna hisobida ko'proq sotilganligini solishtirish.
Asos: bekor qilingan (amalga oshmagan) bitimlarsiz; hajm = lotlar soni × lot hajmi; kilogrammlar tonnaga o'tkazildi.
```

## h054 [trap, ru] — plan: missing period

**Q:** Когда каустическая сода продавалась дороже всего и по какой цене за тонну?

**Gold:** `[['2026-08-06', 13294675.0]]`  
**Gold SQL:** `select deal_date, price_per_unit from deals_enriched where product_base like '%сода%' and status!='Аннулирован(Не оплачен)' order by price_per_unit desc limit 1`  
**Note:** 

**Route:** `gemini:price_stat/month`  
**Got rows:** `[['2026-08', 8, None, 13294675.0, 43]]`

**Plan:** `{"analysis": "Find the date and maximum unit price for caustic soda, filtering by product caustic soda, metric price_stat with max stat, grouping by date or finding the deal, ordered desc, limit 1.", "answerable": true, "kind": "deals", "metric": "price_stat", "stat": "max", "group_by": "month", "order": "desc", "limit": 1, "tonnes": true, "unit": "тонна", "mentioned_products": ["каустическая сода"], "products": ["сода каустическая"], "product_bases": ["сода каустическая"], "region_scope": "warehouse", "side": "any"}`

**Filters:** `["product_base IN ('сода каустическая')"] cond=[]`  
**SQL:** `SELECT strftime(deal_date, '%Y-%m') AS g, month(deal_date) AS k, NULL AS u, round(max(price_per_unit),2), count(*) FROM deals_enriched d  WHERE product_base IN ('сода каустическая') AND status<>'Аннулирован(Не оплачен)' AND unit='тонна' AND currency='Узбекский сум' GROUP BY 1, 2, 3 ORDER BY 4 DESC NULLS LAST LIMIT 1`

**Answer:**
```
Цена (максимум):
1. 2026-08 — 13 294 675.00 сум за 1  (43 сделок)

Понято как: Когда каустическая сода продавалась дороже всего и по какой цене за тонну?
Основание: товар (наименования в реестре): сода каустическая; без аннулированных (не состоявшихся) сделок; единица: тонна; цена за единицу = сумма ÷ (лоты × размер лота).
```

## h058 [trap, uz] — plan/feature: contract-card question

**Q:** Portlandsement EXW va CPT bazislarida qaysi birida narx qimmatroq?

**Gold:** `[['CPT', 936243.9217638691], ['EXW', 878943.7269667371]]`  
**Gold SQL:** `select c."Базис поставки", avg(d.price_per_unit) from deals_enriched d join contracts c on d.contract_id = cast(c."Номер контракта" as bigint) where (d.product_base like '%портландцемент%' or d.product_base like '%цемент портландск%') and c."Базис поставки" in ('EXW','CPT') group by 1 order by 2 desc`  
**Note:** CPT дороже EXW, так как в стоимость CPT включена доставка до пункта назначения продавцом.

**Route:** `gemini:avg_price/delivery_basis`  
**Got rows:** `[['CPT', None, 'тонна', 936832.81, 1308, 938380.7]]`

**Plan:** `{"analysis": "Compare average price of portland cement between EXW and CPT delivery bases, metric avg_price, group by delivery basis to find which is higher", "answerable": true, "kind": "deals", "metric": "avg_price", "stat": "avg", "group_by": "delivery_basis", "order": "desc", "limit": 1, "mentioned_products": ["portlandsement"], "products": ["цемент"], "product_bases": ["портландцемент", "портландцемент цем i 42,5 н", "портландцемент цем ii/а-ш 32,5 н", "портландцемент цем ii/в-к 32,5 н", "цемент портландский", "цемент портландский цем i 42,5н", "цемент портландский цем ii/а-ш 32,5н", "цемент портландский цем ii/в-к 32,5н"], "region_scope": "warehouse", "side": "any"}`

**Filters:** `["product_base IN ('портландцемент','портландцемент цем i 42,5 н','портландцемент цем ii/а-ш 32,5 н','портландцемент цем ii/в-к 32,5 н','цемент портландский','цемент портландский цем i 42,5н','цемент портландский цем ii/а-ш 32,5н','цемент портландский цем ii/в-к 32,5н')"] cond=[]`  
**SQL:** `SELECT delivery_basis AS g, NULL AS k, unit AS u, round(sum(amount)/sum(volume_units),2), count(*), round(avg(price_per_unit),2) FROM deals_enriched d  WHERE product_base IN ('портландцемент','портландцемент цем i 42,5 н','портландцемент цем ii/а-ш 32,5 н','портландцемент цем ii/в-к 32,5 н','цемент портландский','цемент портландский цем i 42,5н','цемент портландский цем ii/а-ш 32,5н','цемент портландский цем ii/в-к 32,5н') AND status<>'Аннулирован(Не оплачен)' AND currency='Узбекский сум' GROUP `

**Answer:**
```
O'rtacha narx:
1. CPT — 936 832.81 so'm 1 t (1 308 ta bitim, 938 380.70 so'm 1 t)

Tushunilgan savol: Portlendsement bo'yicha EXW va CPT yetkazib berish bazislarining o'rtacha narxini taqqoslash.
Asos: товар (наименования в реестре): портландцемент; портландцемент цем i 42,5 н; портландцемент цем ii/а-ш 32,5 н; портландцемент цем ii/в-к 32,5 н; цемент портландский; цемент портландский цем i 42,5н; цемент портландский цем ii/а-ш 32,5н; цемент портландский цем ii/в-к 32,5н; bekor qilingan (amalga oshmagan) bitimlarsiz; o'rtacha tortilgan narx = summa ÷ hajm (shartnoma valyutasida); третья колонка — простое среднее цен.
```

## h060 [trap, uz] — verify: refused (unknown-words)

**Q:** Подшипник бўйича битимларнинг неча фоизи Самарқанд вилоятига тўғри келади?

**Gold:** `[[76.24541164132145]]`  
**Gold SQL:** `select count(*)*100.0/(select count(*) from deals_enriched where product_base like '%подшипник%') from deals_enriched where product_base like '%подшипник%' and region='Samarqand viloyati'`  
**Note:** 

**Route:** `guard:unknown-words`  
**Got rows:** `[]`

**Plan:** `{"analysis": "Calculate the percentage share of deals for bearings in Samarkand region relative to all deals for bearings, metric count with share.", "answerable": true, "kind": "deals", "metric": "count", "group_by": "region", "order": "desc", "share": true, "mentioned_products": ["Подшипник"], "mentioned_places": ["Самарқанд"], "products": ["подшипник"], "product_bases": ["подшипник"], "regions": ["Samarqand viloyati"], "region_scope": "warehouse", "side": "any"}`

**Filters:** `["product_base IN ('подшипник')", "region IN ('Samarqand viloyati')"] cond=[]`  
**SQL:** ``

**Answer:**
```
Ma'lumotlarda bu savolga javob yo'q: ma'lumotlarda quyidagilar yo'q: «тўғри», «келади». Ma'lumotlarda faqat bitimlar (sana, mahsulot, miqdor, summa, narx, ombor hududi, holat, shartnoma turi va shakli, to'lov va yetkazish sanalari), ishtirokchilar (STIR, nomi, hududi, faoliyat turi, roli, QQS), brokerlar va dollar kursi bor.
```
