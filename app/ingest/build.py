"""Build the DuckDB database from the source CSVs. Run: python -m app.ingest.build"""
import os
import sys
from pathlib import Path

import duckdb
from duckdb.sqltypes import VARCHAR

from app import config
from app.text import normalize_name


class BuildError(RuntimeError):
    pass


def _q(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _csv(path: Path | str) -> str:
    return f"read_csv({_q(Path(path))}, header=true, all_varchar=true, union_by_name=true)"


def build(data_dir: Path, dict_dir: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    try:
        con.create_function("norm_name", normalize_name, [VARCHAR], VARCHAR)
        con.execute("CREATE MACRO num(x) AS TRY_CAST(replace(replace(trim(x), ' ', ''), chr(160), '') AS DOUBLE)")
        con.execute("CREATE MACRO dmy(x) AS CAST(try_strptime(x, '%d.%m.%Y') AS DATE)")

        con.execute(f"CREATE TABLE regions AS SELECT * FROM {_csv(dict_dir / 'regions.csv')}")
        con.execute(f"CREATE TABLE products AS SELECT * FROM {_csv(dict_dir / 'products.csv')}")
        con.execute(f"""CREATE TABLE usd_rates AS
            SELECT dmy("Дата") AS rate_date, num("Курс") AS rate FROM {_csv(data_dir / 'usd_rates.csv')}""")

        con.execute(f"""CREATE TABLE contracts AS
            SELECT CAST(c."Номер контракта" AS INTEGER) AS contract_id,
                   c."Наименование продукта" AS product_name,
                   c."Марка товара" AS brand, c."Размер товара" AS size, c."Сорт товара" AS grade,
                   c."Тип товара" AS product_type, c."Класс товара" AS product_class,
                   c."Код ТН ВЭД" AS hs_code, c."Группа товара" AS product_group,
                   c."Категория товара" AS category, c."Отрасль промышленности" AS industry,
                   c."Единица измерения" AS unit, num(c."Лот") AS lot,
                   CASE c."Валюта" WHEN 'Доллар США' THEN 'USD' WHEN 'Узбекский сум' THEN 'UZS' END AS currency,
                   num(c."Базисная цена") AS base_price,
                   num(replace(c."Сумма залога", '%', '')) AS deposit_pct,
                   c."Базис поставки" AS delivery_basis, c."Регион" AS region_ru, r.region,
                   c."Адрес склада" AS warehouse_address, c."Тип торгов" AS trade_type,
                   c."Тип контракта" AS contract_type, c."Форма контракта" AS contract_form,
                   CAST(c."Срок оплаты" AS INTEGER) AS payment_term_days,
                   CAST(c."Срок поставки" AS INTEGER) AS delivery_term_days,
                   c."Упаковка" AS packaging, c."ИНН продавца" AS seller_inn,
                   dmy(c."Дата регистрации") AS registered_date, dmy(c."Действует до") AS valid_until,
                   p.product_canonical, p.product_family
            FROM {_csv(data_dir / 'contracts.csv')} c
            LEFT JOIN regions r ON r.region_ru = c."Регион"
            LEFT JOIN products p ON p.product_name = c."Наименование продукта" """)

        deals_glob = data_dir / "deals_*.csv"
        raw_count = con.execute(f"SELECT COUNT(*) FROM {_csv(deals_glob)}").fetchone()[0]
        con.execute(f"""CREATE TABLE deals AS
            WITH b AS (
              SELECT CAST("Номер сделки" AS BIGINT) AS deal_id, dmy("Дата сделки") AS deal_date,
                     CAST("Время сделки" AS TIME) AS deal_time, "Тип торгов" AS trade_type,
                     num("Количество") AS quantity, num("Сумма сделки") AS amount,
                     num("Стартовая цена") AS start_price, CAST("Номер контракта" AS INTEGER) AS contract_id,
                     "Наименование товара" AS product_name_full, num("Лот") AS lot,
                     "Единица измерения" AS unit, "Регион" AS region, "Тип контракта" AS contract_type,
                     "Форма контракта" AS contract_form, "Состояние сделки" AS status,
                     "ИНН продавца" AS seller_inn, "ИНН покупателя" AS buyer_inn,
                     "Брокер продавца" AS seller_broker, "Брокер покупателя" AS buyer_broker,
                     dmy("Дата оплаты") AS payment_date, dmy("Дата поставки") AS delivery_date,
                     num("Поставлено") AS delivered_lots
              FROM {_csv(deals_glob)})
            SELECT b.*, year(b.deal_date) AS year, month(b.deal_date) AS month,
                   b.quantity * b.lot AS volume,
                   CASE b.unit WHEN 'тонна' THEN b.quantity * b.lot
                               WHEN 'килограмм' THEN b.quantity * b.lot / 1000 END AS volume_tonnes,
                   b.amount / (b.quantity * b.lot) AS unit_price,
                   c.currency, r.rate AS usd_rate,
                   CASE c.currency WHEN 'UZS' THEN b.amount ELSE b.amount * r.rate END AS amount_uzs,
                   CASE c.currency WHEN 'USD' THEN b.amount ELSE b.amount / r.rate END AS amount_usd,
                   b.status = 'Аннулирован(Не оплачен)' AS is_cancelled,
                   c.product_canonical, c.product_family
            FROM b JOIN contracts c ON c.contract_id = b.contract_id
                   JOIN usd_rates r ON r.rate_date = b.deal_date
            ORDER BY b.deal_id""")

        con.execute(f"""CREATE TABLE participants AS
            SELECT p."ИНН" AS inn, p."Наименование" AS name, norm_name(p."Наименование") AS name_norm,
                   p."Регион" AS region, p."Вид деятельности" AS activity, p."Роль" AS role,
                   p."Плательщик НДС" = 'Да' AS vat_payer,
                   COALESCE(b.n, 0) AS deals_as_buyer, COALESCE(s.n, 0) AS deals_as_seller
            FROM {_csv(data_dir / 'participants.csv')} p
            LEFT JOIN (SELECT buyer_inn AS inn, COUNT(*) AS n FROM deals GROUP BY 1) b ON b.inn = p."ИНН"
            LEFT JOIN (SELECT seller_inn AS inn, COUNT(*) AS n FROM deals GROUP BY 1) s ON s.inn = p."ИНН" """)

        con.execute(f"""CREATE TABLE brokers AS
            SELECT k."Код брокера" AS broker_code, k."Наименование" AS name,
                   norm_name(k."Наименование") AS name_norm, k."ИНН" AS inn, k."Регион" AS region,
                   COALESCE(s.n, 0) AS deals_as_seller_broker, COALESCE(b.n, 0) AS deals_as_buyer_broker
            FROM {_csv(data_dir / 'brokers.csv')} k
            LEFT JOIN (SELECT seller_broker AS code, COUNT(*) AS n FROM deals GROUP BY 1) s ON s.code = k."Код брокера"
            LEFT JOIN (SELECT buyer_broker AS code, COUNT(*) AS n FROM deals GROUP BY 1) b ON b.code = k."Код брокера" """)

        _check(con, raw_count)
        con.close()
        os.replace(tmp, db_path)
    except Exception:
        con.close()
        tmp.unlink(missing_ok=True)
        raise


def _check(con, raw_count: int) -> None:
    def scalar(sql):
        return con.execute(sql).fetchone()[0]

    problems = []
    n = scalar("SELECT COUNT(*) FROM deals")
    if n != raw_count:
        problems.append(f"deals: {n} rows after joins, {raw_count} in CSV (missing contract or USD rate)")
    checks = {
        "deals with NULL numbers/dates": """SELECT COUNT(*) FROM deals WHERE amount IS NULL OR quantity IS NULL
            OR lot IS NULL OR deal_date IS NULL OR deal_time IS NULL""",
        "contracts without currency": "SELECT COUNT(*) FROM contracts WHERE currency IS NULL",
        "contracts without region mapping": "SELECT COUNT(*) FROM contracts WHERE region IS NULL",
        "contracts without canonical product": "SELECT COUNT(*) FROM contracts WHERE product_canonical IS NULL",
        "usd_rates with NULL": "SELECT COUNT(*) FROM usd_rates WHERE rate IS NULL OR rate_date IS NULL",
    }
    for label, sql in checks.items():
        bad = scalar(sql)
        if bad:
            problems.append(f"{label}: {bad}")
    if problems:
        raise BuildError("; ".join(problems))


if __name__ == "__main__":
    build(config.DATA_DIR, config.DICT_DIR, config.DB_PATH)
    print(f"built {config.DB_PATH}", file=sys.stderr)
