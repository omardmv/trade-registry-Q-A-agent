import duckdb, sys, pathlib
root = pathlib.Path(sys.argv[1]); out = sys.argv[2]
pathlib.Path(out).unlink(missing_ok=True)
con = duckdb.connect(out)
D = lambda c: f"strptime({c},'%d.%m.%Y')::DATE"
num = lambda c: f"TRY_CAST(replace(replace({c},' ',''),' ','') AS DOUBLE)"
files = ",".join(f"'{root}/deals_{y}.csv'" for y in range(2022, 2027))
con.execute(f"""
CREATE TABLE raw_deals AS SELECT * FROM read_csv([{files}], header=true, all_varchar=true, union_by_name=true);
CREATE TABLE deals AS SELECT
  "Номер сделки"::BIGINT AS deal_id,
  {D('"Дата сделки"')} AS deal_date,
  "Время сделки" AS deal_time,
  "Тип торгов" AS auction_type,
  {num('"Количество"')} AS qty_lots,
  {num('"Сумма сделки"')} AS amount,
  {num('"Стартовая цена"')} AS start_price,
  "Номер контракта"::BIGINT AS contract_id,
  "Наименование товара" AS product_name,
  {num('"Лот"')} AS lot_size,
  "Единица измерения" AS unit,
  "Регион" AS region,
  "Тип контракта" AS contract_type,
  "Форма контракта" AS contract_form,
  "Состояние сделки" AS status,
  "ИНН продавца" AS seller_inn,
  "ИНН покупателя" AS buyer_inn,
  "Брокер продавца" AS seller_broker,
  "Брокер покупателя" AS buyer_broker,
  CASE WHEN "Дата оплаты"='' THEN NULL ELSE {D('"Дата оплаты"')} END AS paid_date,
  CASE WHEN "Дата поставки"='' THEN NULL ELSE {D('"Дата поставки"')} END AS delivery_date,
  {num('"Поставлено"')} AS delivered_lots
FROM raw_deals;
CREATE TABLE contracts AS SELECT * FROM read_csv('{root}/contracts.csv', header=true, all_varchar=true);
CREATE TABLE participants AS SELECT * FROM read_csv('{root}/participants.csv', header=true, all_varchar=true);
CREATE TABLE brokers AS SELECT * FROM read_csv('{root}/brokers.csv', header=true, all_varchar=true);
CREATE TABLE usd_rates AS SELECT {D('"Дата"')} AS rate_date, "Курс"::DOUBLE AS uzs_per_usd FROM read_csv('{root}/usd_rates.csv', header=true, all_varchar=true);
DROP TABLE raw_deals;
CREATE TABLE contracts_clean AS SELECT
  "Номер контракта"::BIGINT AS contract_id, lower("Наименование продукта") AS product_base,
  nullif("Марка товара",'') AS brand, nullif("Размер товара",'') AS psize, nullif("Сорт товара",'') AS grade,
  nullif("Тип товара",'') AS ptype, nullif("Класс товара",'') AS pclass, "Код ТН ВЭД" AS tnved,
  "Группа товара" AS product_group, "Категория товара" AS product_category, "Отрасль промышленности" AS industry,
  "Единица измерения" AS unit, TRY_CAST("Лот" AS DOUBLE) AS lot_size, "Валюта" AS currency,
  TRY_CAST(replace("Базисная цена",' ','') AS DOUBLE) AS base_price,
  TRY_CAST(trim(replace("Сумма залога",'%','')) AS DOUBLE) AS deposit_pct,
  "Базис поставки" AS delivery_basis, "Регион" AS region, "Адрес склада" AS warehouse_address,
  "Тип торгов" AS auction_type, "Тип контракта" AS contract_type, "Форма контракта" AS contract_form,
  TRY_CAST("Срок оплаты" AS INT) AS pay_term_days, TRY_CAST("Срок поставки" AS INT) AS delivery_term_days,
  "Упаковка" AS packaging, "ИНН продавца" AS seller_inn,
  strptime("Дата регистрации",'%d.%m.%Y')::DATE AS reg_date, strptime("Действует до",'%d.%m.%Y')::DATE AS valid_to
FROM contracts;
CREATE TABLE deals_enriched AS
SELECT d.*, d.qty_lots*d.lot_size AS volume_units,
  d.amount/NULLIF(d.qty_lots*d.lot_size,0) AS price_per_unit,
  c.currency, c.product_base,
  CASE WHEN c.currency='Доллар США' THEN d.amount*r.uzs_per_usd ELSE d.amount END AS amount_uzs,
  c.product_group, c.product_category, c.industry, c.tnved, c.brand, c.grade, c.pclass, c.ptype, c.psize,
  c.base_price, c.deposit_pct, c.delivery_basis, c.packaging, c.warehouse_address,
  c.pay_term_days, c.delivery_term_days, c.reg_date AS contract_reg_date, c.valid_to AS contract_valid_to,
  CASE WHEN d.paid_date IS NULL THEN NULL ELSE d.paid_date > d.deal_date + c.pay_term_days END AS paid_late,
  CASE WHEN d.delivery_date IS NULL OR d.paid_date IS NULL THEN NULL ELSE d.delivery_date > d.paid_date + c.delivery_term_days END AS delivered_late,
  round(100.0 * (d.amount/NULLIF(d.qty_lots*d.lot_size,0) / NULLIF(d.start_price,0) - 1), 4) AS price_vs_start_pct
FROM deals d LEFT JOIN contracts_clean c ON c.contract_id=d.contract_id
LEFT JOIN usd_rates r ON r.rate_date=d.deal_date;
CREATE OR REPLACE TABLE contracts_clean AS SELECT c.*, m.region_lat FROM contracts_clean c
  LEFT JOIN (SELECT DISTINCT c2.region, d.region AS region_lat FROM contracts_clean c2 JOIN deals d USING(contract_id)) m USING(region);
""")
for t in ["deals","contracts","participants","brokers","usd_rates"]:
    print(t, con.execute(f"select count(*) from {t}").fetchone()[0])
print(con.execute("select count(*) filter(where amount is null), count(*) filter(where qty_lots is null), count(*) filter(where deal_date is null) from deals").fetchall())
print(con.execute("select status,count(*) from deals group by 1 order by 2 desc").fetchall())
print(con.execute("select currency,contract_type,count(*) from deals_enriched group by 1,2").fetchall())
print(con.execute("select count(*) from deals_enriched where amount_uzs is null").fetchall())
