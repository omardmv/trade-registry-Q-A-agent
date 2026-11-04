"""Canonical product dictionary: maps seller-written contract product names to canonical products.

Run `python -m app.ingest.products` to regenerate data_dictionary/products.csv, then review the diff.
"""
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app import config


@dataclass(frozen=True)
class ProductMatch:
    canonical: str
    family: str
    synonyms: str


# (regex on lower-cased name, canonical, family, synonyms "; "-separated). First match wins.
RULES: list[tuple[str, str, str, str]] = [
    (r"хлопок[\s-]*сырец", "Хлопок-сырец", "Хлопок", "paxta xom ashyosi; xom paxta; paxta; хлопок; хлопок-сырец"),
    (r"шрот\s+хлопк", "Шрот хлопковый", "Хлопок", "paxta shroti; shrot; шрот"),
    (r"масло\s+хлопков|хлопковое\s+масло", "Масло хлопковое", "Хлопок", "paxta yog‘i; paxta moyi"),
    (r"пряжа", "Пряжа хлопчатобумажная", "Хлопок", "paxta ipi; ip kalava; пряжа"),
    (r"масло\s+подсолн|подсолнечное\s+масло", "Масло подсолнечное", "Масло растительное", "kungaboqar yog‘i; kungaboqar moyi"),
    (r"бензин", "Бензин автомобильный", "Нефтепродукты", "benzin; AI-92; AI-95; AI-80"),
    (r"дизел", "Дизельное топливо", "Нефтепродукты", "dizel yoqilg‘isi; dizel; solyarka"),
    (r"мазут", "Мазут", "Нефтепродукты", "mazut"),
    (r"битум", "Битум нефтяной", "Нефтепродукты", "bitum"),
    (r"сжиженн|пропан", "Сжиженный газ", "Нефтепродукты", "suyultirilgan gaz; propan-butan"),
    (r"карбамид|мочевин", "Карбамид", "Минеральные удобрения", "karbamid; mochevina; o‘g‘it"),
    (r"селитр", "Аммиачная селитра", "Минеральные удобрения", "ammiakli selitra; selitra; o‘g‘it"),
    (r"^азот", "Азот", "Технические газы", "azot"),
    (r"аргон", "Аргон газообразный", "Технические газы", "argon"),
    (r"серн", "Кислота серная", "Химическая продукция", "sulfat kislota; oltingugurt kislotasi"),
    (r"сода", "Сода каустическая", "Химическая продукция", "kaustik soda; natriy gidroksid"),
    (r"полипропилен", "Полипропилен", "Полимеры", "polipropilen"),
    (r"труб", "Трубы полиэтиленовые", "Полимерные изделия", "polietilen quvur; quvur"),
    (r"полиэтилен", "Полиэтилен", "Полимеры", "polietilen"),
    (r"поливинилхлорид", "Поливинилхлорид", "Полимеры", "polivinilxlorid; PVX; ПВХ"),
    (r"эмаль", "Эмаль", "Лакокрасочные материалы", "emal; bo‘yoq"),
    (r"цемент", "Портландцемент", "Строительные материалы", "sement; portlandsement"),
    (r"кирпич", "Кирпич керамический", "Строительные материалы", "g‘isht"),
    (r"гипс", "Гипс строительный", "Строительные материалы", "gips"),
    (r"известь", "Известь", "Строительные материалы", "ohak"),
    (r"стекло", "Стекло листовое", "Строительные материалы", "shisha; oyna"),
    (r"пиломатериал", "Пиломатериалы", "Лесоматериалы", "yog‘och; taxta"),
    (r"проволока", "Проволока арматурная", "Металлопродукция", "armatura simi; sim"),
    (r"арматура", "Арматура стальная", "Металлопродукция", "armatura"),
    (r"лист\s+стальной", "Лист стальной горячекатаный", "Металлопродукция", "po‘lat list"),
    (r"катод|медный", "Катод медный", "Металлопродукция", "mis katod; mis"),
    (r"алюмин", "Профиль алюминиевый", "Металлопродукция", "alyuminiy profil"),
    (r"кабель", "Кабель силовой", "Электротехника", "kabel"),
    (r"подшипник", "Подшипник", "Машиностроение", "podshipnik"),
    (r"мука", "Мука пшеничная", "Зерно и мука", "un; bug‘doy uni"),
    (r"пшеница", "Пшеница", "Зерно и мука", "bug‘doy"),
    (r"^рис", "Рис", "Зерно и мука", "guruch"),
    (r"сахар", "Сахар", "Продовольствие", "shakar"),
    (r"соль", "Соль пищевая", "Продовольствие", "osh tuzi; tuz"),
    (r"вода", "Вода питьевая", "Продовольствие", "ichimlik suvi; suv"),
    (r"комбикорм", "Комбикорм", "Корма", "omuxta yem; yem"),
    (r"кормосмесь", "Кормосмесь", "Корма", "yem aralashmasi; yem"),
]


def canonicalize(name: str) -> ProductMatch | None:
    low = name.lower()
    for pattern, canonical, family, synonyms in RULES:
        if re.search(pattern, low):
            return ProductMatch(canonical, family, synonyms)
    return None


def write_dictionary(contracts_csv: Path, out_csv: Path) -> int:
    with open(contracts_csv, encoding="utf-8") as f:
        names = sorted({r["Наименование продукта"] for r in csv.DictReader(f)})
    unmapped = [n for n in names if canonicalize(n) is None]
    if unmapped:
        raise SystemExit(f"Unmapped product names: {unmapped}")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["product_name", "product_canonical", "product_family", "synonyms"])
        for n in names:
            m = canonicalize(n)
            w.writerow([n, m.canonical, m.family, m.synonyms])
    return len(names)


if __name__ == "__main__":
    count = write_dictionary(config.DATA_DIR / "contracts.csv", config.DICT_DIR / "products.csv")
    print(f"wrote {count} product names", file=sys.stderr)
