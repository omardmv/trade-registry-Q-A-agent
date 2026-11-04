import csv

from app import config
from app.ingest.products import canonicalize


def test_spellings_of_raw_cotton_collapse():
    for name in [
        "ХЛОПОК СЫРЕЦ (СПОТ) 3 СОРТ I КЛАСС",
        "Хлопок-сырец (форвард)",
        "Хлопок сырец (фьючерс) второй сорт 2 класс",
    ]:
        m = canonicalize(name)
        assert m.canonical == "Хлопок-сырец"
        assert m.family == "Хлопок"


def test_cotton_byproducts_share_family_not_canonical():
    assert canonicalize("Шрот хлопковых семян").canonical == "Шрот хлопковый"
    assert canonicalize("ХЛОПКОВОЕ МАСЛО").canonical == "Масло хлопковое"
    assert canonicalize("Масло хлопковое высшего сорта рафинированное дезодорированное").canonical == "Масло хлопковое"
    assert canonicalize("Пряжа хлопковая").family == "Хлопок"


def test_order_sensitive_rules():
    assert canonicalize("ТРУБЫ ПОЛИЭТИЛЕНОВЫЕ").canonical == "Трубы полиэтиленовые"
    assert canonicalize("ПОЛИЭТИЛЕН HD-5502").canonical == "Полиэтилен"
    assert canonicalize("ПРОВОЛОКА АРМАТУРНАЯ").canonical == "Проволока арматурная"
    assert canonicalize("Мука пшеничная первый сорт хлебопекарная").canonical == "Мука пшеничная"
    assert canonicalize("Пшеница продовольственная 3-класс").canonical == "Пшеница"
    assert canonicalize("Мочевина (карбамид)").canonical == "Карбамид"


def test_every_contract_product_is_mapped():
    with open(config.DATA_DIR / "contracts.csv", encoding="utf-8") as f:
        names = {r["Наименование продукта"] for r in csv.DictReader(f)}
    unmapped = sorted(n for n in names if canonicalize(n) is None)
    assert unmapped == []


def test_regions_dictionary_covers_contract_regions():
    with open(config.DICT_DIR / "regions.csv", encoding="utf-8") as f:
        ru = {r["region_ru"] for r in csv.DictReader(f)}
    with open(config.DATA_DIR / "contracts.csv", encoding="utf-8") as f:
        used = {r["Регион"] for r in csv.DictReader(f)}
    assert used <= ru
