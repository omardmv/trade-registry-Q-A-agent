# Exchange deals registry — fictional data (English translation)

_Translation of `data/extracted/uzex_spot_data/README.md` (Russian + Uzbek original). Column names are kept in Russian as they appear in the CSV files._

All data is fictional: companies, tax IDs (INN/STIR), brokers, contracts, deals, prices and the dollar exchange rate are invented.
The format repeats the public deals registry of a commodity exchange. Any resemblance to real organisations is coincidental.

## Files

| File | Contents |
|---|---|
| deals_2022.csv … deals_2026.csv | deals by year: 500,000 deals from 3 January 2022 to 30 October 2026 (2022: 84,299 · 2023: 93,985 · 2024: 106,647 · 2025: 115,763 · 2026: 99,306) |
| contracts.csv | contract cards (6,616): the product and its attributes, unit and lot, currency, base price, payment and delivery terms, seller |
| participants.csv | companies — sellers and buyers (14,392) |
| brokers.csv | brokerage firms (120) |
| usd_rates.csv | the dollar exchange rate for every calendar day, UZS per 1 USD |

All files are CSV in UTF-8, comma-separated, the first row holds column names. Values containing a comma are quoted.
Dates are DD.MM.YYYY, times are Tashkent time. Deal statuses are as of the end of the day on 30.10.2026.

## Deals — deals_*.csv

| Column | Meaning |
|---|---|
| Номер сделки (deal number) | a running number across all trading types; grows with deal time; **some numbers are skipped** |
| Дата сделки, Время сделки (deal date, deal time) | when the deal was concluded |
| Тип торгов (auction type) | English auction — price rises from the start price; Reverse auction — price falls from the start price; Double (two-sided) auction |
| Количество (quantity) | how many **lots** were bought |
| Сумма сделки (deal amount) | in the **contract currency** (field «Валюта» in contracts.csv); thousands separated by a space, decimals by a dot |
| Стартовая цена (start price) | price per **one unit of measure** at which trading started, in the contract currency |
| Номер контракта (contract number) | the contract card in contracts.csv |
| Наименование товара (product name) | as in the registry: name and attributes in one string — "… Марка … Размер … Сорт … Тип … Класс …" (brand, size, grade, type, class) |
| Лот, Единица измерения (lot, unit) | how many units of measure are in one lot, and which units |
| Регион (region) | the region of the **seller's warehouse** |
| Тип контракта (contract type) | Внутренний (domestic) or Экспортный (export) |
| Форма контракта (contract form) | Спот (spot), Форвард (forward) or Фьючерс (futures) |
| Состояние сделки (deal status) | see below |
| ИНН продавца, ИНН покупателя (seller INN, buyer INN) | companies from participants.csv |
| Брокер продавца, Брокер покупателя (seller broker, buyer broker) | brokerage codes from brokers.csv |
| Дата оплаты, Дата поставки (payment date, delivery date) | when the buyer paid and when the goods were delivered; **empty if this has not happened yet** |
| Поставлено (delivered) | how many lots were actually delivered |

**How to calculate.** Deal volume in units of measure = Количество × Лот. Unit price = Сумма сделки ÷ (Количество × Лот).
Export deals are in dollars: amount in UZS = Сумма сделки × the rate on the **deal date** from usd_rates.csv.

The contract card is filled in by the seller, so the same product may be written differently by different sellers:
"1 сорт" and "1-сорта", with or without field labels, in capital letters.

| Deal status | Meaning |
|---|---|
| Новый (New) | the deal was concluded on the last day of the export, 30.10.2026 |
| Не подписан (Not signed) | the agreement has not yet been signed by the parties |
| Заключен (Concluded) | a forward or futures contract is signed; the payment term has not yet expired |
| Ждёт оплату (Awaiting payment) | a spot deal is signed; the payment term has not yet expired |
| Оплачен (Paid) | paid, goods not yet delivered |
| Исполнен (Executed) | paid and delivered in full |
| Исполнен(Частично) (Partially executed) | paid, part delivered — how many lots is shown in «Поставлено» |
| Аннулирован(Не оплачен) (Annulled, not paid) | not paid on time and annulled — **the deal did not take place** |

## Contract cards — contracts.csv

| Column | Meaning |
|---|---|
| Номер контракта (contract number) | deals refer to it |
| Наименование продукта; Марка, Размер, Сорт, Тип, Класс товара (product name; brand, size, grade, type, class) | as filled in by the seller; if the attribute fields are empty, everything is written in the name |
| Код ТН ВЭД, Группа товара, Категория товара, Отрасль промышленности (customs code HS/TN VED, product group, product category, industry) | product classification |
| Единица измерения, Лот (unit, lot) | the same as in the deals under this contract |
| Валюта (currency) | Uzbek sum or US dollar — the currency of amounts and prices of deals under the contract |
| Базисная цена (base price) | price per unit of measure on the contract registration date |
| Сумма залога (deposit) | deposit as a percentage of the deal amount |
| Базис поставки, Упаковка (delivery basis, packaging) | delivery terms |
| Регион, Адрес склада (region, warehouse address) | where the goods are stored |
| Тип торгов, Тип контракта, Форма контракта (auction type, contract type, contract form) | the same as in the deals under this contract |
| Срок оплаты (payment term) | calendar days **from the deal date** within which the buyer must pay |
| Срок поставки (delivery term) | calendar days **from the payment date** within which the seller must deliver |
| ИНН продавца (seller INN) | a company from participants.csv |
| Дата регистрации, Действует до (registration date, valid until) | deals are concluded under the contract during this period |

## Participants, brokers, rate

participants.csv — INN (9 digits), name, region, activity type, role (Продавец = seller or Покупатель = buyer), VAT payer.
brokers.csv — broker code, name, INN, region. usd_rates.csv — date and rate, UZS per 1 USD.
