# Exchange Q&A Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A web service whose Gemini-based agent answers Russian/Uzbek questions about the exchange deal registry in `data/` with grounded, verified answers and plain "no data" replies when the data has no answer.

**Architecture:** CSVs are loaded once into a typed DuckDB file with derived columns (volume, UZS/USD amounts, cancellation flag, canonical product). A Gemini writer agent resolves entity names to IDs, then fills a **structured query spec** that deterministic code compiles to SQL from unit-tested templates (free SQL with validators is only a fallback). A composer writes the answer from result rows only; a deterministic verifier checks every number/ID/date/quoted name against the rows and falls back to a template answer; a code-generated sentence states exactly what was computed. FastAPI serves `POST /ask` and a one-page chat UI.

**Tech Stack:** Python 3.12, uv, DuckDB, FastAPI + uvicorn, google-genai, sqlglot, rapidfuzz, PyYAML, pytest, httpx, Docker.

Spec: `docs/superpowers/specs/2026-11-04-exchange-qa-agent-design.md`

## Global Constraints

- Python `>=3.12,<3.13` (the host has 3.15; uv pins 3.12 via `.python-version`).
- Every answer ≤ 90 s end to end; pipeline hard deadline `UZEX_DEADLINE_S=75`.
- The runtime DuckDB connection is `read_only=True` with `enable_external_access=false`.
- No runtime LLM call may introduce data: final answers pass `verify_answer` or are replaced by a template built from rows.
- Entity filters in SQL use only values returned by `resolve()` in the same request or present verbatim in the question.
- Ambiguity splits only on the 5 registry axes: `status_scope`, `measure`, `currency`, `product_scope`, `role`; at most 2 per query.
- Max 8 tool calls per question, max 2 fix retries after the first `compile_query` / `submit_sql`.
- Prefer `compile_query(spec)`; `submit_sql` only when no spec can express the question.
- "No data" texts: ru `В данных нет ответа на этот вопрос.` · uz `Ma’lumotlarda bu savolga javob yo‘q.` · uz-Cyrl `Маълумотларда бу саволга жавоб йўқ.`
- Gemini model from env `GEMINI_MODEL` (default `gemini-2.5-flash`), key from `GEMINI_API_KEY`.
- Unit tests never call Gemini; live tests are marked `live` and excluded by default.

**Deviations from the spec (deliberate, simpler):**
- `is_cancelled` (true for `Аннулирован(Не оплачен)`) instead of `is_completed`; added `volume_tonnes`.
- Intent classification is folded into the writer (`declare_unanswerable` tool) — one fewer LLM round trip.
- `get_examples` uses local character-trigram similarity, not Gemini embeddings (≈25 examples; no network, deterministic).
- Eval expected rows are computed from `gold_sql` at run time; `key_columns` replace hand-typed `must_mention`.

## File Structure

```
pyproject.toml, .python-version, .gitignore, .dockerignore, Dockerfile, README.md
app/
  config.py            paths, env settings
  text.py              transliteration, name normalization, language detection
  db.py                read-only DuckDB access with timeouts
  resolve.py           value resolution (company/broker/product/region/contract)
  semantic.yaml        semantic layer (columns, rules, ambiguity registry, glossary)
  semantic.py          load + render semantic layer into prompt text
  examples.py          curated SQL example store + trigram search
  pipeline.py          orchestration with deadline; AskResult; build_pipeline()
  api.py               FastAPI app
  static/index.html    chat UI
  ingest/products.py   canonical product rules + dictionary generator
  ingest/build.py      CSV → DuckDB build
  compiler/spec.py     structured query spec + strict validation
  compiler/sql.py      spec → SQL templates
  compiler/explain.py  deterministic "what was computed" sentence
  agent/llm.py         LLM interfaces + Gemini implementation
  agent/validators.py  free-SQL validation (fallback path)
  agent/writer.py      writer agent loop
  answer/numbers.py    number/ID/date extraction and formatting
  answer/verifier.py   deterministic answer verifier
  answer/templates.py  no-data / failure / table templates
  answer/composer.py   LLM answer composer
data_dictionary/products.csv, regions.csv
examples/examples.yaml
eval/questions.yaml, eval/run.py
tests/...
```

---

### Task 1: Project scaffold and text normalization

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `app/__init__.py`, `app/config.py`, `app/text.py`, `tests/__init__.py`, `tests/test_text.py`

**Interfaces:**
- Produces: `app.text.to_latin(text: str) -> str`, `normalize_text(text: str) -> str`, `normalize_name(text: str) -> str`, `detect_lang(text: str) -> Literal["ru","uz","uz-Cyrl"]`; `app.config` constants `ROOT, DATA_DIR, DICT_DIR, DB_PATH, EXAMPLES_PATH, SEMANTIC_PATH, GEMINI_MODEL, GEMINI_API_KEY, DEADLINE_S, LLM_TIMEOUT_S, MAX_TOOL_CALLS, MAX_FIX_RETRIES, ROW_LIMIT`.

- [ ] **Step 1: Create scaffold files**

`pyproject.toml`:
```toml
[project]
name = "uzex-qa"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "duckdb>=1.1",
  "fastapi>=0.115",
  "uvicorn>=0.30",
  "google-genai>=1.0",
  "sqlglot>=25",
  "pyyaml>=6",
  "rapidfuzz>=3",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = ["live: calls the real Gemini API (needs GEMINI_API_KEY)"]
addopts = "-m 'not live'"
```

`.python-version`:
```
3.12
```

`.gitignore`:
```
.venv/
__pycache__/
build/
eval/results/
.env
```

`app/__init__.py` and `tests/__init__.py`: empty files.

`app/config.py`:
```python
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("UZEX_DATA_DIR", ROOT / "data"))
DICT_DIR = ROOT / "data_dictionary"
DB_PATH = Path(os.environ.get("UZEX_DB_PATH", ROOT / "build" / "uzex.duckdb"))
EXAMPLES_PATH = ROOT / "examples" / "examples.yaml"
SEMANTIC_PATH = ROOT / "app" / "semantic.yaml"

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

DEADLINE_S = float(os.environ.get("UZEX_DEADLINE_S", "75"))
LLM_TIMEOUT_S = float(os.environ.get("UZEX_LLM_TIMEOUT_S", "25"))
MAX_TOOL_CALLS = 8
MAX_FIX_RETRIES = 2
ROW_LIMIT = 1000
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: creates `.venv` with Python 3.12 and `uv.lock`.

- [ ] **Step 3: Write the failing tests**

`tests/test_text.py`:
```python
from app.text import detect_lang, normalize_name, normalize_text, to_latin


def test_to_latin_uzbek_letters():
    assert to_latin("ҳарайўм кимё") == "harayom kimyo"
    assert to_latin("қопур") == "qopur"


def test_normalize_name_cyrillic_and_latin_agree():
    assert normalize_name('"ҲАРАЙЎМ КИМЁ" МЧЖ') == "harayom kimyo"
    assert normalize_name('"Pudexiz Plast" MChJ') == "pudexiz plast"
    assert normalize_name("PUBAM KIMYO MAS’ULIYATI CHEKLANGAN JAMIYATI") == "pubam kimyo"
    assert normalize_name('"Go‘qe Prokat" MChJ') == normalize_name("Go'qe prokat") == "goqe prokat"
    assert normalize_name('"ҚОПУР БРОКЕР" МЧЖ') == "qopur broker"
    assert normalize_name("RO‘QI ENERGO OK") == "roqi energo"


def test_normalize_text_keeps_all_tokens():
    assert normalize_text("Farg‘ona viloyati") == "fargona viloyati"
    assert normalize_text("Хлопок-сырец (спот)") == "xlopok sirets spot"


def test_detect_lang():
    assert detect_lang("Сколько сделок было заключено в октябре 2025 года?") == "ru"
    assert detect_lang("2025-yilda eng ko'p savdo qilgan korxona qaysi?") == "uz"
    assert detect_lang("2025 йилда энг кўп савдо қилган корхона қайси?") == "uz-Cyrl"
    assert detect_lang("Нечта битим тузилган?") == "uz-Cyrl"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_text.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.text'`

- [ ] **Step 5: Implement `app/text.py`**

```python
"""Text normalization shared by ingest, value resolution and verification."""
import re
from typing import Literal

_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ь": "", "ы": "i", "э": "e", "ю": "yu",
    "я": "ya", "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h",
}
_APOSTROPHES = "‘’ʻʼ'`´"
_LEGAL_FORMS = {
    "mchj", "ooo", "aj", "ao", "ok", "xk", "qk", "chp", "yatt",
    "masuliyati", "cheklangan", "jamiyati",
}
_UZ_CYRL_LETTERS = set("ўқғҳ")
_UZ_CYRL_WORDS = re.compile(r"\b(нечта|қанча|қайси|йил\w*|битим\w*|ким|бўл\w*|канча|кайси)\b", re.I)


def to_latin(text: str) -> str:
    """Lower-case and transliterate Cyrillic (Russian + Uzbek) to Uzbek Latin."""
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text.lower())


def normalize_text(text: str) -> str:
    """Latin, lower case, no apostrophes, punctuation collapsed to single spaces."""
    t = to_latin(text)
    for a in _APOSTROPHES:
        t = t.replace(a, "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def normalize_name(text: str) -> str:
    """normalize_text without legal-form tokens (MChJ, МЧЖ, AJ, ...)."""
    return " ".join(w for w in normalize_text(text).split() if w not in _LEGAL_FORMS)


def detect_lang(text: str) -> Literal["ru", "uz", "uz-Cyrl"]:
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cyr > lat:
        low = text.lower()
        if any(ch in _UZ_CYRL_LETTERS for ch in low) or _UZ_CYRL_WORDS.search(low):
            return "uz-Cyrl"
        return "ru"
    return "uz"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_text.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore app tests
git commit -m "feat: project scaffold and text normalization"
```

---

### Task 2: Product and region dictionaries

**Files:**
- Create: `app/ingest/__init__.py`, `app/ingest/products.py`, `data_dictionary/regions.csv`, `data_dictionary/products.csv` (generated), `tests/test_products.py`

**Interfaces:**
- Consumes: `app.config.DATA_DIR, DICT_DIR`
- Produces: `app.ingest.products.ProductMatch(canonical: str, family: str, synonyms: str)`, `canonicalize(name: str) -> ProductMatch | None`, `write_dictionary(contracts_csv: Path, out_csv: Path) -> int`; CSV `products.csv` columns `product_name,product_canonical,product_family,synonyms`; CSV `regions.csv` columns `region,region_ru,synonyms`.

- [ ] **Step 1: Write the failing tests**

`tests/test_products.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_products.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest'`

- [ ] **Step 3: Create `data_dictionary/regions.csv`**

Uzbek names must match the data byte-for-byte (the apostrophe in `Farg‘ona` and `Qoraqalpog‘iston` is U+2018).
```csv
region,region_ru,synonyms
Toshkent shahri,г. Ташкент,Ташкент; город Ташкент; Toshkent; Tashkent; Тошкент шаҳри
Toshkent viloyati,Ташкентская область,Ташкентская обл; Toshkent vil; Тошкент вилояти
Andijon viloyati,Андижанская область,Андижан; Andijon; Андижон
Buxoro viloyati,Бухарская область,Бухара; Buxoro; Bukhara; Бухоро
Farg‘ona viloyati,Ферганская область,Фергана; Fargona; Fergana; Фарғона
Jizzax viloyati,Джизакская область,Джизак; Jizzax; Jizzakh; Жиззах
Namangan viloyati,Наманганская область,Наманган; Namangan
Navoiy viloyati,Навоийская область,Навои; Navoiy; Navoi; Навоий
Qashqadaryo viloyati,Кашкадарьинская область,Кашкадарья; Qashqadaryo; Қашқадарё
Qoraqalpog‘iston Respublikasi,Республика Каракалпакстан,Каракалпакстан; Qoraqalpogiston; Karakalpakstan; Қорақалпоғистон
Samarqand viloyati,Самаркандская область,Самарканд; Samarqand; Samarkand; Самарқанд
Sirdaryo viloyati,Сырдарьинская область,Сырдарья; Sirdaryo; Syrdarya; Сирдарё
Surxondaryo viloyati,Сурхандарьинская область,Сурхандарья; Surxondaryo; Surkhandarya; Сурхондарё
Xorazm viloyati,Хорезмская область,Хорезм; Xorazm; Khorezm; Хоразм
```

- [ ] **Step 4: Implement `app/ingest/products.py`** (and empty `app/ingest/__init__.py`)

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_products.py -v`
Expected: 5 passed

- [ ] **Step 6: Generate and review the dictionary**

Run: `uv run python -m app.ingest.products && cut -d, -f2 data_dictionary/products.csv | sort | uniq -c | sort -rn`
Expected: `wrote 230 product names`; ~42 canonical products. Read the CSV and confirm no name is mapped to an obviously wrong canonical product (e.g. a flour row mapped to wheat). Fix rules and re-run if needed.

- [ ] **Step 7: Commit**

```bash
git add app/ingest data_dictionary tests/test_products.py
git commit -m "feat: canonical product and region dictionaries"
```

---
### Task 3: DuckDB build

**Files:**
- Create: `app/ingest/build.py`, `tests/conftest.py`, `tests/test_build.py`

**Interfaces:**
- Consumes: `app.text.normalize_name`, `data_dictionary/products.csv`, `data_dictionary/regions.csv`
- Produces: `app.ingest.build.build(data_dir: Path, dict_dir: Path, db_path: Path) -> None`, `BuildError`; pytest fixture `db_path` (session-scoped built DB). DuckDB tables:
  - `deals(deal_id, deal_date, deal_time, trade_type, quantity, amount, start_price, contract_id, product_name_full, lot, unit, region, contract_type, contract_form, status, seller_inn, buyer_inn, seller_broker, buyer_broker, payment_date, delivery_date, delivered_lots, year, month, volume, volume_tonnes, unit_price, currency, usd_rate, amount_uzs, amount_usd, is_cancelled, product_canonical, product_family)`
  - `contracts(contract_id, product_name, brand, size, grade, product_type, product_class, hs_code, product_group, category, industry, unit, lot, currency, base_price, deposit_pct, delivery_basis, region_ru, region, warehouse_address, trade_type, contract_type, contract_form, payment_term_days, delivery_term_days, packaging, seller_inn, registered_date, valid_until, product_canonical, product_family)`
  - `participants(inn, name, name_norm, region, activity, role, vat_payer, deals_as_buyer, deals_as_seller)`
  - `brokers(broker_code, name, name_norm, inn, region, deals_as_seller_broker, deals_as_buyer_broker)`
  - `usd_rates(rate_date, rate)`, `products(product_name, product_canonical, product_family, synonyms)`, `regions(region, region_ru, synonyms)`

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:
```python
import pytest

from app import config
from app.ingest.build import build


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "uzex.duckdb"
    build(config.DATA_DIR, config.DICT_DIR, path)
    return path
```

`tests/test_build.py`:
```python
import datetime as dt

import duckdb
import pytest


@pytest.fixture(scope="module")
def con(db_path):
    c = duckdb.connect(str(db_path), read_only=True)
    yield c
    c.close()


def one(con, sql, params=()):
    return con.execute(sql, params).fetchone()


def test_row_counts_match_readme(con):
    assert one(con, "SELECT COUNT(*) FROM deals")[0] == 500_000
    years = dict(con.execute("SELECT year, COUNT(*) FROM deals GROUP BY year").fetchall())
    assert years == {2022: 84_299, 2023: 93_985, 2024: 106_647, 2025: 115_763, 2026: 99_306}
    assert one(con, "SELECT COUNT(*) FROM contracts")[0] == 6_616
    assert one(con, "SELECT COUNT(*) FROM participants")[0] == 14_392
    assert one(con, "SELECT COUNT(*) FROM brokers")[0] == 120
    assert one(con, "SELECT COUNT(*) FROM usd_rates")[0] == 1_765


def test_domestic_deal_parsed(con):
    row = one(con, """SELECT deal_date, amount, volume, currency, amount_uzs, is_cancelled, contract_id
                      FROM deals WHERE deal_id = 387830""")
    assert row == (dt.date(2025, 1, 2), 85_256_750.0, 25.0, "UZS", 85_256_750.0, False, 102267)


def test_export_deal_converted_with_deal_date_rate(con):
    amount, currency, amount_uzs, rate = one(con, """
        SELECT d.amount, d.currency, d.amount_uzs, r.rate
        FROM deals d JOIN usd_rates r ON r.rate_date = d.deal_date WHERE d.deal_id = 280091""")
    assert currency == "USD"
    assert amount == pytest.approx(185_176.24)
    assert amount_uzs == pytest.approx(amount * rate)


def test_cancelled_and_status_counts(con):
    assert one(con, "SELECT COUNT(*) FROM deals WHERE is_cancelled")[0] == 33_719
    assert one(con, "SELECT COUNT(*) FROM deals WHERE status = 'Исполнен'")[0] == 431_061


def test_contract_region_mapped_and_product_canonical(con):
    assert one(con, "SELECT region, product_canonical FROM contracts WHERE contract_id = 100001") == (
        "Farg‘ona viloyati", "Профиль алюминиевый")


def test_no_nulls_in_critical_columns(con):
    assert one(con, """SELECT COUNT(*) FROM deals WHERE amount IS NULL OR quantity IS NULL OR lot IS NULL
                       OR currency IS NULL OR usd_rate IS NULL OR product_canonical IS NULL""")[0] == 0


def test_participant_normalized_name_and_counts(con):
    name_norm, buyer, seller = one(con, """SELECT name_norm, deals_as_buyer, deals_as_seller
                                           FROM participants WHERE inn = '915688255'""")
    assert name_norm == "harayom kimyo"
    assert buyer + seller >= 0


def test_mass_volume_in_tonnes(con):
    unit, volume, tonnes = one(con, "SELECT unit, volume, volume_tonnes FROM deals WHERE deal_id = 100001")
    assert unit == "килограмм"
    assert tonnes == pytest.approx(volume / 1000)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_build.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest.build'`

- [ ] **Step 3: Implement `app/ingest/build.py`**

```python
"""Build the DuckDB database from the source CSVs. Run: python -m app.ingest.build"""
import os
import sys
from pathlib import Path

import duckdb
from duckdb.typing import VARCHAR

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_build.py -v`
Expected: 8 passed. If `test_row_counts_match_readme` or `_check` fails, inspect the offending rows with `duckdb` before changing parsing — never loosen the check.

- [ ] **Step 5: Build the real DB**

Run: `uv run python -m app.ingest.build`
Expected: `built .../build/uzex.duckdb`

- [ ] **Step 6: Commit**

```bash
git add app/ingest/build.py tests/conftest.py tests/test_build.py
git commit -m "feat: build typed DuckDB database from CSVs"
```

---

### Task 4: Read-only database access

**Files:**
- Create: `app/db.py`, `tests/test_db.py`
- Modify: `tests/conftest.py` (add `db` fixture)

**Interfaces:**
- Consumes: built DB from Task 3
- Produces: `app.db.Database(path: Path)` with `.query(sql: str, params: list | None = None, timeout_s: float = 5.0, max_rows: int = 1000) -> QueryResult` and `.explain(sql: str) -> None`; `QueryResult(columns: list[str], rows: list[tuple], truncated: bool)`; `QueryError(Exception)`; `to_jsonable(value) -> Any`; fixture `db`.

- [ ] **Step 1: Add the fixture and failing tests**

Append to `tests/conftest.py`:
```python
from app.db import Database


@pytest.fixture(scope="session")
def db(db_path):
    return Database(db_path)
```

`tests/test_db.py`:
```python
import datetime as dt
from decimal import Decimal

import pytest

from app.db import QueryError, to_jsonable


def test_query_returns_columns_and_rows(db):
    res = db.query("SELECT COUNT(*) AS n FROM brokers")
    assert res.columns == ["n"]
    assert res.rows == [(120,)]
    assert res.truncated is False


def test_query_truncates(db):
    res = db.query("SELECT broker_code FROM brokers ORDER BY 1", max_rows=5)
    assert len(res.rows) == 5
    assert res.truncated is True


def test_writes_are_rejected(db):
    with pytest.raises(QueryError):
        db.query("DELETE FROM brokers")


def test_external_access_is_disabled(db):
    with pytest.raises(QueryError):
        db.query("SELECT * FROM read_csv('data/brokers.csv')")


def test_timeout_interrupts(db):
    with pytest.raises(QueryError, match="timed out"):
        db.query("SELECT COUNT(*) FROM deals a, deals b, deals c", timeout_s=0.5)


def test_explain_reports_bad_column(db):
    with pytest.raises(QueryError):
        db.explain("SELECT no_such_column FROM deals")


def test_to_jsonable():
    assert to_jsonable(dt.date(2025, 1, 2)) == "2025-01-02"
    assert to_jsonable(Decimal("1.5")) == 1.5
    assert to_jsonable(dt.time(10, 4, 49)) == "10:04:49"
    assert to_jsonable("x") == "x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Implement `app/db.py`**

```python
"""Read-only DuckDB access with per-query timeouts and row limits."""
import datetime as dt
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


class QueryError(Exception):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


class Database:
    def __init__(self, path: Path):
        self._con = duckdb.connect(str(path), read_only=True, config={"enable_external_access": False})

    def query(self, sql: str, params: list | None = None, timeout_s: float = 5.0,
              max_rows: int = 1000) -> QueryResult:
        cur = self._con.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        timer.start()
        try:
            cur.execute(sql, params or [])
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
        except duckdb.InterruptException as e:
            raise QueryError(f"query timed out after {timeout_s}s") from e
        except duckdb.Error as e:
            raise QueryError(str(e)) from e
        finally:
            timer.cancel()
            cur.close()
        return QueryResult(columns, rows[:max_rows], len(rows) > max_rows)

    def explain(self, sql: str) -> None:
        self.query("EXPLAIN " + sql)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.strftime("%H:%M:%S")
    return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: 7 passed. If `test_timeout_interrupts` hangs or raises a different exception class, check the installed duckdb version's interrupt exception name (`python -c "import duckdb; print(duckdb.__version__)"`) and adjust the `except` clause.

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/conftest.py tests/test_db.py
git commit -m "feat: read-only database access with timeouts"
```

---
### Task 5: Value resolver

**Files:**
- Create: `app/resolve.py`, `tests/test_resolve.py`

**Interfaces:**
- Consumes: `Database.query`, `app.text.normalize_name`, `normalize_text`
- Produces: `app.resolve.Candidate(kind: str, id: str, name: str, score: float, extra: dict)`; `Resolver(db: Database)`; `Resolver.resolve(kind: Literal["company","broker","product","region","contract"], text: str, limit: int = 10) -> list[Candidate]`. Candidate ids: company → INN; broker → broker code; product → `product_canonical` (kind `"product"`) or `product_family` (kind `"product_family"`); region → Uzbek region name as in `deals.region`; contract → contract number as string.

- [ ] **Step 1: Write the failing tests**

`tests/test_resolve.py`:
```python
import pytest

from app.resolve import Resolver


@pytest.fixture(scope="module")
def resolver(db):
    return Resolver(db)


def test_company_by_latin_name(resolver):
    top = resolver.resolve("company", "Pudexiz Plast")[0]
    assert top.id == "975923536"
    assert top.score >= 95


def test_company_by_cyrillic_name_typed_in_latin(resolver):
    top = resolver.resolve("company", "Harayom Kimyo")[0]
    assert top.id == "915688255"


def test_company_by_inn(resolver):
    [c] = resolver.resolve("company", "915688255")
    assert c.id == "915688255" and c.score == 100


def test_unknown_inn_returns_nothing(resolver):
    assert resolver.resolve("company", "123456789") == []


def test_broker_by_code_and_name(resolver):
    assert resolver.resolve("broker", "bk001")[0].id == "BK001"
    assert resolver.resolve("broker", "Kekuxo broker")[0].id == "BK001"
    assert resolver.resolve("broker", "BK999") == []


def test_product_russian_and_uzbek(resolver):
    ids = {c.id for c in resolver.resolve("product", "хлопок-сырец")}
    assert "Хлопок-сырец" in ids
    uz = resolver.resolve("product", "paxta")
    assert {"Хлопок-сырец", "Масло хлопковое"} <= {c.id for c in uz}
    assert any(c.kind == "product_family" and c.id == "Хлопок" for c in uz)


def test_product_absent(resolver):
    assert resolver.resolve("product", "золото") == []


def test_region_russian_to_uzbek(resolver):
    assert resolver.resolve("region", "Самаркандская область")[0].id == "Samarqand viloyati"
    assert resolver.resolve("region", "Fargona")[0].id == "Farg‘ona viloyati"


def test_contract_lookup(resolver):
    [c] = resolver.resolve("contract", "100001")
    assert c.id == "100001"
    assert c.extra["product_canonical"] == "Профиль алюминиевый"
    assert resolver.resolve("contract", "999999") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.resolve'`

- [ ] **Step 3: Implement `app/resolve.py`**

```python
"""Value resolution: map user-typed entity text to exact IDs stored in the database."""
import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process

from app.db import Database
from app.text import normalize_name, normalize_text

Kind = Literal["company", "broker", "product", "region", "contract"]
_CUTOFF = 75.0
_ALL = 100_000


@dataclass
class Candidate:
    kind: str
    id: str
    name: str
    score: float
    extra: dict = field(default_factory=dict)


class Resolver:
    def __init__(self, db: Database):
        self._db = db
        self._companies = db.query(
            "SELECT inn, name, name_norm, region, role, deals_as_buyer, deals_as_seller FROM participants",
            max_rows=_ALL).rows
        self._company_by_inn = {r[0]: r for r in self._companies}
        self._brokers = db.query(
            "SELECT broker_code, name, name_norm, region, deals_as_seller_broker, deals_as_buyer_broker FROM brokers",
            max_rows=_ALL).rows
        self._broker_by_code = {r[0]: r for r in self._brokers}
        self._product_rows = db.query(
            "SELECT product_name, product_canonical, product_family, synonyms FROM products", max_rows=_ALL).rows
        self._regions = db.query("SELECT region, region_ru, synonyms FROM regions", max_rows=_ALL).rows
        self._product_keys, self._product_targets = self._index_products()
        self._region_keys, self._region_targets = self._index_regions()

    def _index_products(self):
        keys, targets = [], []
        for raw, canonical, family, synonyms in self._product_rows:
            for text in [raw, canonical, *synonyms.split(";")]:
                keys.append(normalize_text(text))
                targets.append(("product", canonical, family))
            keys.append(normalize_text(family))
            targets.append(("product_family", family, family))
        return keys, targets

    def _index_regions(self):
        keys, targets = [], []
        for region, region_ru, synonyms in self._regions:
            for text in [region, region_ru, *synonyms.split(";")]:
                keys.append(normalize_text(text))
                targets.append(region)
        return keys, targets

    def resolve(self, kind: Kind, text: str, limit: int = 10) -> list[Candidate]:
        text = (text or "").strip()
        if not text:
            return []
        return {
            "company": self._company,
            "broker": self._broker,
            "product": self._product,
            "region": self._region,
            "contract": self._contract,
        }[kind](text, limit)

    def _company(self, text, limit):
        if re.fullmatch(r"\d{9}", text):
            row = self._company_by_inn.get(text)
            return [self._company_candidate(row, 100.0)] if row else []
        q = normalize_name(text)
        if not q:
            return []
        hits = process.extract(q, [r[2] for r in self._companies], scorer=fuzz.WRatio,
                               limit=limit, score_cutoff=_CUTOFF)
        cands = [self._company_candidate(self._companies[i], score) for _, score, i in hits]
        return sorted(cands, key=lambda c: (-c.score, -(c.extra["deals_as_buyer"] + c.extra["deals_as_seller"])))

    @staticmethod
    def _company_candidate(row, score):
        inn, name, _, region, role, buyer, seller = row
        return Candidate("company", inn, name, float(score),
                         {"region": region, "role": role, "deals_as_buyer": buyer, "deals_as_seller": seller})

    def _broker(self, text, limit):
        code = text.upper().replace(" ", "")
        if re.fullmatch(r"BK\d{3}", code):
            row = self._broker_by_code.get(code)
            return [self._broker_candidate(row, 100.0)] if row else []
        q = normalize_name(text)
        if not q:
            return []
        hits = process.extract(q, [r[2] for r in self._brokers], scorer=fuzz.WRatio,
                               limit=limit, score_cutoff=_CUTOFF)
        return [self._broker_candidate(self._brokers[i], score) for _, score, i in hits]

    @staticmethod
    def _broker_candidate(row, score):
        code, name, _, region, as_seller, as_buyer = row
        return Candidate("broker", code, name, float(score),
                         {"region": region, "deals_as_seller_broker": as_seller, "deals_as_buyer_broker": as_buyer})

    def _product(self, text, limit):
        q = normalize_text(text)
        if not q:
            return []
        hits = process.extract(q, self._product_keys, scorer=fuzz.WRatio, limit=None, score_cutoff=_CUTOFF)
        best: dict[tuple[str, str], Candidate] = {}
        for _, score, i in hits:
            kind, value, family = self._product_targets[i]
            key = (kind, value)
            if key not in best or score > best[key].score:
                best[key] = Candidate(kind, value, value, float(score), {"product_family": family})
        return sorted(best.values(), key=lambda c: -c.score)[:limit]

    def _region(self, text, limit):
        q = normalize_text(text)
        hits = process.extract(q, self._region_keys, scorer=fuzz.WRatio, limit=None, score_cutoff=_CUTOFF)
        best: dict[str, Candidate] = {}
        for _, score, i in hits:
            region = self._region_targets[i]
            if region not in best or score > best[region].score:
                best[region] = Candidate("region", region, region, float(score))
        return sorted(best.values(), key=lambda c: -c.score)[:limit]

    def _contract(self, text, limit):
        digits = re.sub(r"\D", "", text)
        if not digits:
            return []
        res = self._db.query(
            """SELECT c.contract_id, c.product_name, c.product_canonical, c.seller_inn, p.name
               FROM contracts c LEFT JOIN participants p ON p.inn = c.seller_inn WHERE c.contract_id = ?""",
            [int(digits)])
        return [Candidate("contract", str(r[0]), r[1], 100.0,
                          {"product_canonical": r[2], "seller_inn": r[3], "seller_name": r[4]}) for r in res.rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_resolve.py -v`
Expected: 9 passed. If `test_product_absent` fails because `золото` fuzzily matches something, print the hits and raise `_CUTOFF` for products only (keep companies at 75); do not delete the test.

- [ ] **Step 5: Commit**

```bash
git add app/resolve.py tests/test_resolve.py
git commit -m "feat: value resolver for companies, brokers, products, regions, contracts"
```

---
### Task 6: Query spec model and validation

The structured form Gemini fills instead of SQL. Parsing is strict: anything invalid becomes a `SpecError` whose message is sent back to the model.

**Files:**
- Create: `app/compiler/__init__.py` (empty), `app/compiler/spec.py`, `tests/test_spec.py`

**Interfaces:**
- Produces: `SpecError(ValueError)`; dataclasses `Filters`, `DealsAggregate`, `DealLookup`, `ContractLookup`, `UsdRate`, `RegistryCount`; `Spec` union; `parse_spec(d: dict) -> Spec`; `entity_values(spec: Spec) -> set[str]`; constants `MEASURES, DIMENSIONS, STATUSES, CONTRACT_TYPES, CONTRACT_FORMS, TRADE_TYPES, STATUS_SCOPES, REGISTRY_ENTITIES, ROLES, DATA_START, DATA_END, RATES_START, RATES_END`.

- [ ] **Step 1: Write the failing tests**

`tests/test_spec.py`:
```python
import datetime as dt

import pytest

from app.compiler.spec import (DealLookup, DealsAggregate, RegistryCount, SpecError, UsdRate,
                               entity_values, parse_spec)


def test_parse_aggregate_defaults_and_month_fill():
    s = parse_spec({"kind": "deals_aggregate", "measures": ["deal_count"],
                    "filters": {"year": 2025, "month_from": 10}})
    assert isinstance(s, DealsAggregate)
    assert s.filters.month_from == s.filters.month_to == 10
    assert s.status_scope == "split"
    assert s.group_by is None


def test_grouped_default_top_is_3():
    s = parse_spec({"kind": "deals_aggregate", "measures": ["volume_tonnes"], "group_by": "buyer",
                    "filters": {"products": ["Хлопок-сырец"]}})
    assert s.top == 3


@pytest.mark.parametrize("bad, message", [
    ({"measures": ["profit"]}, "measure"),
    ({"measures": []}, "measure"),
    ({"measures": ["deal_count", "amount_uzs", "amount_usd"]}, "at most 2"),
    ({"measures": ["deal_count"], "group_by": "ceo"}, "group_by"),
    ({"measures": ["deal_count"], "filters": {"year": 2021}}, "outside"),
    ({"measures": ["deal_count"], "filters": {"year": 2026, "month_from": 12}}, "outside"),
    ({"measures": ["deal_count"], "filters": {"buyer_inns": ["12345"]}}, "INN"),
    ({"measures": ["deal_count"], "filters": {"brokers": ["X1"]}}, "broker"),
    ({"measures": ["deal_count"], "filters": {"statuses": ["Done"]}}, "status"),
    ({"measures": ["deal_count"], "filters": {"date_from": "2021-12-01"}}, "outside"),
])
def test_invalid_aggregate(bad, message):
    with pytest.raises(SpecError, match=message):
        parse_spec({"kind": "deals_aggregate", **bad})


def test_parse_lookups():
    assert parse_spec({"kind": "deal_lookup", "deal_id": 387830}) == DealLookup(387830)
    assert parse_spec({"kind": "usd_rate", "date": "2025-06-15"}) == UsdRate(date=dt.date(2025, 6, 15))
    with pytest.raises(SpecError, match="outside"):
        parse_spec({"kind": "usd_rate", "date": "2026-12-01"})
    assert parse_spec({"kind": "registry_count", "entity": "brokers"}) == RegistryCount("brokers")
    with pytest.raises(SpecError, match="kind"):
        parse_spec({"kind": "forecast"})


def test_entity_values():
    s = parse_spec({"kind": "deals_aggregate", "measures": ["deal_count"],
                    "filters": {"products": ["Карбамид"], "regions": ["Samarqand viloyati"],
                                "buyer_inns": ["915688255"], "brokers": ["BK001"]}})
    assert entity_values(s) == {"Карбамид", "Samarqand viloyati", "915688255", "BK001"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.compiler'`

- [ ] **Step 3: Implement `app/compiler/spec.py`**

```python
"""Structured query specs filled by the LLM; validated strictly before compilation."""
import datetime as dt
import re
from dataclasses import dataclass, field

DATA_START, DATA_END = dt.date(2022, 1, 3), dt.date(2026, 10, 30)
RATES_START, RATES_END = dt.date(2022, 1, 1), dt.date(2026, 10, 31)

MEASURES = ("deal_count", "amount_uzs", "amount_usd", "volume_tonnes", "volume", "avg_amount_uzs",
            "avg_price_uzs_per_kg", "max_amount_uzs", "distinct_buyers", "distinct_sellers",
            "lots_bought", "lots_delivered", "cancelled_pct")
DIMENSIONS = ("buyer", "seller", "participant", "seller_broker", "buyer_broker", "broker", "region",
              "product", "product_family", "month", "year", "contract_type", "contract_form",
              "trade_type", "status", "unit", "contract")
STATUSES = ("Новый", "Не подписан", "Заключен", "Ждёт оплату", "Оплачен", "Исполнен",
            "Исполнен(Частично)", "Аннулирован(Не оплачен)")
CONTRACT_TYPES = ("Внутренний", "Экспортный")
CONTRACT_FORMS = ("Спот", "Форвард", "Фьючерс")
TRADE_TYPES = ("Английский аукцион", "Обратный аукцион", "Двусторонний аукцион")
STATUS_SCOPES = ("all", "valid", "split")
REGISTRY_ENTITIES = ("participants", "brokers", "contracts")
ROLES = ("Покупатель", "Продавец")


class SpecError(ValueError):
    pass


@dataclass
class Filters:
    year: int | None = None
    month_from: int | None = None
    month_to: int | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    products: list[str] = field(default_factory=list)
    product_family: str | None = None
    regions: list[str] = field(default_factory=list)
    buyer_inns: list[str] = field(default_factory=list)
    seller_inns: list[str] = field(default_factory=list)
    participant_inns: list[str] = field(default_factory=list)
    seller_brokers: list[str] = field(default_factory=list)
    buyer_brokers: list[str] = field(default_factory=list)
    brokers: list[str] = field(default_factory=list)
    contract_id: int | None = None
    contract_type: str | None = None
    contract_form: str | None = None
    trade_type: str | None = None
    statuses: list[str] = field(default_factory=list)


@dataclass
class DealsAggregate:
    measures: list[str]
    filters: Filters = field(default_factory=Filters)
    group_by: str | None = None
    top: int | None = None
    status_scope: str = "split"


@dataclass
class DealLookup:
    deal_id: int


@dataclass
class ContractLookup:
    contract_id: int


@dataclass
class UsdRate:
    date: dt.date | None = None
    year: int | None = None
    month: int | None = None


@dataclass
class RegistryCount:
    entity: str
    role: str | None = None
    vat_payer: bool | None = None
    region: str | None = None
    registered_year: int | None = None


Spec = DealsAggregate | DealLookup | ContractLookup | UsdRate | RegistryCount


def _opt_int(d: dict, key: str) -> int | None:
    v = d.get(key)
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise SpecError(f"{key} must be an integer, got {v!r}")


def _opt_date(d: dict, key: str) -> dt.date | None:
    v = d.get(key)
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        raise SpecError(f"{key} must be YYYY-MM-DD, got {v!r}")


def _str_list(d: dict, key: str) -> list[str]:
    v = d.get(key) or []
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in v if str(x).strip()]


def _enum(value, allowed, name):
    if value not in allowed:
        raise SpecError(f"invalid {name} {value!r}; allowed: {', '.join(allowed)}")
    return value


def _opt_enum(d: dict, key: str, allowed) -> str | None:
    v = d.get(key)
    return None if v in (None, "") else _enum(v, allowed, key)


def _outside(what: str) -> SpecError:
    return SpecError(f"{what} is outside the data (deals 03.01.2022–30.10.2026, USD rates "
                     f"01.01.2022–31.10.2026); call declare_unanswerable")


def _check_year(year: int | None) -> None:
    if year is not None and not 2022 <= year <= 2026:
        raise _outside(f"year {year}")


def _parse_filters(d: dict) -> Filters:
    f = Filters(
        year=_opt_int(d, "year"), month_from=_opt_int(d, "month_from"), month_to=_opt_int(d, "month_to"),
        date_from=_opt_date(d, "date_from"), date_to=_opt_date(d, "date_to"),
        products=_str_list(d, "products"), product_family=d.get("product_family") or None,
        regions=_str_list(d, "regions"),
        buyer_inns=_str_list(d, "buyer_inns"), seller_inns=_str_list(d, "seller_inns"),
        participant_inns=_str_list(d, "participant_inns"),
        seller_brokers=[b.upper() for b in _str_list(d, "seller_brokers")],
        buyer_brokers=[b.upper() for b in _str_list(d, "buyer_brokers")],
        brokers=[b.upper() for b in _str_list(d, "brokers")],
        contract_id=_opt_int(d, "contract_id"),
        contract_type=_opt_enum(d, "contract_type", CONTRACT_TYPES),
        contract_form=_opt_enum(d, "contract_form", CONTRACT_FORMS),
        trade_type=_opt_enum(d, "trade_type", TRADE_TYPES),
        statuses=[_enum(s, STATUSES, "status") for s in _str_list(d, "statuses")],
    )
    _check_year(f.year)
    if f.month_from is None and f.month_to is not None:
        f.month_from = f.month_to
    if f.month_to is None and f.month_from is not None:
        f.month_to = f.month_from
    for m in (f.month_from, f.month_to):
        if m is not None and not 1 <= m <= 12:
            raise SpecError(f"month {m} must be 1..12")
    if f.month_from is not None and f.month_from > f.month_to:
        raise SpecError("month_from must be <= month_to")
    if f.year == 2026 and f.month_from is not None and f.month_from > 10:
        raise _outside(f"{f.month_from:02d}.2026")
    for d_ in (f.date_from, f.date_to):
        if d_ is not None and not DATA_START <= d_ <= DATA_END:
            raise _outside(d_.isoformat())
    for inn in f.buyer_inns + f.seller_inns + f.participant_inns:
        if not re.fullmatch(r"\d{9}", inn):
            raise SpecError(f"INN must be 9 digits, got {inn!r}")
    for b in f.seller_brokers + f.buyer_brokers + f.brokers:
        if not re.fullmatch(r"BK\d{3}", b):
            raise SpecError(f"broker code must look like BK001, got {b!r}")
    return f


def _parse_aggregate(d: dict) -> DealsAggregate:
    measures = _str_list(d, "measures")
    if not measures:
        raise SpecError("at least one measure is required")
    if len(measures) > 2:
        raise SpecError("at most 2 measures per query")
    for m in measures:
        _enum(m, MEASURES, "measure")
    group_by = d.get("group_by") or None
    if group_by is not None:
        _enum(group_by, DIMENSIONS, "group_by")
    top = _opt_int(d, "top")
    if group_by and top is None and group_by not in ("month", "year"):
        top = 3
    if top is not None and not 1 <= top <= 20:
        raise SpecError("top must be 1..20")
    scope = d.get("status_scope") or "split"
    _enum(scope, STATUS_SCOPES, "status_scope")
    return DealsAggregate(measures, _parse_filters(d.get("filters") or {}), group_by, top, scope)


def parse_spec(d: dict) -> Spec:
    kind = d.get("kind")
    if kind == "deals_aggregate":
        return _parse_aggregate(d)
    if kind == "deal_lookup":
        deal_id = _opt_int(d, "deal_id")
        if deal_id is None:
            raise SpecError("deal_id is required")
        return DealLookup(deal_id)
    if kind == "contract_lookup":
        contract_id = _opt_int(d, "contract_id")
        if contract_id is None:
            raise SpecError("contract_id is required")
        return ContractLookup(contract_id)
    if kind == "usd_rate":
        spec = UsdRate(_opt_date(d, "date"), _opt_int(d, "year"), _opt_int(d, "month"))
        if spec.date is not None and not RATES_START <= spec.date <= RATES_END:
            raise _outside(spec.date.isoformat())
        _check_year(spec.year)
        if spec.year == 2026 and spec.month is not None and spec.month > 10:
            raise _outside(f"{spec.month:02d}.2026")
        if spec.date is None and spec.year is None:
            raise SpecError("usd_rate needs date or year")
        return spec
    if kind == "registry_count":
        entity = _enum(d.get("entity"), REGISTRY_ENTITIES, "entity")
        role = _opt_enum(d, "role", ROLES)
        vat = d.get("vat_payer")
        year = _opt_int(d, "registered_year")
        _check_year(year)
        return RegistryCount(entity, role, None if vat is None else bool(vat), d.get("region") or None, year)
    raise SpecError(f"unknown kind {kind!r}; use deals_aggregate, deal_lookup, contract_lookup, "
                    "usd_rate or registry_count")


def entity_values(spec: Spec) -> set[str]:
    """Values that must have been returned by resolve() in this request."""
    if isinstance(spec, DealsAggregate):
        f = spec.filters
        values = set(f.products + f.regions + f.buyer_inns + f.seller_inns + f.participant_inns
                     + f.seller_brokers + f.buyer_brokers + f.brokers)
        if f.product_family:
            values.add(f.product_family)
        return values
    if isinstance(spec, RegistryCount) and spec.region:
        return {spec.region}
    return set()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_spec.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add app/compiler tests/test_spec.py
git commit -m "feat: structured query spec with strict validation"
```

---
### Task 7: Spec → SQL compiler

**Files:**
- Create: `app/compiler/sql.py`, `tests/test_compiler.py`

**Interfaces:**
- Consumes: `app.compiler.spec.*`, `Database` (tests only)
- Produces: `Compiled(sql: str, splits: list[str], notes: list[str])`; `compile_spec(spec: Spec) -> Compiled`. `splits` ⊆ registry axes (`status_scope`, `measure`, `role`, `currency`). `notes` are machine codes rendered by Task 8: `excluded_cancelled`, `all_statuses`, `status_split`, `uzs_converted`, `usd_converted`, `broker_both_sides`, `role_split`, `volume_by_unit`, `ranked_top_<n>`.

Column-naming rules (the composer and verifier rely on them): measure aliases `deals, total_uzs, total_usd, volume_t, volume, avg_amount_uzs, avg_price_uzs_per_kg, max_amount_uzs, buyers, sellers, lots_bought, lots_delivered, cancelled_pct`; with `status_scope="split"` each gets `_all` / `_valid` suffixes.

- [ ] **Step 1: Write the failing tests**

`tests/test_compiler.py`:
```python
import pytest

from app.compiler.spec import parse_spec
from app.compiler.sql import compile_spec


def run(db, spec_dict):
    compiled = compile_spec(parse_spec(spec_dict))
    return compiled, db.query(compiled.sql)


def scalar(db, sql):
    return db.query(sql).rows[0][0]


def test_count_with_status_split(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count"],
                      "filters": {"year": 2025, "month_from": 10}})
    assert res.columns == ["deals_all", "deals_valid"]
    assert res.rows[0][0] == scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2025 AND month=10")
    assert res.rows[0][1] == scalar(db, "SELECT COUNT(*) FROM deals WHERE year=2025 AND month=10 AND NOT is_cancelled")
    assert c.splits == ["status_scope"]


def test_top_buyer_of_raw_cotton_by_volume_and_value(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["volume_tonnes", "amount_uzs"],
                      "group_by": "buyer", "top": 1, "filters": {"products": ["Хлопок-сырец"]}})
    assert res.columns[:3] == ["ranking", "buyer_inn", "buyer_name"]
    by = {r[0]: r for r in res.rows}
    assert set(by) == {"by_volume_t", "by_total_uzs"}
    expected = scalar(db, """SELECT buyer_inn FROM deals WHERE product_canonical='Хлопок-сырец' AND NOT is_cancelled
                             GROUP BY 1 ORDER BY SUM(volume_tonnes) DESC LIMIT 1""")
    assert by["by_volume_t"][1] == expected
    assert "measure" in c.splits and "excluded_cancelled" in c.notes


def test_participant_role_split(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "participant",
                      "top": 2, "filters": {"year": 2025}})
    assert res.columns[:3] == ["role", "inn", "name"]
    assert sorted(r[0] for r in res.rows) == ["buyer", "buyer", "seller", "seller"]
    assert "role" in c.splits


def test_broker_either_side(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "broker", "top": 1})
    expected = scalar(db, """WITH s AS (SELECT seller_broker b, amount_uzs FROM deals WHERE NOT is_cancelled
                                       UNION ALL SELECT buyer_broker, amount_uzs FROM deals WHERE NOT is_cancelled)
                             SELECT b FROM s GROUP BY b ORDER BY SUM(amount_uzs) DESC LIMIT 1""")
    assert res.rows[0][0] == expected


def test_volume_is_grouped_by_unit(db):
    c, res = run(db, {"kind": "deals_aggregate", "measures": ["volume"],
                      "filters": {"products": ["Дизельное топливо"], "year": 2023}, "status_scope": "valid"})
    assert res.columns[0] == "unit"
    assert "volume_by_unit" in c.notes


def test_month_series_ordered(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count"], "group_by": "month",
                      "filters": {"year": 2024}, "status_scope": "valid"})
    assert [r[1] for r in res.rows] == list(range(1, 13))


def test_broker_filter_either_side(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["deal_count"], "status_scope": "all",
                      "filters": {"year": 2025, "brokers": ["BK001"]}})
    assert res.rows[0][0] == scalar(db, """SELECT COUNT(*) FROM deals WHERE year=2025
                                          AND (seller_broker='BK001' OR buyer_broker='BK001')""")


def test_cancelled_pct(db):
    _, res = run(db, {"kind": "deals_aggregate", "measures": ["cancelled_pct"], "filters": {"year": 2025}})
    assert res.columns == ["cancelled_pct"]
    assert 0 < res.rows[0][0] < 100


def test_lookups(db):
    _, res = run(db, {"kind": "deal_lookup", "deal_id": 387830})
    row = dict(zip(res.columns, res.rows[0]))
    assert row["buyer_broker"] == "BK106" and row["contract_id"] == 102267
    _, res = run(db, {"kind": "contract_lookup", "contract_id": 100001})
    row = dict(zip(res.columns, res.rows[0]))
    assert row["seller_inn"] == "953758401" and row["product_canonical"] == "Профиль алюминиевый"
    _, res = run(db, {"kind": "usd_rate", "date": "2026-10-30"})
    assert res.rows[0][1] == pytest.approx(13369.54)
    _, res = run(db, {"kind": "usd_rate", "year": 2024, "month": 3})
    assert res.columns == ["avg_rate", "min_rate", "max_rate", "days"]
    _, res = run(db, {"kind": "registry_count", "entity": "brokers"})
    assert res.rows == [(120,)]
    _, res = run(db, {"kind": "registry_count", "entity": "participants", "role": "Продавец"})
    assert res.rows == [(392,)]


def test_literals_are_escaped():
    c = compile_spec(parse_spec({"kind": "deals_aggregate", "measures": ["deal_count"],
                                 "filters": {"products": ["x' OR 1=1 --"]}}))
    assert "'x'' OR 1=1 --'" in c.sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compiler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.compiler.sql'`

- [ ] **Step 3: Implement `app/compiler/sql.py`**

```python
"""Compile validated specs into SQL from fixed templates. All SQL here is reviewed and unit-tested."""
from dataclasses import dataclass, field

from app.compiler.spec import (ContractLookup, DealLookup, DealsAggregate, Filters, RegistryCount, Spec,
                               UsdRate)

_VALID = " FILTER (WHERE NOT d.is_cancelled)"

# measure -> (expression template with {f} = optional FILTER clause, alias)
MEASURE_SQL = {
    "deal_count": ("COUNT(*){f}", "deals"),
    "amount_uzs": ("SUM(d.amount_uzs){f}", "total_uzs"),
    "amount_usd": ("SUM(d.amount_usd){f}", "total_usd"),
    "volume_tonnes": ("SUM(d.volume_tonnes){f}", "volume_t"),
    "volume": ("SUM(d.volume){f}", "volume"),
    "avg_amount_uzs": ("AVG(d.amount_uzs){f}", "avg_amount_uzs"),
    "avg_price_uzs_per_kg": ("SUM(d.amount_uzs){f} / NULLIF(SUM(d.volume_tonnes){f} * 1000, 0)",
                             "avg_price_uzs_per_kg"),
    "max_amount_uzs": ("MAX(d.amount_uzs){f}", "max_amount_uzs"),
    "distinct_buyers": ("COUNT(DISTINCT d.buyer_inn){f}", "buyers"),
    "distinct_sellers": ("COUNT(DISTINCT d.seller_inn){f}", "sellers"),
    "lots_bought": ("SUM(d.quantity){f}", "lots_bought"),
    "lots_delivered": ("SUM(d.delivered_lots){f}", "lots_delivered"),
    "cancelled_pct": ("ROUND(100.0 * COUNT(*) FILTER (WHERE d.is_cancelled) / COUNT(*), 2)", "cancelled_pct"),
}


@dataclass(frozen=True)
class Dim:
    select: tuple[str, ...]   # "expr AS alias"
    group: tuple[str, ...]
    join: str = ""
    order_asc: bool = False   # time series: order by key ascending


DIMS = {
    "buyer": Dim(("d.buyer_inn AS buyer_inn", "pb.name AS buyer_name"), ("d.buyer_inn", "pb.name"),
                 "LEFT JOIN participants pb ON pb.inn = d.buyer_inn"),
    "seller": Dim(("d.seller_inn AS seller_inn", "ps.name AS seller_name"), ("d.seller_inn", "ps.name"),
                  "LEFT JOIN participants ps ON ps.inn = d.seller_inn"),
    "seller_broker": Dim(("d.seller_broker AS seller_broker", "bs.name AS seller_broker_name"),
                         ("d.seller_broker", "bs.name"), "LEFT JOIN brokers bs ON bs.broker_code = d.seller_broker"),
    "buyer_broker": Dim(("d.buyer_broker AS buyer_broker", "bb.name AS buyer_broker_name"),
                        ("d.buyer_broker", "bb.name"), "LEFT JOIN brokers bb ON bb.broker_code = d.buyer_broker"),
    "broker": Dim(("d.broker_code AS broker_code", "bk.name AS broker_name"), ("d.broker_code", "bk.name"),
                  "LEFT JOIN brokers bk ON bk.broker_code = d.broker_code"),
    "region": Dim(("d.region AS region",), ("d.region",)),
    "product": Dim(("d.product_canonical AS product",), ("d.product_canonical",)),
    "product_family": Dim(("d.product_family AS product_family",), ("d.product_family",)),
    "month": Dim(("d.year AS year", "d.month AS month"), ("d.year", "d.month"), order_asc=True),
    "year": Dim(("d.year AS year",), ("d.year",), order_asc=True),
    "contract_type": Dim(("d.contract_type AS contract_type",), ("d.contract_type",)),
    "contract_form": Dim(("d.contract_form AS contract_form",), ("d.contract_form",)),
    "trade_type": Dim(("d.trade_type AS trade_type",), ("d.trade_type",)),
    "status": Dim(("d.status AS status",), ("d.status",)),
    "unit": Dim(("d.unit AS unit",), ("d.unit",)),
    "contract": Dim(("d.contract_id AS contract_id",), ("d.contract_id",)),
}
_UNIT = Dim(("d.unit AS unit",), ("d.unit",))


@dataclass
class Compiled:
    sql: str
    splits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(lit(v) for v in values)})"


def where_clauses(f: Filters, a: str = "d") -> list[str]:
    w = []
    if f.year is not None:
        w.append(f"{a}.year = {int(f.year)}")
    if f.month_from is not None:
        w.append(f"{a}.month BETWEEN {int(f.month_from)} AND {int(f.month_to)}")
    if f.date_from is not None:
        w.append(f"{a}.deal_date >= DATE {lit(f.date_from.isoformat())}")
    if f.date_to is not None:
        w.append(f"{a}.deal_date <= DATE {lit(f.date_to.isoformat())}")
    if f.products:
        w.append(_in(f"{a}.product_canonical", f.products))
    if f.product_family:
        w.append(f"{a}.product_family = {lit(f.product_family)}")
    if f.regions:
        w.append(_in(f"{a}.region", f.regions))
    if f.buyer_inns:
        w.append(_in(f"{a}.buyer_inn", f.buyer_inns))
    if f.seller_inns:
        w.append(_in(f"{a}.seller_inn", f.seller_inns))
    if f.participant_inns:
        w.append(f"({_in(f'{a}.buyer_inn', f.participant_inns)} OR {_in(f'{a}.seller_inn', f.participant_inns)})")
    if f.seller_brokers:
        w.append(_in(f"{a}.seller_broker", f.seller_brokers))
    if f.buyer_brokers:
        w.append(_in(f"{a}.buyer_broker", f.buyer_brokers))
    if f.brokers:
        w.append(f"({_in(f'{a}.seller_broker', f.brokers)} OR {_in(f'{a}.buyer_broker', f.brokers)})")
    if f.contract_id is not None:
        w.append(f"{a}.contract_id = {int(f.contract_id)}")
    if f.contract_type:
        w.append(f"{a}.contract_type = {lit(f.contract_type)}")
    if f.contract_form:
        w.append(f"{a}.contract_form = {lit(f.contract_form)}")
    if f.trade_type:
        w.append(f"{a}.trade_type = {lit(f.trade_type)}")
    if f.statuses:
        w.append(_in(f"{a}.status", f.statuses))
    return w


def _measure_cols(measures: list[str], split: bool) -> list[str]:
    cols = []
    for m in measures:
        expr, alias = MEASURE_SQL[m]
        if split and m != "cancelled_pct":
            cols.append(f"{expr.format(f='')} AS {alias}_all")
            cols.append(f"{expr.format(f=_VALID)} AS {alias}_valid")
        else:
            cols.append(f"{expr.format(f='')} AS {alias}")
    return cols


def _currency_notes(measures: list[str]) -> list[str]:
    notes = []
    if any(m in ("amount_uzs", "avg_amount_uzs", "avg_price_uzs_per_kg", "max_amount_uzs") for m in measures):
        notes.append("uzs_converted")
    if "amount_usd" in measures:
        notes.append("usd_converted")
    return notes


def _compile_aggregate(s: DealsAggregate) -> Compiled:
    notes = _currency_notes(s.measures)
    splits = []
    if "amount_uzs" in s.measures and "amount_usd" in s.measures:
        splits.append("currency")
    where = where_clauses(s.filters)
    grouped = s.group_by is not None
    scope = s.status_scope
    if grouped and scope == "split":
        scope = "valid"          # rankings are computed on deals that took place
    if s.filters.statuses:
        scope = "all"            # explicit status filter overrides the default
    if scope == "valid":
        where.append("NOT d.is_cancelled")
        notes.append("excluded_cancelled")
    elif scope == "all":
        notes.append("all_statuses")
    else:
        notes.append("status_split")
        splits.append("status_scope")
    split_cols = scope == "split"
    dims: list[Dim] = []
    if grouped:
        dims.append(DIMS[s.group_by])
    if "volume" in s.measures:
        dims.append(_UNIT)
        notes.append("volume_by_unit")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    measure_cols = _measure_cols(s.measures, split_cols)

    if not dims:
        return Compiled(f"SELECT {', '.join(measure_cols)} FROM deals d{where_sql}", splits, notes)

    if s.group_by == "participant":
        splits.append("role")
        notes.append("role_split")
        parts = []
        for role, dim in (("buyer", "buyer_inn"), ("seller", "seller_inn")):
            alias = "pb" if role == "buyer" else "ps"
            parts.append(_ranked_select(
                [f"'{role}' AS role", f"d.{dim} AS inn", f"{alias}.name AS name"] + measure_cols,
                f"FROM deals d LEFT JOIN participants {alias} ON {alias}.inn = d.{dim}{where_sql}",
                [f"d.{dim}", f"{alias}.name"], s, rank_alias=_rank_alias(s.measures[0], split_cols)))
        notes.append(f"ranked_top_{s.top}")
        return Compiled(" UNION ALL ".join(parts), splits, notes)

    source = f"FROM deals d"
    if s.group_by == "broker":
        notes.append("broker_both_sides")
        inner = where_clauses(s.filters, "x") + (["NOT x.is_cancelled"] if scope == "valid" else [])
        inner_where = (" WHERE " + " AND ".join(inner)) if inner else ""
        source = ("FROM (SELECT x.*, x.seller_broker AS broker_code FROM deals x" + inner_where +
                  " UNION ALL SELECT x.*, x.buyer_broker AS broker_code FROM deals x" + inner_where + ") d")
        where_sql = ""
    joins = " ".join(dim.join for dim in dims if dim.join)
    select = [c for dim in dims for c in dim.select]
    group = [g for dim in dims for g in dim.group]
    from_sql = f"{source} {joins}{where_sql}".replace("  ", " ")

    if dims[0].order_asc and s.top is None:
        sql = (f"SELECT {', '.join(select + measure_cols)} {from_sql} GROUP BY {', '.join(group)} "
               f"ORDER BY {', '.join(group)}")
        return Compiled(sql, splits, notes)

    if not grouped:          # volume-by-unit only
        sql = f"SELECT {', '.join(select + measure_cols)} {from_sql} GROUP BY {', '.join(group)} ORDER BY 1"
        return Compiled(sql, splits, notes)

    notes.append(f"ranked_top_{s.top}")
    if len(s.measures) == 2:
        splits.append("measure")
        parts = []
        for m in s.measures:
            rank = _rank_alias(m, split_cols)
            parts.append(_ranked_select([f"'by_{rank}' AS ranking"] + select + measure_cols, from_sql, group, s,
                                        rank_alias=rank))
        return Compiled(" UNION ALL ".join(parts), splits, notes)
    sql = _ranked_select(select + measure_cols, from_sql, group, s, _rank_alias(s.measures[0], split_cols))
    return Compiled(sql, splits, notes)


def _rank_alias(measure: str, split: bool) -> str:
    alias = MEASURE_SQL[measure][1]
    return f"{alias}_valid" if split and measure != "cancelled_pct" else alias


def _ranked_select(cols: list[str], from_sql: str, group: list[str], s: DealsAggregate, rank_alias: str) -> str:
    return (f"(SELECT {', '.join(cols)} {from_sql} GROUP BY {', '.join(group)} "
            f"ORDER BY {rank_alias} DESC NULLS LAST LIMIT {int(s.top)})")


def _compile_deal(s: DealLookup) -> Compiled:
    return Compiled(f"""SELECT d.deal_id, d.deal_date, d.deal_time, d.product_name_full, d.product_canonical,
       d.quantity, d.lot, d.unit, d.volume, d.amount, d.currency, d.amount_uzs, d.start_price, d.status,
       d.contract_id, d.contract_type, d.contract_form, d.trade_type, d.region,
       d.seller_inn, ps.name AS seller_name, d.buyer_inn, pb.name AS buyer_name,
       d.seller_broker, bs.name AS seller_broker_name, d.buyer_broker, bb.name AS buyer_broker_name,
       d.payment_date, d.delivery_date, d.delivered_lots
FROM deals d
LEFT JOIN participants ps ON ps.inn = d.seller_inn LEFT JOIN participants pb ON pb.inn = d.buyer_inn
LEFT JOIN brokers bs ON bs.broker_code = d.seller_broker LEFT JOIN brokers bb ON bb.broker_code = d.buyer_broker
WHERE d.deal_id = {int(s.deal_id)}""")


def _compile_contract(s: ContractLookup) -> Compiled:
    cid = int(s.contract_id)
    return Compiled(f"""SELECT c.contract_id, c.product_name, c.product_canonical, c.unit, c.lot, c.currency,
       c.base_price, c.region, c.contract_type, c.contract_form, c.trade_type, c.payment_term_days,
       c.delivery_term_days, c.registered_date, c.valid_until, c.seller_inn, p.name AS seller_name,
       (SELECT COUNT(*) FROM deals d WHERE d.contract_id = {cid}) AS deals_all,
       (SELECT COUNT(*) FROM deals d WHERE d.contract_id = {cid} AND NOT d.is_cancelled) AS deals_valid,
       (SELECT string_agg(DISTINCT d.seller_broker, ', ' ORDER BY d.seller_broker)
          FROM deals d WHERE d.contract_id = {cid}) AS seller_brokers,
       (SELECT string_agg(DISTINCT d.buyer_broker, ', ' ORDER BY d.buyer_broker)
          FROM deals d WHERE d.contract_id = {cid}) AS buyer_brokers
FROM contracts c LEFT JOIN participants p ON p.inn = c.seller_inn
WHERE c.contract_id = {cid}""")


def _compile_rate(s: UsdRate) -> Compiled:
    if s.date is not None:
        return Compiled(f"SELECT rate_date, rate FROM usd_rates WHERE rate_date = DATE {lit(s.date.isoformat())}")
    where = [f"year(rate_date) = {int(s.year)}"]
    if s.month is not None:
        where.append(f"month(rate_date) = {int(s.month)}")
    return Compiled("SELECT AVG(rate) AS avg_rate, MIN(rate) AS min_rate, MAX(rate) AS max_rate, "
                    f"COUNT(*) AS days FROM usd_rates WHERE {' AND '.join(where)}")


def _compile_registry(s: RegistryCount) -> Compiled:
    where = []
    if s.entity == "participants":
        if s.role:
            where.append(f"role = {lit(s.role)}")
        if s.vat_payer is not None:
            where.append("vat_payer" if s.vat_payer else "NOT vat_payer")
    if s.region:
        where.append(f"region = {lit(s.region)}")
    if s.entity == "contracts" and s.registered_year is not None:
        where.append(f"year(registered_date) = {int(s.registered_year)}")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    return Compiled(f"SELECT COUNT(*) AS {s.entity} FROM {s.entity}{where_sql}")


def compile_spec(spec: Spec) -> Compiled:
    if isinstance(spec, DealsAggregate):
        return _compile_aggregate(spec)
    if isinstance(spec, DealLookup):
        return _compile_deal(spec)
    if isinstance(spec, ContractLookup):
        return _compile_contract(spec)
    if isinstance(spec, UsdRate):
        return _compile_rate(spec)
    if isinstance(spec, RegistryCount):
        return _compile_registry(spec)
    raise TypeError(f"unsupported spec {type(spec).__name__}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compiler.py -v`
Expected: 10 passed. For any failure, print `compiled.sql` and run it in `duckdb build/uzex.duckdb` to see the real error; fix the template, not the test's expected value (the expected values come from independent hand-written SQL).

- [ ] **Step 5: Commit**

```bash
git add app/compiler/sql.py tests/test_compiler.py
git commit -m "feat: compile structured specs to reviewed SQL templates"
```

---
### Task 8: Deterministic explanation of what was computed

Appended by code (not the LLM) to every compiled answer, so the interpretation is always visible.

**Files:**
- Create: `app/compiler/explain.py`, `tests/test_explain.py`

**Interfaces:**
- Consumes: `Spec`, `Compiled.notes`
- Produces: `explain(spec: Spec, notes: list[str], lang: str) -> str` (one sentence; `lang` ∈ `ru`, `uz`, `uz-Cyrl`; `uz-Cyrl` uses the Latin Uzbek text).

- [ ] **Step 1: Write the failing tests**

`tests/test_explain.py`:
```python
from app.compiler.explain import explain
from app.compiler.spec import parse_spec
from app.compiler.sql import compile_spec


def _explain(d, lang):
    spec = parse_spec(d)
    return explain(spec, compile_spec(spec).notes, lang)


def test_ru_count_split():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                     "filters": {"year": 2025, "month_from": 10}}, "ru")
    assert text.startswith("Расчёт:")
    assert "10.2025" in text
    assert "все сделки и отдельно без аннулированных" in text


def test_uz_ranking_with_product():
    text = _explain({"kind": "deals_aggregate", "measures": ["volume_tonnes"], "group_by": "buyer",
                     "filters": {"products": ["Хлопок-сырец"]}}, "uz")
    assert text.startswith("Hisob:")
    assert "Хлопок-сырец" in text
    assert "bekor qilingan bitimlar hisobga olinmagan" in text
    assert "eng yaxshi 3 ta" in text


def test_currency_note():
    text = _explain({"kind": "deals_aggregate", "measures": ["amount_usd"], "status_scope": "valid"}, "ru")
    assert "по курсу на дату сделки" in text


def test_lookup_has_no_explanation():
    assert _explain({"kind": "deal_lookup", "deal_id": 1}, "ru") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_explain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.compiler.explain'`

- [ ] **Step 3: Implement `app/compiler/explain.py`**

```python
"""One-line, deterministic description of what a compiled query computed."""
from app.compiler.spec import DealsAggregate, Filters, Spec

_T = {
    "ru": {
        "prefix": "Расчёт:", "deals": "сделки", "all_time": "за весь период (03.01.2022–30.10.2026)",
        "period_year": "за {y} год", "period_month": "за {m}", "period_months": "за {m1}–{m2}",
        "period_dates": "с {d1} по {d2}", "product": "товар: {v}", "family": "группа товаров: {v}",
        "region": "регион: {v}", "buyer": "покупатель ИНН {v}", "seller": "продавец ИНН {v}",
        "participant": "участник ИНН {v}", "broker": "брокер {v} (любая сторона)",
        "seller_broker": "брокер продавца {v}", "buyer_broker": "брокер покупателя {v}",
        "contract": "контракт {v}", "contract_type": "тип контракта: {v}", "contract_form": "форма: {v}",
        "trade_type": "тип торгов: {v}", "status": "состояние: {v}",
        "excluded_cancelled": "без аннулированных сделок", "all_statuses": "все состояния сделок",
        "status_split": "все сделки и отдельно без аннулированных",
        "uzs_converted": "суммы в сумах (экспортные сделки пересчитаны по курсу на дату сделки)",
        "usd_converted": "суммы в долларах (сумовые сделки пересчитаны по курсу на дату сделки)",
        "broker_both_sides": "брокер учитывается и со стороны продавца, и со стороны покупателя",
        "role_split": "отдельно как покупатель и как продавец",
        "volume_by_unit": "объём по единицам измерения", "ranked_top": "топ-{n}",
    },
    "uz": {
        "prefix": "Hisob:", "deals": "bitimlar", "all_time": "butun davr uchun (03.01.2022–30.10.2026)",
        "period_year": "{y}-yil", "period_month": "{m}", "period_months": "{m1}–{m2}",
        "period_dates": "{d1} dan {d2} gacha", "product": "tovar: {v}", "family": "tovar guruhi: {v}",
        "region": "hudud: {v}", "buyer": "xaridor STIR {v}", "seller": "sotuvchi STIR {v}",
        "participant": "ishtirokchi STIR {v}", "broker": "broker {v} (istalgan tomon)",
        "seller_broker": "sotuvchi brokeri {v}", "buyer_broker": "xaridor brokeri {v}",
        "contract": "shartnoma {v}", "contract_type": "shartnoma turi: {v}", "contract_form": "shakli: {v}",
        "trade_type": "savdo turi: {v}", "status": "holati: {v}",
        "excluded_cancelled": "bekor qilingan bitimlar hisobga olinmagan", "all_statuses": "barcha holatdagi bitimlar",
        "status_split": "barcha bitimlar va alohida bekor qilinganlarsiz",
        "uzs_converted": "summalar so‘mda (eksport bitimlari bitim sanasidagi kurs bo‘yicha)",
        "usd_converted": "summalar dollarda (so‘mdagi bitimlar bitim sanasidagi kurs bo‘yicha)",
        "broker_both_sides": "broker sotuvchi va xaridor tomonida ham hisobga olingan",
        "role_split": "xaridor va sotuvchi sifatida alohida",
        "volume_by_unit": "hajm o‘lchov birliklari bo‘yicha", "ranked_top": "eng yaxshi {n} ta",
    },
}


def _period(f: Filters, t: dict) -> str:
    if f.date_from or f.date_to:
        d1 = (f.date_from.strftime("%d.%m.%Y") if f.date_from else "03.01.2022")
        d2 = (f.date_to.strftime("%d.%m.%Y") if f.date_to else "30.10.2026")
        return t["period_dates"].format(d1=d1, d2=d2)
    if f.year is None and f.month_from is None:
        return t["all_time"]
    if f.month_from is None:
        return t["period_year"].format(y=f.year)
    suffix = f".{f.year}" if f.year is not None else ""
    if f.month_from == f.month_to:
        return t["period_month"].format(m=f"{f.month_from:02d}{suffix}")
    return t["period_months"].format(m1=f"{f.month_from:02d}{suffix}", m2=f"{f.month_to:02d}{suffix}")


def explain(spec: Spec, notes: list[str], lang: str) -> str:
    if not isinstance(spec, DealsAggregate):
        return ""
    t = _T["ru"] if lang == "ru" else _T["uz"]
    f = spec.filters
    parts = [f"{t['deals']} {_period(f, t)}"]
    for key, values in (("product", f.products), ("region", f.regions), ("buyer", f.buyer_inns),
                        ("seller", f.seller_inns), ("participant", f.participant_inns), ("broker", f.brokers),
                        ("seller_broker", f.seller_brokers), ("buyer_broker", f.buyer_brokers),
                        ("status", f.statuses)):
        if values:
            parts.append(t[key].format(v=", ".join(values)))
    for key, value in (("family", f.product_family), ("contract", f.contract_id),
                       ("contract_type", f.contract_type), ("contract_form", f.contract_form),
                       ("trade_type", f.trade_type)):
        if value is not None:
            parts.append(t[key].format(v=value))
    for note in notes:
        if note.startswith("ranked_top_"):
            parts.append(t["ranked_top"].format(n=note.removeprefix("ranked_top_")))
        elif note in t:
            parts.append(t[note])
    return f"{t['prefix']} " + "; ".join(parts) + "."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_explain.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/compiler/explain.py tests/test_explain.py
git commit -m "feat: deterministic explanation of compiled queries"
```

---
### Task 9: Semantic layer and example store

**Files:**
- Create: `app/semantic.yaml`, `app/semantic.py`, `examples/examples.yaml`, `app/examples.py`, `tests/test_semantic.py`

**Interfaces:**
- Consumes: `app.config.SEMANTIC_PATH, EXAMPLES_PATH`, `app.text.normalize_text`, `app.compiler.spec.parse_spec`
- Produces: `load_semantic(path=config.SEMANTIC_PATH) -> dict`, `render_schema_prompt(sem: dict) -> str`; `Example(question: str, spec: dict | None, sql: str | None, action: str | None, note: str)`, `ExampleStore(examples: list[Example])`, `ExampleStore.from_yaml(path) -> ExampleStore`, `.search(question: str, k: int = 4) -> list[Example]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_semantic.py`:
```python
from app import config
from app.compiler.spec import parse_spec
from app.examples import ExampleStore
from app.semantic import load_semantic, render_schema_prompt


def test_schema_prompt_mentions_every_table_and_rule():
    text = render_schema_prompt(load_semantic())
    for table in ["deals", "contracts", "participants", "brokers", "usd_rates"]:
        assert f"## {table}" in text
    assert "is_cancelled" in text and "amount_uzs" in text
    assert "status_scope" in text and "product_scope" in text


def test_schema_prompt_columns_exist(db):
    sem = load_semantic()
    for table, info in sem["tables"].items():
        actual = set(db.query(f"SELECT * FROM {table} LIMIT 0").columns)
        assert set(info["columns"]) <= actual, table


def test_every_example_spec_parses():
    store = ExampleStore.from_yaml(config.EXAMPLES_PATH)
    assert len(store.examples) >= 20
    for ex in store.examples:
        if ex.spec is not None:
            parse_spec(ex.spec)
        else:
            assert ex.sql or ex.action == "declare_unanswerable"


def test_example_sql_runs(db):
    for ex in ExampleStore.from_yaml(config.EXAMPLES_PATH).examples:
        if ex.sql:
            db.explain(ex.sql)


def test_search_finds_similar_question():
    store = ExampleStore.from_yaml(config.EXAMPLES_PATH)
    top = store.search("Сколько сделок было в мае 2023 года?", k=1)[0]
    assert top.spec["measures"] == ["deal_count"]
    top_uz = store.search("paxtani eng ko‘p sotib olgan kim?", k=1)[0]
    assert top_uz.spec["group_by"] == "buyer"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.examples'`

- [ ] **Step 3: Create `app/semantic.yaml`**

```yaml
data_coverage: >-
  Сделки (deals) с 03.01.2022 по 30.10.2026, состояния — на конец дня 30.10.2026. Курс USD (usd_rates) —
  каждый день с 01.01.2022 по 31.10.2026. Данных о прибыли, сотрудниках, контактах, рейтингах, других
  биржах, других валютах и прогнозах нет.

tables:
  deals:
    description: "Сделки биржи (bitimlar), одна строка = одна сделка, 500 000 строк."
    columns:
      deal_id: "BIGINT. Номер сделки / bitim raqami."
      deal_date: "DATE. Дата сделки."
      deal_time: "TIME. Время сделки (Ташкент)."
      year: "INT. Год сделки."
      month: "INT. Месяц сделки 1..12."
      trade_type: "Тип торгов: Английский аукцион | Обратный аукцион | Двусторонний аукцион."
      quantity: "DOUBLE. Куплено лотов."
      lot: "DOUBLE. Единиц измерения в одном лоте."
      unit: "Единица измерения: тонна, килограмм, литр, метр, куб.метр, кв.метр, штука."
      volume: "DOUBLE. Объём в единицах измерения = quantity × lot. Суммировать только внутри одной unit."
      volume_tonnes: "DOUBLE. Объём в тоннах для тонна/килограмм, иначе NULL."
      amount: "DOUBLE. Сумма сделки в валюте контракта (UZS или USD). НЕ суммировать без учёта currency."
      currency: "UZS | USD (валюта контракта; USD у экспортных контрактов)."
      usd_rate: "DOUBLE. Курс сум за 1 USD на дату сделки."
      amount_uzs: "DOUBLE. Сумма в сумах (USD × курс на дату сделки)."
      amount_usd: "DOUBLE. Сумма в долларах (UZS ÷ курс на дату сделки)."
      unit_price: "DOUBLE. Цена за единицу в валюте контракта."
      start_price: "DOUBLE. Стартовая цена за единицу в валюте контракта."
      contract_id: "INT. Номер контракта → contracts."
      product_name_full: "Наименование товара как в реестре (свободный текст продавца, не для фильтров)."
      product_canonical: "Канонический товар (фильтровать только по значениям из resolve)."
      product_family: "Группа товара, напр. «Хлопок» = хлопок-сырец + шрот + масло + пряжа."
      region: "Регион склада продавца, узбекская латиница (Toshkent shahri, Samarqand viloyati ...)."
      contract_type: "Внутренний | Экспортный."
      contract_form: "Спот | Форвард | Фьючерс."
      status: "Состояние сделки (см. rules)."
      is_cancelled: "BOOLEAN. TRUE = Аннулирован(Не оплачен): сделка не состоялась."
      seller_inn: "ИНН продавца (STIR) → participants.inn."
      buyer_inn: "ИНН покупателя → participants.inn."
      seller_broker: "Код брокера продавца → brokers.broker_code."
      buyer_broker: "Код брокера покупателя → brokers.broker_code."
      payment_date: "DATE. Дата оплаты, NULL если не оплачено."
      delivery_date: "DATE. Дата поставки, NULL если не поставлено."
      delivered_lots: "DOUBLE. Фактически поставлено лотов."
  contracts:
    description: "Карточки контрактов (shartnomalar), 6 616 строк."
    columns:
      contract_id: "INT. Номер контракта."
      product_name: "Наименование продукта как записал продавец."
      product_canonical: "Канонический товар."
      product_family: "Группа товара."
      unit: "Единица измерения."
      lot: "Единиц в лоте."
      currency: "UZS | USD."
      base_price: "Базисная цена за единицу на дату регистрации."
      deposit_pct: "Залог, % от суммы сделки."
      region: "Регион склада, узбекская латиница."
      trade_type: "Тип торгов."
      contract_type: "Внутренний | Экспортный."
      contract_form: "Спот | Форвард | Фьючерс."
      payment_term_days: "INT. Срок оплаты, календарных дней от даты сделки."
      delivery_term_days: "INT. Срок поставки, календарных дней от даты оплаты."
      seller_inn: "ИНН продавца."
      registered_date: "DATE. Дата регистрации."
      valid_until: "DATE. Действует до."
  participants:
    description: "Компании — продавцы и покупатели (ishtirokchilar), 14 392 строки."
    columns:
      inn: "ИНН (STIR), 9 цифр."
      name: "Наименование как в реестре (для вывода, не для фильтров)."
      region: "Регион, узбекская латиница."
      activity: "Вид деятельности."
      role: "Покупатель | Продавец."
      vat_payer: "BOOLEAN. Плательщик НДС."
      deals_as_buyer: "INT. Сделок в роли покупателя."
      deals_as_seller: "INT. Сделок в роли продавца."
  brokers:
    description: "Брокерские конторы, 120 строк."
    columns:
      broker_code: "Код брокера, BK001..BK120."
      name: "Наименование."
      inn: "ИНН конторы."
      region: "Регион."
  usd_rates:
    description: "Курс: сум за 1 USD на каждый календарный день."
    columns:
      rate_date: "DATE."
      rate: "DOUBLE."

rules:
  - "Сделка «состоялась», если NOT is_cancelled. Аннулирован(Не оплачен) — не оплачена в срок и аннулирована."
  - "Состояния: Новый (заключена 30.10.2026), Не подписан, Заключен (форвард/фьючерс подписан), Ждёт оплату (спот подписан), Оплачен (не поставлен), Исполнен (оплачен и поставлен), Исполнен(Частично), Аннулирован(Не оплачен)."
  - "Деньги: всегда amount_uzs или amount_usd; amount — только вместе с currency."
  - "Объём: volume_tonnes для массовых товаров; иначе volume с группировкой по unit."
  - "Цена за кг = SUM(amount_uzs) / (SUM(volume_tonnes) × 1000)."
  - "Компании, брокеры, товары, регионы — только по ID/значениям из resolve(); по названиям не фильтровать."
  - "В выдаче рядом с ИНН/кодом всегда выводить наименование (join participants/brokers)."
  - "Производные числа (проценты, средние, доли) считать в SQL отдельными колонками."

ambiguities:
  status_scope:
    when: "счёт или сумма сделок без явного указания состояния"
    default: "показать все сделки и отдельно без аннулированных (status_scope=split); рейтинги — без аннулированных"
  measure:
    when: "«больше всего», «крупнейший», «eng ko‘p» без указания мерила"
    default: "два мерила: объём (volume_tonnes, для немассовых товаров — deal_count) и сумма (amount_uzs)"
  currency:
    when: "сумма без указания валюты"
    default: "в сумах (amount_uzs); «в долларах» → amount_usd"
  product_scope:
    when: "«хлопок», «paxta» без уточнения"
    default: "хлопок-сырец (products=[Хлопок-сырец]); если вопрос о всей хлопковой продукции — product_family=Хлопок с group_by=product"
  role:
    when: "«торговала больше всего», «eng ko‘p savdo qilgan» без указания покупатель/продавец"
    default: "group_by=participant (отдельно покупатели и продавцы)"

glossary:
  - "bitim, savdo / сделка, торги → deals"
  - "shartnoma / контракт → contracts"
  - "xaridor, sotib olgan / покупатель, купил → buyer"
  - "sotuvchi, sotgan / продавец, продал → seller"
  - "korxona, kompaniya, tashkilot / компания, предприятие → participants"
  - "STIR / ИНН → inn"
  - "summa, qiymat / сумма, стоимость → amount_uzs | amount_usd"
  - "hajm, miqdor, tonna / объём, количество, тонн → volume_tonnes | volume"
  - "narx / цена → unit_price | avg_price_uzs_per_kg"
  - "eksport / экспорт → contract_type=Экспортный; ichki / внутренний → Внутренний"
  - "bekor qilingan / аннулирован → is_cancelled"
  - "viloyat, hudud / регион, область → region"
  - "kurs / курс → usd_rates"
  - "chorak / квартал: I = месяцы 1–3, II = 4–6, III = 7–9, IV = 10–12"
```

- [ ] **Step 4: Implement `app/semantic.py`**

```python
"""Semantic layer: column meanings, business rules, ambiguity registry, glossary."""
from pathlib import Path

import yaml

from app import config


def load_semantic(path: Path = config.SEMANTIC_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_schema_prompt(sem: dict) -> str:
    out = ["# Data coverage", sem["data_coverage"].strip(), "", "# Tables"]
    for table, info in sem["tables"].items():
        out.append(f"## {table}")
        out.append(info["description"])
        out.extend(f"- {col}: {desc}" for col, desc in info["columns"].items())
        out.append("")
    out.append("# Business rules")
    out.extend(f"- {r}" for r in sem["rules"])
    out.append("")
    out.append("# Ambiguity registry (the only allowed split axes)")
    for axis, info in sem["ambiguities"].items():
        out.append(f"- {axis}: when {info['when']} → {info['default']}")
    out.append("")
    out.append("# Glossary (Uzbek / Russian → schema)")
    out.extend(f"- {g}" for g in sem["glossary"])
    return "\n".join(out)
```

- [ ] **Step 5: Create `examples/examples.yaml`**

Examples use IDs that exist in the data; none duplicates an eval question.
```yaml
- question: "Сколько сделок было заключено в июле 2024 года?"
  spec: {kind: deals_aggregate, measures: [deal_count], filters: {year: 2024, month_from: 7}}
- question: "2023-yil mart oyida nechta bitim tuzilgan?"
  spec: {kind: deals_aggregate, measures: [deal_count], filters: {year: 2023, month_from: 3}}
- question: "Сколько сделок было во втором квартале 2025 года?"
  spec: {kind: deals_aggregate, measures: [deal_count], filters: {year: 2025, month_from: 4, month_to: 6}}
- question: "Какая общая сумма сделок в 2023 году?"
  spec: {kind: deals_aggregate, measures: [amount_uzs], filters: {year: 2023}}
- question: "Какова стоимость экспортных сделок в долларах за 2024 год?"
  spec: {kind: deals_aggregate, measures: [amount_usd], filters: {year: 2024, contract_type: Экспортный}}
- question: "Какая компания купила больше всего карбамида?"
  note: "resolve(product, карбамид) → Карбамид; мерило не указано → объём и сумма"
  spec: {kind: deals_aggregate, measures: [volume_tonnes, amount_uzs], group_by: buyer, filters: {products: [Карбамид]}}
- question: "Paxta xom ashyosini eng ko‘p sotib olgan kompaniya qaysi?"
  note: "resolve(product, paxta xom ashyosi) → Хлопок-сырец"
  spec: {kind: deals_aggregate, measures: [volume_tonnes, amount_uzs], group_by: buyer, filters: {products: [Хлопок-сырец]}}
- question: "Кто больше всех продал цемента по сумме в 2023 году?"
  spec: {kind: deals_aggregate, measures: [amount_uzs], group_by: seller, top: 3, filters: {year: 2023, products: [Портландцемент]}}
- question: "Какая компания больше всего торговала в 2023 году?"
  note: "роль не указана → покупатели и продавцы отдельно"
  spec: {kind: deals_aggregate, measures: [amount_uzs], group_by: participant, filters: {year: 2023}}
- question: "Кто был брокером по сделке 412000?"
  spec: {kind: deal_lookup, deal_id: 412000}
- question: "101800-shartnoma bo‘yicha sotuvchi va brokerlar kim?"
  spec: {kind: contract_lookup, contract_id: 101800}
- question: "Какой брокер обслужил сделки на наибольшую сумму в 2024 году?"
  spec: {kind: deals_aggregate, measures: [amount_uzs], group_by: broker, filters: {year: 2024}}
- question: "BK045 broker 2025-yilda nechta bitimda qatnashgan?"
  spec: {kind: deals_aggregate, measures: [deal_count], filters: {year: 2025, brokers: [BK045]}}
- question: "Какой брокер покупателей провёл больше всего сделок в 2023?"
  spec: {kind: deals_aggregate, measures: [deal_count], group_by: buyer_broker, filters: {year: 2023}}
- question: "Какой был курс доллара 01.03.2025?"
  spec: {kind: usd_rate, date: "2025-03-01"}
- question: "2024-yil fevral oyida o‘rtacha dollar kursi qancha bo‘lgan?"
  spec: {kind: usd_rate, year: 2024, month: 2}
- question: "Сколько дизельного топлива продано в 2023 году?"
  note: "дизель в литрах → volume по unit"
  spec: {kind: deals_aggregate, measures: [volume], filters: {year: 2023, products: [Дизельное топливо]}}
- question: "Средняя цена хлопка-сырца за килограмм в 2024 году?"
  spec: {kind: deals_aggregate, measures: [avg_price_uzs_per_kg], status_scope: valid, filters: {year: 2024, products: [Хлопок-сырец]}}
- question: "Какая доля сделок была аннулирована в 2023 году?"
  spec: {kind: deals_aggregate, measures: [cancelled_pct, deal_count], status_scope: all, filters: {year: 2023}}
- question: "Qaysi viloyatda 2025-yilda eng ko‘p bitim tuzilgan?"
  spec: {kind: deals_aggregate, measures: [deal_count], group_by: region, filters: {year: 2025}}
- question: "Сколько сделок по цементу было в Самаркандской области в 2024?"
  spec: {kind: deals_aggregate, measures: [deal_count], filters: {year: 2024, products: [Портландцемент], regions: [Samarqand viloyati]}}
- question: "Сколько продано хлопковой продукции (масло, шрот, пряжа, сырец) в 2025 по сумме?"
  spec: {kind: deals_aggregate, measures: [amount_uzs], group_by: product, top: 10, filters: {year: 2025, product_family: Хлопок}}
- question: "Как менялось число сделок по месяцам в 2024 году?"
  spec: {kind: deals_aggregate, measures: [deal_count], group_by: month, status_scope: valid, filters: {year: 2024}}
- question: "Сколько сделок исполнено частично в 2023 году и сколько лотов поставлено?"
  spec: {kind: deals_aggregate, measures: [deal_count, lots_delivered], status_scope: all, filters: {year: 2023, statuses: ["Исполнен(Частично)"]}}
- question: "Сколько покупателей зарегистрировано в реестре?"
  spec: {kind: registry_count, entity: participants, role: Покупатель}
- question: "Сколько сделок было в 2021 году?"
  action: declare_unanswerable
  note: "данные начинаются 03.01.2022"
- question: "Какая прибыль у компании Pudexiz Plast?"
  action: declare_unanswerable
  note: "прибыли в данных нет"
- question: "Какой средний срок поставки по форвардным контрактам?"
  note: "нет шаблона → submit_sql"
  sql: "SELECT AVG(delivery_term_days) AS avg_delivery_term_days, COUNT(*) AS contracts FROM contracts WHERE contract_form = 'Форвард'"
```

- [ ] **Step 6: Implement `app/examples.py`**

```python
"""Curated question → spec/SQL examples, retrieved by character-trigram similarity."""
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.text import normalize_text


@dataclass
class Example:
    question: str
    spec: dict | None = None
    sql: str | None = None
    action: str | None = None
    note: str = ""


def _grams(text: str) -> Counter:
    t = f"  {normalize_text(text)}  "
    return Counter(t[i:i + 3] for i in range(len(t) - 2))


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class ExampleStore:
    def __init__(self, examples: list[Example]):
        self.examples = examples
        self._grams = [_grams(e.question) for e in examples]

    @classmethod
    def from_yaml(cls, path: Path) -> "ExampleStore":
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls([Example(r["question"], r.get("spec"), r.get("sql"), r.get("action"), r.get("note", ""))
                    for r in raw])

    def search(self, question: str, k: int = 4) -> list[Example]:
        q = _grams(question)
        ranked = sorted(range(len(self.examples)), key=lambda i: -_cosine(q, self._grams[i]))
        return [self.examples[i] for i in ranked[:k]]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: 5 passed

- [ ] **Step 8: Commit**

```bash
git add app/semantic.yaml app/semantic.py examples app/examples.py tests/test_semantic.py
git commit -m "feat: semantic layer and curated example store"
```

---
### Task 10: Free-SQL validators (fallback path)

**Files:**
- Create: `app/agent/__init__.py` (empty), `app/agent/validators.py`, `tests/test_validators.py`

**Interfaces:**
- Consumes: `Database.explain`
- Produces: `Validation(ok: bool, sql: str, errors: list[str])`; `validate_sql(sql: str, allowed: set[str], question: str, db: Database | None = None, check_ids: bool = True, row_limit: int = 1000) -> Validation`. `allowed` = values returned by `resolve()` in this request.

- [ ] **Step 1: Write the failing tests**

`tests/test_validators.py`:
```python
from app.agent.validators import validate_sql


def v(sql, allowed=frozenset(), question="", db=None, **kw):
    return validate_sql(sql, set(allowed), question, db, **kw)


def test_plain_select_ok_and_limit_added():
    r = v("SELECT COUNT(*) AS n FROM deals WHERE year = 2025")
    assert r.ok, r.errors
    assert r.sql.rstrip().endswith("LIMIT 1000")


def test_existing_limit_kept():
    r = v("SELECT deal_id FROM deals ORDER BY amount_uzs DESC LIMIT 5")
    assert r.ok and r.sql.count("LIMIT") == 1


def test_rejects_non_select_and_multiple_statements():
    assert not v("DELETE FROM deals").ok
    assert not v("SELECT 1; SELECT 2").ok


def test_rejects_unknown_table_and_table_functions():
    assert any("unknown table" in e for e in v("SELECT * FROM secrets").errors)
    assert not v("SELECT * FROM read_csv('data/deals_2022.csv')").ok


def test_cte_names_allowed():
    assert v("WITH t AS (SELECT buyer_inn FROM deals) SELECT COUNT(*) FROM t").ok


def test_money_without_currency_rejected():
    r = v("SELECT SUM(amount) FROM deals")
    assert not r.ok and any("amount_uzs" in e for e in r.errors)
    assert v("SELECT currency, SUM(amount) FROM deals GROUP BY currency").ok


def test_name_filters_rejected():
    r = v("SELECT COUNT(*) FROM participants WHERE name LIKE '%Plast%'")
    assert not r.ok and any("resolve" in e for e in r.errors)
    assert not v("SELECT COUNT(*) FROM deals WHERE product_name_full ILIKE '%хлоп%'").ok


def test_ids_must_be_resolved_or_in_question():
    sql = "SELECT COUNT(*) FROM deals WHERE buyer_inn = '975923536'"
    assert not v(sql).ok
    assert v(sql, allowed={"975923536"}).ok
    assert v(sql, question="Сколько купила компания с ИНН 975923536?").ok
    assert not v("SELECT COUNT(*) FROM deals WHERE product_canonical = 'Золото'").ok
    assert v("SELECT COUNT(*) FROM deals WHERE seller_inn = '975923536'", check_ids=False).ok


def test_explain_catches_bad_column(db):
    r = v("SELECT nope FROM deals", db=db)
    assert not r.ok and r.errors
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validators.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent'`

- [ ] **Step 3: Implement `app/agent/validators.py`**

```python
"""Validation for free-form SQL (fallback path when no spec fits)."""
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.db import Database, QueryError

KNOWN_TABLES = {"deals", "contracts", "participants", "brokers", "usd_rates", "products", "regions"}
NAME_COLUMNS = {"name", "name_norm", "product_name", "product_name_full", "warehouse_address"}
MONEY_COLUMNS = {"amount", "start_price", "unit_price", "base_price"}
RESOLVED_COLUMNS = {"product_canonical", "product_family", "region", "seller_inn", "buyer_inn", "inn",
                    "seller_broker", "buyer_broker", "broker_code"}
BLOCKED_FUNCTIONS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
                     "read_blob", "glob", "getenv", "current_setting", "query", "query_table"}
_ID_LITERAL = re.compile(r"\d{9}|BK\d{3}")


@dataclass
class Validation:
    ok: bool
    sql: str
    errors: list[str] = field(default_factory=list)


def _string_literals(node: exp.Expression) -> list[str]:
    return [n.this for n in node.find_all(exp.Literal) if n.is_string]


def validate_sql(sql: str, allowed: set[str], question: str, db: Database | None = None,
                 check_ids: bool = True, row_limit: int = 1000) -> Validation:
    sql = sql.strip().rstrip(";").strip()
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        return Validation(False, sql, [f"SQL parse error: {e}"])
    if len(statements) != 1:
        return Validation(False, sql, ["exactly one SQL statement is required"])
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        return Validation(False, sql, ["only SELECT / WITH queries are allowed"])

    errors: list[str] = []
    ctes = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    for t in stmt.find_all(exp.Table):
        name = t.name.lower()
        if not name:
            errors.append("table functions are not allowed")
        elif name not in KNOWN_TABLES | ctes:
            errors.append(f"unknown table {t.name!r}; tables: {', '.join(sorted(KNOWN_TABLES))}")
    for f in stmt.find_all(exp.Func):
        fname = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if fname in BLOCKED_FUNCTIONS:
            errors.append(f"function {fname} is not allowed")

    all_columns = {c.name for c in stmt.find_all(exp.Column)}
    for agg in stmt.find_all(exp.AggFunc):
        money = {c.name for c in agg.find_all(exp.Column)} & MONEY_COLUMNS
        if money and "currency" not in all_columns:
            errors.append(f"{', '.join(sorted(money))} is in the contract currency; use amount_uzs / amount_usd "
                          "or group/filter by currency")
            break

    for node in stmt.find_all(exp.EQ, exp.NEQ, exp.Like, exp.ILike, exp.In):
        cols = [x for x in (node.this, node.args.get("expression")) if isinstance(x, exp.Column)]
        literals = _string_literals(node)
        for col in cols:
            if col.name in NAME_COLUMNS and literals:
                errors.append(f"do not filter by {col.name} text; call resolve() and filter by inn / "
                              "broker_code / product_canonical / region")
            if check_ids and col.name in RESOLVED_COLUMNS:
                for value in literals:
                    if value not in allowed and value not in question:
                        errors.append(f"{col.name} = {value!r} was not returned by resolve() in this request")
    if check_ids:
        for value in _string_literals(stmt):
            if _ID_LITERAL.fullmatch(value) and value not in allowed and value not in question:
                errors.append(f"ID {value!r} was not returned by resolve() in this request")

    if stmt.args.get("limit") is None:
        sql = f"{sql}\nLIMIT {row_limit}"
    errors = list(dict.fromkeys(errors))
    if not errors and db is not None:
        try:
            db.explain(sql)
        except QueryError as e:
            errors.append(f"database error: {e}")
    return Validation(not errors, sql, errors)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validators.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add app/agent tests/test_validators.py
git commit -m "feat: validators for free-form SQL fallback"
```

---

### Task 11: LLM interface and Gemini implementation

**Files:**
- Create: `app/agent/llm.py`, `tests/fakes.py`, `tests/test_llm_live.py`

**Interfaces:**
- Produces: `ToolSpec(name, description, parameters: dict)`, `ToolCall(name: str, args: dict)`, `LLMReply(text: str, tool_calls: list[ToolCall])`; protocols `Chat.send_text(text) -> LLMReply`, `Chat.send_tool_results(results: list[tuple[str, dict]]) -> LLMReply`, `LLM.start_chat(system: str, tools: list[ToolSpec]) -> Chat`, `LLM.complete(system: str, prompt: str) -> str`; `GeminiLLM(api_key: str, model: str, timeout_s: float)`; test doubles `tests.fakes.FakeLLM(chat_replies=(), completions=())` with `.chat.sent`, `.prompts`, `.system`.

Tool parameter schemas use Gemini's OpenAPI-style dicts (`"type": "OBJECT"`, `"STRING"`, `"INTEGER"`, `"ARRAY"`, `"BOOLEAN"`).

- [ ] **Step 1: Implement `app/agent/llm.py`**

```python
"""LLM abstraction so the agent can be tested with scripted fakes; Gemini is the real backend."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Chat(Protocol):
    def send_text(self, text: str) -> LLMReply: ...
    def send_tool_results(self, results: list[tuple[str, dict]]) -> LLMReply: ...


class LLM(Protocol):
    def start_chat(self, system: str, tools: list[ToolSpec]) -> Chat: ...
    def complete(self, system: str, prompt: str) -> str: ...


def _plain(value):
    """Convert proto/map containers returned by the SDK into plain Python types."""
    if isinstance(value, dict) or hasattr(value, "items"):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)) or (hasattr(value, "__iter__") and not isinstance(value, (str, bytes))):
        return [_plain(v) for v in value]
    return value


class GeminiChat:
    def __init__(self, client, model: str, system: str, tools: list[ToolSpec]):
        from google.genai import types
        self._types = types
        self._client = client
        self._model = model
        self._contents: list = []
        self._config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters=t.parameters)
                for t in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _call(self) -> LLMReply:
        resp = self._client.models.generate_content(model=self._model, contents=self._contents, config=self._config)
        content = resp.candidates[0].content
        self._contents.append(content)
        parts = content.parts or []
        text = "".join(p.text for p in parts if getattr(p, "text", None))
        calls = [ToolCall(fc.name, _plain(fc.args or {})) for fc in (resp.function_calls or [])]
        return LLMReply(text, calls)

    def send_text(self, text: str) -> LLMReply:
        t = self._types
        self._contents.append(t.Content(role="user", parts=[t.Part.from_text(text=text)]))
        return self._call()

    def send_tool_results(self, results: list[tuple[str, dict]]) -> LLMReply:
        t = self._types
        parts = [t.Part.from_function_response(name=name, response=payload) for name, payload in results]
        self._contents.append(t.Content(role="user", parts=parts))
        return self._call()


class GeminiLLM:
    def __init__(self, api_key: str, model: str, timeout_s: float):
        from google import genai
        from google.genai import types
        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self._model = model

    def start_chat(self, system: str, tools: list[ToolSpec]) -> GeminiChat:
        return GeminiChat(self._client, self._model, system, tools)

    def complete(self, system: str, prompt: str) -> str:
        resp = self._client.models.generate_content(
            model=self._model, contents=prompt,
            config=self._types.GenerateContentConfig(system_instruction=system, temperature=0))
        return (resp.text or "").strip()
```

- [ ] **Step 2: Create test doubles `tests/fakes.py`**

```python
from app.agent.llm import LLMReply, ToolCall


def call(name, **args):
    return LLMReply(tool_calls=[ToolCall(name, args)])


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send_text(self, text):
        self.sent.append(("text", text))
        return self.replies.pop(0)

    def send_tool_results(self, results):
        self.sent.append(("tools", results))
        return self.replies.pop(0)


class FakeLLM:
    def __init__(self, chat_replies=(), completions=()):
        self.chat = FakeChat(chat_replies)
        self.completions = list(completions)
        self.prompts = []
        self.system = None

    def start_chat(self, system, tools):
        self.system = system
        return self.chat

    def complete(self, system, prompt):
        self.prompts.append(prompt)
        return self.completions.pop(0)
```

- [ ] **Step 3: Write the live smoke test `tests/test_llm_live.py`**

```python
import pytest

from app import config
from app.agent.llm import GeminiLLM, ToolSpec

pytestmark = pytest.mark.live


@pytest.fixture
def llm():
    if not config.GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not set")
    return GeminiLLM(config.GEMINI_API_KEY, config.GEMINI_MODEL, 30)


def test_complete(llm):
    assert "4" in llm.complete("Answer with a number only.", "2+2=?")


def test_tool_call_round_trip(llm):
    tool = ToolSpec("lookup", "Look up a deal by id.",
                    {"type": "OBJECT", "properties": {"deal_id": {"type": "INTEGER"}}, "required": ["deal_id"]})
    chat = llm.start_chat("Always call the lookup tool.", [tool])
    reply = chat.send_text("Find deal 387830")
    assert reply.tool_calls and reply.tool_calls[0].name == "lookup"
    assert int(reply.tool_calls[0].args["deal_id"]) == 387830
    final = chat.send_tool_results([("lookup", {"buyer_broker": "BK106"})])
    assert "BK106" in final.text
```

- [ ] **Step 4: Run the live test once (needs a key)**

Run: `GEMINI_API_KEY=... uv run pytest tests/test_llm_live.py -m live -v`
Expected: 2 passed. If `FunctionDeclaration(parameters=dict)` is rejected by the installed google-genai version, switch to `parameters_json_schema=` with lower-case JSON-schema types, and update all tool schemas in Task 12 the same way.

- [ ] **Step 5: Commit**

```bash
git add app/agent/llm.py tests/fakes.py tests/test_llm_live.py
git commit -m "feat: LLM interface with Gemini backend and test fakes"
```

---
### Task 12: Writer agent loop

**Files:**
- Create: `app/agent/writer.py`, `tests/test_writer.py`

**Interfaces:**
- Consumes: `LLM`, `Database`, `Resolver`, `ExampleStore`, `parse_spec`, `entity_values`, `compile_spec`, `validate_sql`, `to_jsonable`
- Produces: `WriterResult(status: Literal["ok","unanswerable","failed"], path: Literal["compiled","sql"] | None, sql: str | None, result: QueryResult | None, spec: Spec | None, splits: list[str], notes: list[str], assumptions: list[str], reason: str, trace: list[dict])`; `Writer(llm, db, resolver, examples, schema_prompt: str, max_tool_calls: int = 8, max_fixes: int = 2)`; `Writer.run(question: str, lang: str, deadline: float, previous: dict | None = None) -> WriterResult` (`deadline` is a `time.monotonic()` value).

- [ ] **Step 1: Write the failing tests**

`tests/test_writer.py`:
```python
import time

import pytest

from app import config
from app.agent.writer import Writer
from app.examples import ExampleStore
from app.resolve import Resolver
from app.semantic import load_semantic, render_schema_prompt
from tests.fakes import FakeLLM, call


@pytest.fixture(scope="module")
def parts(db):
    return db, Resolver(db), ExampleStore.from_yaml(config.EXAMPLES_PATH), render_schema_prompt(load_semantic())


def make(parts, replies, **kw):
    db, resolver, examples, schema = parts
    llm = FakeLLM(chat_replies=replies)
    return Writer(llm, db, resolver, examples, schema, **kw), llm


def deadline():
    return time.monotonic() + 60


COUNT_SPEC = {"kind": "deals_aggregate", "measures": ["deal_count"],
              "filters": {"year": 2025, "seller_inns": ["975923536"]}}


def test_resolve_then_compile(parts):
    writer, llm = make(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                               call("compile_query", spec=COUNT_SPEC, assumptions=[])])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "ok" and res.path == "compiled"
    assert res.result.columns == ["deals_all", "deals_valid"]
    assert "status_scope" in res.splits
    candidates = llm.chat.sent[1][1][0][1]["candidates"]
    assert candidates[0]["id"] == "975923536"


def test_unresolved_inn_is_rejected_then_unanswerable(parts):
    writer, llm = make(parts, [call("compile_query", spec=COUNT_SPEC, assumptions=[]),
                               call("declare_unanswerable", reason="company not found")])
    res = writer.run("Сколько продала компания X?", "ru", deadline())
    assert res.status == "unanswerable"
    error = llm.chat.sent[1][1][0][1]["error"]
    assert "975923536" in error


def test_spec_error_out_of_range_is_fed_back(parts):
    bad = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2021}}
    writer, llm = make(parts, [call("compile_query", spec=bad, assumptions=[]),
                               call("declare_unanswerable", reason="2021 not in data")])
    res = writer.run("Сколько сделок было в 2021?", "ru", deadline())
    assert res.status == "unanswerable"
    assert "outside the data" in llm.chat.sent[1][1][0][1]["error"]


def test_free_sql_fallback(parts):
    sql = "SELECT AVG(delivery_term_days) AS avg_days FROM contracts WHERE contract_form = 'Форвард'"
    writer, _ = make(parts, [call("submit_sql", sql=sql, splits=[], assumptions=[])])
    res = writer.run("Средний срок поставки по форвардам?", "ru", deadline())
    assert res.status == "ok" and res.path == "sql"
    assert res.result.columns == ["avg_days"]


def test_fix_budget(parts):
    bad = {"kind": "deals_aggregate", "measures": ["profit"]}
    writer, _ = make(parts, [call("compile_query", spec=bad, assumptions=[])] * 3, max_fixes=2)
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed" and "fix" in res.reason


def test_tool_budget(parts):
    writer, _ = make(parts, [call("get_examples", question="x")] * 4, max_tool_calls=3)
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed" and "tool budget" in res.reason


def test_deadline(parts):
    writer, _ = make(parts, [call("get_examples", question="x")])
    res = writer.run("?", "ru", time.monotonic() - 1)
    assert res.status == "failed" and res.reason == "timeout"


def test_text_reply_is_nudged_once(parts):
    from app.agent.llm import LLMReply
    writer, llm = make(parts, [LLMReply(text="The answer is 5"), LLMReply(text="still text")])
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed"
    assert llm.chat.sent[1][0] == "text"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.writer'`

- [ ] **Step 3: Implement `app/agent/writer.py`**

```python
"""Writer agent: Gemini turns a question into a compiled spec (preferred) or validated SQL."""
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

from app.agent.llm import LLM, ToolSpec
from app.agent.validators import validate_sql
from app.compiler.spec import (CONTRACT_FORMS, CONTRACT_TYPES, DIMENSIONS, MEASURES, REGISTRY_ENTITIES, ROLES,
                               STATUS_SCOPES, STATUSES, TRADE_TYPES, Spec, SpecError, entity_values, parse_spec)
from app.compiler.sql import compile_spec
from app.db import Database, QueryError, QueryResult, to_jsonable
from app.examples import ExampleStore
from app.resolve import Resolver

S, I, B = {"type": "STRING"}, {"type": "INTEGER"}, {"type": "BOOLEAN"}


def _arr(item):
    return {"type": "ARRAY", "items": item}


def _enum(values):
    return {"type": "STRING", "enum": list(values)}


FILTERS_SCHEMA = {"type": "OBJECT", "properties": {
    "year": I, "month_from": I, "month_to": I,
    "date_from": {"type": "STRING", "description": "YYYY-MM-DD"},
    "date_to": {"type": "STRING", "description": "YYYY-MM-DD"},
    "products": _arr(S), "product_family": S, "regions": _arr(S),
    "buyer_inns": _arr(S), "seller_inns": _arr(S), "participant_inns": _arr(S),
    "seller_brokers": _arr(S), "buyer_brokers": _arr(S), "brokers": _arr(S),
    "contract_id": I, "contract_type": _enum(CONTRACT_TYPES), "contract_form": _enum(CONTRACT_FORMS),
    "trade_type": _enum(TRADE_TYPES), "statuses": _arr(_enum(STATUSES)),
}}

SPEC_SCHEMA = {"type": "OBJECT", "properties": {
    "kind": _enum(["deals_aggregate", "deal_lookup", "contract_lookup", "usd_rate", "registry_count"]),
    "measures": _arr(_enum(MEASURES)), "filters": FILTERS_SCHEMA, "group_by": _enum(DIMENSIONS), "top": I,
    "status_scope": _enum(STATUS_SCOPES),
    "deal_id": I, "contract_id": I, "date": {"type": "STRING", "description": "YYYY-MM-DD"},
    "year": I, "month": I,
    "entity": _enum(REGISTRY_ENTITIES), "role": _enum(ROLES), "vat_payer": B, "region": S, "registered_year": I,
}, "required": ["kind"]}

TOOLS = [
    ToolSpec("resolve", "Find exact IDs for a company, broker, product, region or contract named in the question.",
             {"type": "OBJECT", "properties": {
                 "kind": _enum(["company", "broker", "product", "region", "contract"]), "text": S},
              "required": ["kind", "text"]}),
    ToolSpec("get_examples", "Return verified example questions with their spec or SQL.",
             {"type": "OBJECT", "properties": {"question": S}, "required": ["question"]}),
    ToolSpec("compile_query", "PREFERRED. Run a structured query spec compiled by verified templates.",
             {"type": "OBJECT", "properties": {"spec": SPEC_SCHEMA, "assumptions": _arr(S)},
              "required": ["spec", "assumptions"]}),
    ToolSpec("submit_sql", "FALLBACK only when no spec can express the question. One DuckDB SELECT.",
             {"type": "OBJECT", "properties": {
                 "sql": S, "splits": _arr(_enum(["status_scope", "measure", "currency", "product_scope", "role"])),
                 "assumptions": _arr(S)}, "required": ["sql", "splits", "assumptions"]}),
    ToolSpec("explore_sql", "Read-only exploration query (max 50 rows), e.g. distinct units of a product.",
             {"type": "OBJECT", "properties": {"sql": S}, "required": ["sql"]}),
    ToolSpec("declare_unanswerable", "The database cannot answer this question.",
             {"type": "OBJECT", "properties": {"reason": S}, "required": ["reason"]}),
]

SYSTEM = """You are the query planner of a Q&A service over a commodity-exchange deal registry.
Questions come in Russian or Uzbek. You never answer from memory: you only produce a query.

Steps:
1. If the question needs data the database does not have (see Data coverage), or a period outside it,
   call declare_unanswerable.
2. For every company, broker, product, region or contract mentioned, call resolve(). Use only the IDs/values
   it returns. If nothing relevant comes back, call declare_unanswerable. If several candidates are equally
   close, include all of them (max 5) and group by that entity so each is shown separately.
3. Call get_examples() once.
4. Prefer compile_query(spec). Use submit_sql only if no spec kind/measure/dimension can express the question.
5. Ambiguity: follow the Ambiguity registry. Compute the variants in ONE query: status_scope=split for counts
   and totals; two measures for "most/largest" without a measure; group_by=participant when the role is not
   stated. At most 2 split axes. Write the defaults you applied as short assumptions in the question's language.
6. Free SQL rules: DuckDB dialect; money via amount_uzs/amount_usd; never filter by names; derived numbers
   (percent, averages, differences) as SQL columns; include participant/broker names next to IDs.

{schema}
"""


@dataclass
class WriterResult:
    status: Literal["ok", "unanswerable", "failed"]
    path: Literal["compiled", "sql"] | None = None
    sql: str | None = None
    result: QueryResult | None = None
    spec: Spec | None = None
    splits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    reason: str = ""
    trace: list[dict] = field(default_factory=list)


class Writer:
    def __init__(self, llm: LLM, db: Database, resolver: Resolver, examples: ExampleStore, schema_prompt: str,
                 max_tool_calls: int = 8, max_fixes: int = 2):
        self.llm, self.db, self.resolver, self.examples = llm, db, resolver, examples
        self.system = SYSTEM.format(schema=schema_prompt)
        self.max_tool_calls, self.max_fixes = max_tool_calls, max_fixes

    def run(self, question: str, lang: str, deadline: float, previous: dict | None = None) -> WriterResult:
        allowed: set[str] = set()
        trace: list[dict] = []
        calls = fixes = 0
        nudged = False

        def failed(reason):
            return WriterResult("failed", reason=reason, trace=trace)

        if time.monotonic() > deadline:
            return failed("timeout")
        chat = self.llm.start_chat(self.system, TOOLS)
        reply = chat.send_text(self._prompt(question, lang, previous))
        while True:
            if time.monotonic() > deadline:
                return failed("timeout")
            if not reply.tool_calls:
                if nudged:
                    return failed("model did not call a tool")
                nudged = True
                reply = chat.send_text("Do not answer in text. Call compile_query, submit_sql or declare_unanswerable.")
                continue
            results = []
            for tc in reply.tool_calls:
                calls += 1
                trace.append({"tool": tc.name, "args": tc.args})
                if calls > self.max_tool_calls:
                    return failed("tool budget exceeded")
                if tc.name == "declare_unanswerable":
                    return WriterResult("unanswerable", reason=str(tc.args.get("reason", "")), trace=trace)
                if tc.name in ("compile_query", "submit_sql"):
                    outcome = (self._compile(tc.args, allowed, question) if tc.name == "compile_query"
                               else self._sql(tc.args, allowed, question))
                    if isinstance(outcome, WriterResult):
                        outcome.trace = trace
                        return outcome
                    fixes += 1
                    trace[-1]["error"] = outcome
                    if fixes > self.max_fixes:
                        return failed(f"fix budget exceeded: {outcome}")
                    results.append((tc.name, {"error": outcome}))
                    continue
                results.append((tc.name, self._tool(tc.name, tc.args, allowed, question)))
            reply = chat.send_tool_results(results)

    @staticmethod
    def _prompt(question, lang, previous):
        text = f"Question: {question}\nQuestion language: {lang}\n"
        if previous:
            text += f"Previous question: {previous.get('question', '')}\nPrevious SQL: {previous.get('sql', '')}\n"
        return text

    def _compile(self, args, allowed, question) -> WriterResult | str:
        try:
            spec = parse_spec(args.get("spec") or {})
        except SpecError as e:
            return str(e)
        missing = sorted(v for v in entity_values(spec) if v not in allowed and v not in question)
        if missing:
            return f"values {missing} were not returned by resolve() in this request; call resolve() first"
        compiled = compile_spec(spec)
        try:
            result = self.db.query(compiled.sql, timeout_s=10)
        except QueryError as e:
            return f"compiled query failed: {e}"
        return WriterResult("ok", "compiled", compiled.sql, result, spec, compiled.splits, compiled.notes,
                            [str(a) for a in args.get("assumptions") or []])

    def _sql(self, args, allowed, question) -> WriterResult | str:
        v = validate_sql(str(args.get("sql", "")), allowed, question, self.db)
        if not v.ok:
            return "; ".join(v.errors)
        try:
            result = self.db.query(v.sql, timeout_s=10)
        except QueryError as e:
            return f"query failed: {e}"
        return WriterResult("ok", "sql", v.sql, result, None, [str(s) for s in args.get("splits") or []][:2], [],
                            [str(a) for a in args.get("assumptions") or []])

    def _tool(self, name, args, allowed, question) -> dict:
        if name == "resolve":
            cands = self.resolver.resolve(args.get("kind", ""), str(args.get("text", "")))
            allowed.update(c.id for c in cands)
            return {"candidates": [asdict(c) for c in cands]}
        if name == "get_examples":
            exs = self.examples.search(str(args.get("question") or question))
            return {"examples": [{"question": e.question, "spec": json.dumps(e.spec, ensure_ascii=False) if e.spec
                                  else None, "sql": e.sql, "action": e.action, "note": e.note} for e in exs]}
        if name == "explore_sql":
            v = validate_sql(str(args.get("sql", "")), allowed, question, self.db, check_ids=False, row_limit=50)
            if not v.ok:
                return {"error": "; ".join(v.errors)}
            try:
                res = self.db.query(v.sql, timeout_s=5, max_rows=50)
            except QueryError as e:
                return {"error": str(e)}
            return {"columns": res.columns, "rows": [[to_jsonable(c) for c in r] for r in res.rows]}
        return {"error": f"unknown tool {name}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_writer.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add app/agent/writer.py tests/test_writer.py
git commit -m "feat: writer agent with compiled-spec and SQL paths"
```

---
### Task 13: Answer verifier and templates

**Files:**
- Create: `app/answer/__init__.py` (empty), `app/answer/numbers.py`, `app/answer/verifier.py`, `app/answer/templates.py`, `tests/test_verifier.py`

**Interfaces:**
- Consumes: `app.text.normalize_name`
- Produces:
  - `numbers.NumberToken(text: str, value: float, tolerance: float)`, `extract_numbers(text) -> list[NumberToken]`, `extract_ids(text) -> list[str]`, `extract_dates(text) -> list[str]`, `extract_quoted(text) -> list[str]`, `strip_dates_and_ids(text) -> str`, `format_number(v) -> str`, `format_cell(v) -> str`
  - `verifier.Verification(ok: bool, unsupported: list[str])`, `verify_answer(answer: str, question: str, rows: list[tuple], extra_text: str = "") -> Verification`
  - `templates.no_data(lang) -> str`, `failed(lang) -> str`, `timeout(lang) -> str`, `table_answer(lang, columns, rows, explanation: str = "") -> str`

Verification rules: every number in the answer must match a numeric cell, a number inside a string cell, a date component of a date cell, a number in the question, a year 2022–2026, or an integer 0–10 — within the precision the answer displays (`1,23 млрд` matches 1 234 567 890). Every INN (9 digits) and broker code must appear in a cell or the question. Every date must match a date cell or the question. Every «quoted» name must appear in a string cell, the question or `extra_text`.

- [ ] **Step 1: Write the failing tests**

`tests/test_verifier.py`:
```python
import datetime as dt

import pytest

from app.answer.numbers import extract_numbers, format_number
from app.answer.templates import NO_DATA, no_data, table_answer
from app.answer.verifier import verify_answer


def values(text):
    return [t.value for t in extract_numbers(text)]


def test_extract_numbers_formats():
    assert values("Заключено 115 763 сделки") == [115763]
    assert values("на 1,2 млрд сум") == [1.2e9]
    assert values("доля 7,29%") == [7.29]
    assert values("2.5 mln so‘m") == [2.5e6]
    assert values("в 2025 году 9 сделок") == [2025, 9]


def test_tolerance_follows_displayed_precision():
    [t] = extract_numbers("1,23 млрд")
    assert t.tolerance == pytest.approx(0.005e9)


def test_format_number():
    assert format_number(1234567.891) == "1 234 567,89"
    assert format_number(115763.0) == "115 763"


Q = "Сколько сделок было заключено в октябре 2025 года?"


def test_supported_answer_passes():
    assert verify_answer("В октябре 2025 года заключено 9 912 сделок, из них состоялось 9 300.", Q,
                         [(9912, 9300)]).ok


def test_invented_number_fails():
    r = verify_answer("Заключено 10 500 сделок.", Q, [(9912, 9300)])
    assert not r.ok and r.unsupported == ["10 500"]


def test_scaled_number_passes():
    rows = [("915688255", '"ҲАРАЙЎМ КИМЁ" МЧЖ', 1_234_567_890.0)]
    answer = "Больше всего купила «ҲАРАЙЎМ КИМЁ» (ИНН 915688255) — на 1,23 млрд сум."
    assert verify_answer(answer, "Кто купил больше всего?", rows).ok


def test_invented_inn_broker_name_date_fail():
    rows = [("915688255", "BK001", dt.date(2025, 6, 15))]
    assert not verify_answer("ИНН 999999999", "?", rows).ok
    assert not verify_answer("брокер BK002", "?", rows).ok
    assert not verify_answer("компания «Небыло»", "?", rows).ok
    assert not verify_answer("дата 16.06.2025", "?", rows).ok
    assert verify_answer("15.06.2025 брокер BK001, ИНН 915688255", "?", rows).ok


def test_template_answers_always_verify():
    rows = [("915688255", "Х", 1234567.891, dt.date(2025, 6, 15), None)]
    text = table_answer("ru", ["inn", "name", "total_uzs", "date", "x"], rows, "Расчёт: сделки за 2025 год.")
    assert verify_answer(text, "?", rows, extra_text="Расчёт: сделки за 2025 год.").ok


def test_no_data_texts():
    assert no_data("ru").startswith(NO_DATA["ru"])
    assert no_data("uz").startswith("Ma’lumotlarda bu savolga javob yo‘q.")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_verifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.answer'`

- [ ] **Step 3: Implement `app/answer/numbers.py`**

```python
"""Extract and format numbers, IDs, dates and quoted names for answer verification."""
import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

_SEP = "[ \u00a0\u202f]"  # space, no-break space, narrow no-break space
_SCALES = [
    (r"трлн\w*|trln|trillion\w*", 1e12),
    (r"млрд\w*|миллиард\w*|mlrd|milliard\w*", 1e9),
    (r"млн\w*|миллион\w*|mln|million\w*", 1e6),
    (r"тыс\w*|ming", 1e3),
]
_SCALE_RE = "|".join(p for p, _ in _SCALES)
NUMBER_RE = re.compile(
    rf"(?<![\w.,])(\d{{1,3}}(?:{_SEP}\d{{3}})+|\d+)(?:[.,](\d+))?(?:{_SEP}?({_SCALE_RE})\.?)?", re.I)
DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b|\b\d{4}-\d{2}-\d{2}\b")
ID_RE = re.compile(r"(?<!\d)\d{9}(?!\d)|\bBK\d{3}\b", re.I)
QUOTED_RE = re.compile(r"[«“\"]([^»”\"]{2,80})[»”\"]")


@dataclass
class NumberToken:
    text: str
    value: float
    tolerance: float


def _scale(word: str | None) -> float:
    if not word:
        return 1.0
    for pattern, factor in _SCALES:
        if re.fullmatch(pattern, word, re.I):
            return factor
    return 1.0


def strip_dates_and_ids(text: str) -> str:
    return ID_RE.sub(" ", DATE_RE.sub(" ", text))


def extract_numbers(text: str) -> list[NumberToken]:
    tokens = []
    for m in NUMBER_RE.finditer(strip_dates_and_ids(text)):
        integer = re.sub(_SEP, "", m.group(1))
        decimals = m.group(2) or ""
        scale = _scale(m.group(3))
        value = float(f"{integer}.{decimals}" if decimals else integer) * scale
        tokens.append(NumberToken(m.group(0).strip(), value, 0.5 * 10 ** (-len(decimals)) * scale))
    return tokens


def extract_ids(text: str) -> list[str]:
    return [m.group(0).upper() for m in ID_RE.finditer(DATE_RE.sub(" ", text))]


def extract_dates(text: str) -> list[str]:
    return DATE_RE.findall(text)


def extract_quoted(text: str) -> list[str]:
    return [m.group(1).strip() for m in QUOTED_RE.finditer(text)]


def format_number(v) -> str:
    v = float(v)
    if v.is_integer():
        return f"{int(v):,}".replace(",", " ")
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


def format_cell(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (int, float, Decimal)):
        return format_number(v)
    if isinstance(v, dt.datetime):
        return v.strftime("%d.%m.%Y %H:%M:%S")
    if isinstance(v, dt.date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, dt.time):
        return v.strftime("%H:%M:%S")
    return str(v)
```

- [ ] **Step 4: Implement `app/answer/verifier.py`**

```python
"""Deterministic check that every fact in an answer comes from the query result or the question."""
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from app.answer.numbers import extract_dates, extract_ids, extract_numbers, extract_quoted
from app.text import normalize_name

_ALWAYS_NUMBERS = list(range(0, 11)) + [2022, 2023, 2024, 2025, 2026]
_ALWAYS_DATES = {"03.01.2022", "30.10.2026", "01.01.2022", "31.10.2026"}


@dataclass
class Verification:
    ok: bool
    unsupported: list[str] = field(default_factory=list)


def verify_answer(answer: str, question: str, rows: list[tuple], extra_text: str = "") -> Verification:
    numbers: list[float] = list(_ALWAYS_NUMBERS)
    dates: set[str] = set(_ALWAYS_DATES)
    strings: list[str] = []
    for row in rows:
        for cell in row:
            if cell is None or isinstance(cell, bool):
                continue
            if isinstance(cell, (int, float, Decimal)):
                numbers.append(float(cell))
            elif isinstance(cell, dt.date):
                dates |= {cell.strftime("%d.%m.%Y"), cell.isoformat()[:10]}
                numbers += [cell.day, cell.month, cell.year]
            else:
                text = str(cell)
                strings.append(text)
                numbers += [t.value for t in extract_numbers(text)]
                dates |= set(extract_dates(text))
    numbers += [t.value for t in extract_numbers(question)]
    dates |= set(extract_dates(question))
    haystack = " ".join(strings) + " " + question
    names_haystack = normalize_name(haystack + " " + extra_text)

    unsupported: list[str] = []
    for d in extract_dates(answer):
        if d not in dates:
            unsupported.append(d)
    for ident in extract_ids(answer):
        if ident not in haystack.upper():
            unsupported.append(ident)
    for tok in extract_numbers(answer):
        if not any(abs(tok.value - v) <= tok.tolerance + 1e-9 * abs(v) for v in numbers):
            unsupported.append(tok.text)
    for quoted in extract_quoted(answer):
        norm = normalize_name(quoted)
        if norm and norm not in names_haystack:
            unsupported.append(quoted)
    return Verification(not unsupported, unsupported)
```

- [ ] **Step 5: Implement `app/answer/templates.py`**

```python
"""Fixed texts and the row-based fallback answer (always verifiable by construction)."""
from app.answer.numbers import format_cell

NO_DATA = {
    "ru": "В данных нет ответа на этот вопрос.",
    "uz": "Ma’lumotlarda bu savolga javob yo‘q.",
    "uz-Cyrl": "Маълумотларда бу саволга жавоб йўқ.",
}
COVERAGE = {
    "ru": "Данные охватывают сделки биржи с 03.01.2022 по 30.10.2026: сделки, контракты, участники, брокеры "
          "и курс доллара.",
    "uz": "Ma’lumotlar 03.01.2022 dan 30.10.2026 gacha bo‘lgan birja bitimlarini qamrab oladi: bitimlar, "
          "shartnomalar, ishtirokchilar, brokerlar va dollar kursi.",
    "uz-Cyrl": "Маълумотлар 03.01.2022 дан 30.10.2026 гача бўлган биржа битимларини қамраб олади: битимлар, "
               "шартномалар, иштирокчилар, брокерлар ва доллар курси.",
}
FAILED = {
    "ru": "Не удалось надёжно ответить на этот вопрос по данным. Попробуйте переформулировать вопрос.",
    "uz": "Bu savolga ma’lumotlar asosida ishonchli javob berib bo‘lmadi. Savolni boshqacha ifodalab ko‘ring.",
    "uz-Cyrl": "Бу саволга маълумотлар асосида ишончли жавоб бериб бўлмади. Саволни бошқача ифодалаб кўринг.",
}
TIMEOUT = {
    "ru": "Не удалось ответить за отведённое время. Попробуйте задать вопрос проще.",
    "uz": "Belgilangan vaqt ichida javob berib bo‘lmadi. Savolni soddaroq berib ko‘ring.",
    "uz-Cyrl": "Белгиланган вақт ичида жавоб бериб бўлмади. Саволни соддароқ бериб кўринг.",
}
RESULT_HEADER = {"ru": "Результат запроса:", "uz": "So‘rov natijasi:", "uz-Cyrl": "Сўров натижаси:"}


def _lang(lang: str) -> str:
    return lang if lang in NO_DATA else "ru"


def no_data(lang: str) -> str:
    lang = _lang(lang)
    return f"{NO_DATA[lang]} {COVERAGE[lang]}"


def failed(lang: str) -> str:
    return FAILED[_lang(lang)]


def timeout(lang: str) -> str:
    return TIMEOUT[_lang(lang)]


def table_answer(lang: str, columns: list[str], rows: list[tuple], explanation: str = "") -> str:
    lines = [RESULT_HEADER[_lang(lang)]]
    for row in rows[:20]:
        lines.append("; ".join(f"{c}: {format_cell(v)}" for c, v in zip(columns, row)))
    if len(rows) > 20:
        lines.append("…")
    if explanation:
        lines.append(explanation)
    return "\n".join(lines)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_verifier.py -v`
Expected: 10 passed

- [ ] **Step 7: Commit**

```bash
git add app/answer tests/test_verifier.py
git commit -m "feat: deterministic answer verifier and fallback templates"
```

---
### Task 14: Composer and pipeline

**Files:**
- Create: `app/answer/composer.py`, `app/pipeline.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `LLM.complete`, `WriterResult`, `verify_answer`, templates, `explain`, `detect_lang`, `to_jsonable`
- Produces: `compose(llm, *, question, lang, sql, columns, rows, row_count, truncated, splits, assumptions, feedback=None) -> str`; `AskResult` (fields: `answer, lang, answered, source, sql, path, columns, rows, row_count, truncated, splits, assumptions, explanation, verified, unsupported, reason, timings`); `Pipeline(writer, llm, deadline_s: float = 75.0, compose_reserve_s: float = 15.0)`; `Pipeline.ask(question: str, previous: dict | None = None) -> AskResult`; `build_pipeline() -> Pipeline`. `source` ∈ `llm | template | no_data | failed | timeout`.

Answer flow: empty result or all-NULL → `no_data`. Otherwise compose → verify → (on failure, if time remains) compose again with the unsupported tokens → verify → else `table_answer`. For the compiled path the deterministic explanation is appended **after** verification (it is code-generated, not model output).

- [ ] **Step 1: Write the failing tests**

`tests/test_pipeline.py`:
```python
from app.agent.writer import WriterResult
from app.db import QueryResult
from app.pipeline import Pipeline
from tests.fakes import FakeLLM


class StubWriter:
    def __init__(self, result):
        self.result = result

    def run(self, question, lang, deadline, previous=None):
        return self.result


def ok(rows, columns=("deals_all", "deals_valid")):
    return WriterResult("ok", "sql", "SELECT 1", QueryResult(list(columns), rows, False), splits=["status_scope"])


Q = "Сколько сделок было заключено в октябре 2025 года?"


def test_good_answer_is_returned():
    llm = FakeLLM(completions=["Заключено 9 912 сделок, без аннулированных — 9 300."])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), llm).ask(Q)
    assert r.answered and r.verified and r.source == "llm"
    assert r.lang == "ru"
    assert "9 912" in r.answer


def test_hallucination_is_regenerated_then_templated():
    llm = FakeLLM(completions=["Заключено 10 500 сделок.", "Заключено 10 600 сделок."])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), llm).ask(Q)
    assert r.source == "template" and r.verified
    assert "10 500" not in r.answer and "9 912" in r.answer
    assert "10 500" in llm.prompts[1]


def test_regeneration_can_fix():
    llm = FakeLLM(completions=["Заключено 10 500 сделок.", "Заключено 9 912 сделок."])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), llm).ask(Q)
    assert r.source == "llm" and r.answer.startswith("Заключено 9 912")


def test_empty_and_null_results_are_no_data():
    for rows in ([], [(None, None)]):
        r = Pipeline(StubWriter(ok(rows)), FakeLLM()).ask(Q)
        assert not r.answered and r.source == "no_data"
        assert r.answer.startswith("В данных нет ответа")


def test_unanswerable_in_uzbek():
    r = Pipeline(StubWriter(WriterResult("unanswerable", reason="no profit data")), FakeLLM()).ask(
        "Kompaniyaning foydasi qancha?")
    assert not r.answered and r.answer.startswith("Ma’lumotlarda bu savolga javob yo‘q.")


def test_failed_and_timeout():
    r = Pipeline(StubWriter(WriterResult("failed", reason="timeout")), FakeLLM()).ask(Q)
    assert r.source == "timeout" and not r.answered
    r = Pipeline(StubWriter(WriterResult("failed", reason="fix budget exceeded")), FakeLLM()).ask(Q)
    assert r.source == "failed" and not r.answered


def test_composer_exception_falls_back_to_template():
    class Boom(FakeLLM):
        def complete(self, system, prompt):
            raise TimeoutError
    r = Pipeline(StubWriter(ok([(9912, 9300)])), Boom()).ask(Q)
    assert r.source == "template" and r.answered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline'`

- [ ] **Step 3: Implement `app/answer/composer.py`**

```python
"""LLM answer writer. It only rephrases result rows; the verifier checks its output."""
from app.answer.numbers import format_cell

LANG_NAMES = {"ru": "Russian", "uz": "Uzbek (Latin script)", "uz-Cyrl": "Uzbek (Cyrillic script)"}

SYSTEM = """You write the final answer of a commodity-exchange Q&A service.
Rules:
- Answer in {language}.
- Use ONLY values present in the result rows or the question. Never add numbers, names, INNs, broker codes,
  contract numbers or dates that are not there. Do not calculate new numbers (no sums, differences or
  percentages); you may only shorten large numbers like "1,23 млрд" / "1,23 mlrd".
- Copy INNs, broker codes and contract numbers exactly. Put company and broker names in «» exactly as in the
  rows, followed by the INN or code in parentheses.
- Columns ending in _all / _valid: _all = all deals, _valid = without cancelled (annulled) deals. Present both.
  A "ranking" column (by_volume_t, by_total_uzs ...) or "role" column (buyer / seller) means separate
  variants: present each. If the variants give the same top result, say it once.
- Column suffixes: _uzs = so'm, _usd = US dollars, _t = tonnes, _pct = percent.
- Format numbers with spaces between thousands and a comma for decimals: 1 234 567,89.
- 1–4 sentences, or a short list for rankings. No preamble, no SQL, no explanation of the method.
"""


def render_rows(columns: list[str], rows: list[tuple], limit: int = 200) -> str:
    lines = [" | ".join(columns)]
    lines += [" | ".join(format_cell(v) for v in row) for row in rows[:limit]]
    return "\n".join(lines)


def compose(llm, *, question: str, lang: str, sql: str, columns: list[str], rows: list[tuple], row_count: int,
            truncated: bool, splits: list[str], assumptions: list[str], feedback: list[str] | None = None) -> str:
    prompt = (f"Question: {question}\n\nExecuted SQL:\n{sql}\n\n"
              f"Result ({row_count} rows{', truncated' if truncated else ''}):\n{render_rows(columns, rows)}\n\n"
              f"Split axes: {', '.join(splits) or 'none'}\n"
              f"Assumptions: {'; '.join(assumptions) or 'none'}\n")
    if feedback:
        prompt += ("\nYour previous answer contained values that are NOT in the result: " + ", ".join(feedback)
                   + ". Rewrite it using only values from the result.\n")
    return llm.complete(SYSTEM.format(language=LANG_NAMES.get(lang, "Russian")), prompt).strip()
```

- [ ] **Step 4: Implement `app/pipeline.py`**

```python
"""End-to-end question answering with a hard deadline."""
import logging
import time
from dataclasses import dataclass, field

from app import config
from app.agent.writer import Writer, WriterResult
from app.answer import templates
from app.answer.composer import compose
from app.answer.verifier import verify_answer
from app.compiler.explain import explain
from app.db import to_jsonable
from app.text import detect_lang

log = logging.getLogger(__name__)
MAX_RETURNED_ROWS = 200


@dataclass
class AskResult:
    answer: str
    lang: str
    answered: bool
    source: str
    sql: str | None = None
    path: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    splits: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    explanation: str = ""
    verified: bool = True
    unsupported: list[str] = field(default_factory=list)
    reason: str = ""
    timings: dict = field(default_factory=dict)


class Pipeline:
    def __init__(self, writer, llm, deadline_s: float = 75.0, compose_reserve_s: float = 15.0):
        self.writer, self.llm = writer, llm
        self.deadline_s, self.compose_reserve_s = deadline_s, compose_reserve_s

    def ask(self, question: str, previous: dict | None = None) -> AskResult:
        t0 = time.monotonic()
        deadline = t0 + self.deadline_s
        lang = detect_lang(question)
        try:
            wr = self.writer.run(question, lang, deadline - self.compose_reserve_s, previous)
        except Exception as e:  # never surface a stack trace as an answer
            log.exception("writer crashed")
            wr = WriterResult("failed", reason=f"error: {type(e).__name__}")
        timings = {"writer_s": round(time.monotonic() - t0, 2)}
        res = self._answer(question, lang, wr, deadline)
        timings["total_s"] = round(time.monotonic() - t0, 2)
        res.timings = timings
        return res

    def _answer(self, question: str, lang: str, wr: WriterResult, deadline: float) -> AskResult:
        if wr.status == "unanswerable":
            return AskResult(templates.no_data(lang), lang, False, "no_data", reason=wr.reason)
        if wr.status == "failed":
            if wr.reason == "timeout":
                return AskResult(templates.timeout(lang), lang, False, "timeout", reason=wr.reason)
            return AskResult(templates.failed(lang), lang, False, "failed", reason=wr.reason)

        result = wr.result
        explanation = explain(wr.spec, wr.notes, lang) if wr.spec is not None else ""
        base = dict(lang=lang, sql=wr.sql, path=wr.path, columns=result.columns,
                    rows=[[to_jsonable(c) for c in r] for r in result.rows[:MAX_RETURNED_ROWS]],
                    row_count=len(result.rows), truncated=result.truncated, splits=wr.splits,
                    assumptions=wr.assumptions, explanation=explanation)
        if not result.rows or all(c is None for row in result.rows for c in row):
            return AskResult(templates.no_data(lang), answered=False, source="no_data", **base)

        context = " ".join(wr.assumptions)
        feedback = None
        unsupported: list[str] = []
        for _ in range(2):
            if time.monotonic() > deadline:
                break
            try:
                text = compose(self.llm, question=question, lang=lang, sql=wr.sql, columns=result.columns,
                               rows=result.rows, row_count=len(result.rows), truncated=result.truncated,
                               splits=wr.splits, assumptions=wr.assumptions, feedback=feedback)
            except Exception:
                log.exception("composer failed")
                break
            check = verify_answer(text, question, result.rows, extra_text=context)
            if check.ok and text:
                answer = f"{text}\n\n{explanation}" if explanation else text
                return AskResult(answer, answered=True, source="llm", verified=True, **base)
            unsupported = check.unsupported
            feedback = unsupported
        answer = templates.table_answer(lang, result.columns, result.rows, explanation)
        return AskResult(answer, answered=True, source="template", verified=True, unsupported=unsupported, **base)


def build_pipeline() -> Pipeline:
    from app.agent.llm import GeminiLLM
    from app.db import Database
    from app.examples import ExampleStore
    from app.resolve import Resolver
    from app.semantic import load_semantic, render_schema_prompt

    db = Database(config.DB_PATH)
    llm = GeminiLLM(config.GEMINI_API_KEY, config.GEMINI_MODEL, config.LLM_TIMEOUT_S)
    writer = Writer(llm, db, Resolver(db), ExampleStore.from_yaml(config.EXAMPLES_PATH),
                    render_schema_prompt(load_semantic()), config.MAX_TOOL_CALLS, config.MAX_FIX_RETRIES)
    return Pipeline(writer, llm, config.DEADLINE_S)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add app/answer/composer.py app/pipeline.py tests/test_pipeline.py
git commit -m "feat: composer and pipeline with verification fallback"
```

---
### Task 15: HTTP API and chat UI

**Files:**
- Create: `app/api.py`, `app/static/index.html`, `tests/test_api.py`

**Interfaces:**
- Consumes: `Pipeline.ask`, `build_pipeline`, `AskResult`
- Produces: `POST /ask` body `{"question": str (1..1000 chars), "previous": {"question": str, "sql": str} | null}` → `AskResult` as JSON; `GET /health` → `{"status": "ok"}`; `GET /` → chat page. Dependency `get_pipeline()` (overridable in tests).

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py`:
```python
from fastapi.testclient import TestClient

from app.api import app, get_pipeline
from app.pipeline import AskResult


class FakePipeline:
    def __init__(self):
        self.calls = []

    def ask(self, question, previous=None):
        self.calls.append((question, previous))
        return AskResult("Заключено 9 912 сделок.", "ru", True, "llm", sql="SELECT 1", columns=["n"],
                         rows=[[9912]], row_count=1)


def client(fake):
    app.dependency_overrides[get_pipeline] = lambda: fake
    return TestClient(app)


def test_ask_returns_answer_and_evidence():
    fake = FakePipeline()
    r = client(fake).post("/ask", json={"question": "Сколько сделок?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Заключено 9 912 сделок." and body["rows"] == [[9912]] and body["sql"] == "SELECT 1"
    assert fake.calls == [("Сколько сделок?", None)]


def test_previous_is_passed():
    fake = FakePipeline()
    client(fake).post("/ask", json={"question": "А в 2024?", "previous": {"question": "Сколько?", "sql": "SELECT 1"}})
    assert fake.calls[0][1] == {"question": "Сколько?", "sql": "SELECT 1"}


def test_validation():
    c = client(FakePipeline())
    assert c.post("/ask", json={"question": ""}).status_code == 422
    assert c.post("/ask", json={"question": "x" * 1001}).status_code == 422


def test_health_and_ui():
    c = client(FakePipeline())
    assert c.get("/health").json() == {"status": "ok"}
    page = c.get("/")
    assert page.status_code == 200 and "<form" in page.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api'`

- [ ] **Step 3: Implement `app/api.py`**

```python
"""HTTP API: POST /ask, GET /health, static chat UI."""
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.pipeline import Pipeline, build_pipeline

app = FastAPI(title="Exchange Q&A")


class Previous(BaseModel):
    question: str = Field(default="", max_length=1000)
    sql: str = Field(default="", max_length=20000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    previous: Previous | None = None


@lru_cache(maxsize=1)
def get_pipeline() -> Pipeline:
    return build_pipeline()


@app.post("/ask")
def ask(body: AskIn, pipeline: Pipeline = Depends(get_pipeline)) -> dict:
    previous = body.previous.model_dump() if body.previous else None
    return asdict(pipeline.ask(body.question.strip(), previous))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
```

- [ ] **Step 4: Create `app/static/index.html`**

```html
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Биржа — вопросы по данным / Birja — ma’lumotlar bo‘yicha savollar</title>
<style>
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 860px; margin: 0 auto; padding: 16px; }
  #log { display: flex; flex-direction: column; gap: 12px; margin-bottom: 96px; }
  .q { align-self: flex-end; background: #e8f0fe; padding: 8px 12px; border-radius: 12px; max-width: 80%; }
  .a { background: #f4f4f4; padding: 8px 12px; border-radius: 12px; white-space: pre-wrap; }
  .a.nodata { background: #fff4e5; }
  details { margin-top: 6px; font-size: 13px; }
  pre { white-space: pre-wrap; background: #fff; padding: 8px; border: 1px solid #ddd; }
  table { border-collapse: collapse; font-size: 13px; }
  td, th { border: 1px solid #ddd; padding: 2px 6px; }
  form { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; padding: 12px; border-top: 1px solid #ddd; }
  form > div { max-width: 860px; margin: 0 auto; display: flex; gap: 8px; }
  input { flex: 1; font: inherit; padding: 8px; }
  .meta { color: #777; font-size: 12px; }
</style>
</head>
<body>
<h1>Реестр сделок биржи · Birja bitimlari</h1>
<div id="log"></div>
<form id="f">
  <div>
    <input id="q" autocomplete="off" placeholder="Сколько сделок было в октябре 2025 года? / 2025-yilda nechta bitim tuzilgan?">
    <button>Спросить / So‘rash</button>
  </div>
</form>
<script>
const log = document.getElementById("log");
let previous = null;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function table(columns, rows) {
  const t = el("table");
  const head = el("tr");
  columns.forEach(c => head.appendChild(el("th", null, c)));
  t.appendChild(head);
  rows.slice(0, 50).forEach(r => {
    const tr = el("tr");
    r.forEach(v => tr.appendChild(el("td", null, v === null ? "—" : String(v))));
    t.appendChild(tr);
  });
  return t;
}

document.getElementById("f").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = document.getElementById("q");
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  log.appendChild(el("div", "q", question));
  const answer = el("div", "a", "…");
  log.appendChild(answer);
  answer.scrollIntoView();
  try {
    const r = await fetch("/ask", {method: "POST", headers: {"Content-Type": "application/json"},
                                   body: JSON.stringify({question, previous})});
    const body = await r.json();
    answer.textContent = body.answer || JSON.stringify(body.detail || body);
    if (!body.answered) answer.classList.add("nodata");
    if (body.sql) {
      const d = el("details");
      d.appendChild(el("summary", null, "SQL и данные / SQL va ma’lumotlar"));
      d.appendChild(el("pre", null, body.sql));
      d.appendChild(table(body.columns, body.rows));
      answer.appendChild(d);
      previous = {question, sql: body.sql};
    }
    answer.appendChild(el("div", "meta",
      `${body.source} · ${body.verified ? "verified" : "unverified"} · ${(body.timings || {}).total_s ?? "?"} s`));
  } catch (e) {
    answer.textContent = "Ошибка сети / Tarmoq xatosi: " + e;
  }
});
</script>
</body>
</html>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add app/api.py app/static tests/test_api.py
git commit -m "feat: HTTP API and chat UI"
```

---
### Task 16: Evaluation set and runner

**Files:**
- Create: `eval/__init__.py` (empty), `eval/questions.yaml`, `eval/run.py`, `tests/test_eval_set.py`

**Interfaces:**
- Consumes: `build_pipeline`, `Database`, `extract_numbers`, `normalize_name`, `to_jsonable`
- Produces: CLI `uv run python -m eval.run [--only q01,q02] [--workers N]` → prints per-question table + summary, writes `eval/results/run-<UTC timestamp>.json`, exits 1 if any hallucination or max latency > 90 s. Question fields: `id, lang, question, answerable, gold_sql, key_columns`.

Scoring (per answerable question): **SQL correctness** = every `key_columns` value of every gold row (≤5 rows) appears in the agent's result rows (numeric: within max(0.01, 1e-6·|x|)). **Answer correctness** = answered ∧ verified ∧ every key value appears in the answer text (numbers via displayed precision, strings via normalized substring). Unanswerable questions: correct iff `answered == false`. **Hallucination** = answered ∧ ¬verified, or answered on an unanswerable question.

- [ ] **Step 1: Write the failing test for the eval set itself**

`tests/test_eval_set.py`:
```python
import yaml

from app import config


def load():
    with open(config.ROOT / "eval" / "questions.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_eval_set_shape():
    qs = load()
    assert len(qs) == 60
    assert len({q["id"] for q in qs}) == 60
    assert sum(not q["answerable"] for q in qs) == 12
    assert {q["lang"] for q in qs} == {"ru", "uz"}


def test_gold_sql_runs_and_has_key_columns(db):
    for q in load():
        if not q["answerable"]:
            continue
        res = db.query(q["gold_sql"])
        assert res.rows, q["id"]
        assert set(q["key_columns"]) <= set(res.columns), q["id"]
        assert all(dict(zip(res.columns, r))[k] is not None for r in res.rows for k in q["key_columns"]), q["id"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_eval_set.py -v`
Expected: FAIL with `FileNotFoundError` for `eval/questions.yaml`

- [ ] **Step 3: Create `eval/questions.yaml`**

```yaml
- {id: q01, lang: ru, answerable: true, question: "Сколько сделок было заключено в октябре 2025 года?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2025 AND month = 10"}
- {id: q02, lang: ru, answerable: true, question: "Какая компания купила больше всего хлопка?", key_columns: [buyer_inn],
   gold_sql: "SELECT buyer_inn FROM deals WHERE product_canonical = 'Хлопок-сырец' AND NOT is_cancelled GROUP BY buyer_inn ORDER BY SUM(volume_tonnes) DESC LIMIT 1"}
- {id: q03, lang: ru, answerable: true, question: "Кто выступал брокером покупателя по сделке 387830?", key_columns: [buyer_broker],
   gold_sql: "SELECT buyer_broker FROM deals WHERE deal_id = 387830"}
- {id: q04, lang: ru, answerable: true, question: "Какие брокеры продавца работали по контракту 102267?", key_columns: [seller_broker],
   gold_sql: "SELECT seller_broker FROM deals WHERE contract_id = 102267 GROUP BY seller_broker ORDER BY COUNT(*) DESC LIMIT 5"}
- {id: q05, lang: ru, answerable: true, question: "Какова общая стоимость сделок в долларах?", key_columns: [total_usd_all, total_usd_valid],
   gold_sql: "SELECT SUM(amount_usd) AS total_usd_all, SUM(amount_usd) FILTER (WHERE NOT is_cancelled) AS total_usd_valid FROM deals"}
- {id: q06, lang: uz, answerable: true, question: "2025-yilda eng ko'p savdo qilgan korxona qaysi?", key_columns: [inn],
   gold_sql: "(SELECT buyer_inn AS inn FROM deals WHERE year = 2025 AND NOT is_cancelled GROUP BY buyer_inn ORDER BY SUM(amount_uzs) DESC LIMIT 1) UNION ALL (SELECT seller_inn FROM deals WHERE year = 2025 AND NOT is_cancelled GROUP BY seller_inn ORDER BY SUM(amount_uzs) DESC LIMIT 1)"}
- {id: q07, lang: uz, answerable: true, question: "Qaysi broker orqali eng katta hajmdagi bitimlar amalga oshirilgan?", key_columns: [broker_code],
   gold_sql: "WITH s AS (SELECT seller_broker AS broker_code, amount_uzs FROM deals WHERE NOT is_cancelled UNION ALL SELECT buyer_broker, amount_uzs FROM deals WHERE NOT is_cancelled) SELECT broker_code FROM s GROUP BY broker_code ORDER BY SUM(amount_uzs) DESC LIMIT 1"}
- {id: q08, lang: ru, answerable: true, question: "Сколько сделок было в 2023 году?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2023"}
- {id: q09, lang: uz, answerable: true, question: "2024-yilda nechta bitim tuzilgan?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2024"}
- {id: q10, lang: ru, answerable: true, question: "Сколько сделок было аннулировано в 2022 году?", key_columns: [cancelled],
   gold_sql: "SELECT COUNT(*) AS cancelled FROM deals WHERE year = 2022 AND is_cancelled"}
- {id: q11, lang: ru, answerable: true, question: "Какая общая сумма сделок в сумах за 2025 год?", key_columns: [total_uzs_all, total_uzs_valid],
   gold_sql: "SELECT SUM(amount_uzs) AS total_uzs_all, SUM(amount_uzs) FILTER (WHERE NOT is_cancelled) AS total_uzs_valid FROM deals WHERE year = 2025"}
- {id: q12, lang: ru, answerable: true, question: "Сколько экспортных сделок было в 2024 году?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2024 AND contract_type = 'Экспортный'"}
- {id: q13, lang: ru, answerable: true, question: "Какой был средний курс доллара в марте 2024 года?", key_columns: [avg_rate],
   gold_sql: "SELECT AVG(rate) AS avg_rate FROM usd_rates WHERE year(rate_date) = 2024 AND month(rate_date) = 3"}
- {id: q14, lang: uz, answerable: true, question: "15.06.2025 kuni dollar kursi qancha bo‘lgan?", key_columns: [rate],
   gold_sql: "SELECT rate FROM usd_rates WHERE rate_date = DATE '2025-06-15'"}
- {id: q15, lang: ru, answerable: true, question: "Сколько брокерских контор в реестре?", key_columns: [brokers],
   gold_sql: "SELECT COUNT(*) AS brokers FROM brokers"}
- {id: q16, lang: ru, answerable: true, question: "Сколько компаний-покупателей в реестре участников?", key_columns: [buyers],
   gold_sql: "SELECT COUNT(*) AS buyers FROM participants WHERE role = 'Покупатель'"}
- {id: q17, lang: uz, answerable: true, question: "102267-shartnoma bo‘yicha sotuvchi kim?", key_columns: [seller_inn],
   gold_sql: "SELECT seller_inn FROM contracts WHERE contract_id = 102267"}
- {id: q18, lang: ru, answerable: true, question: "Какой товар продаётся по контракту 100001?", key_columns: [product_name],
   gold_sql: "SELECT product_name FROM contracts WHERE contract_id = 100001"}
- {id: q19, lang: ru, answerable: true, question: "Назовите три региона с наибольшим числом сделок в 2025 году.", key_columns: [deals],
   gold_sql: "SELECT region, COUNT(*) AS deals FROM deals WHERE year = 2025 AND NOT is_cancelled GROUP BY region ORDER BY deals DESC LIMIT 3"}
- {id: q20, lang: ru, answerable: true, question: "Сколько тонн хлопка-сырца продано в 2025 году?", key_columns: [tonnes_valid],
   gold_sql: "SELECT SUM(volume_tonnes) AS tonnes_valid FROM deals WHERE product_canonical = 'Хлопок-сырец' AND year = 2025 AND NOT is_cancelled"}
- {id: q21, lang: uz, answerable: true, question: "2025-yilda paxta xom ashyosining o‘rtacha narxi bir kilogramm uchun qancha bo‘lgan?", key_columns: [avg_price_uzs_per_kg],
   gold_sql: "SELECT SUM(amount_uzs) / (SUM(volume_tonnes) * 1000) AS avg_price_uzs_per_kg FROM deals WHERE product_canonical = 'Хлопок-сырец' AND year = 2025 AND NOT is_cancelled"}
- {id: q22, lang: ru, answerable: true, question: "Какой брокер провёл больше всего сделок на стороне покупателя в 2024 году?", key_columns: [buyer_broker],
   gold_sql: "SELECT buyer_broker FROM deals WHERE year = 2024 AND NOT is_cancelled GROUP BY buyer_broker ORDER BY COUNT(*) DESC LIMIT 1"}
- {id: q23, lang: ru, answerable: true, question: "Сколько сделок исполнено частично в 2026 году?", key_columns: [partial],
   gold_sql: "SELECT COUNT(*) AS partial FROM deals WHERE year = 2026 AND status = 'Исполнен(Частично)'"}
- {id: q24, lang: ru, answerable: true, question: "Какая самая крупная сделка 2023 года по сумме в сумах?", key_columns: [deal_id, amount_uzs],
   gold_sql: "SELECT deal_id, amount_uzs FROM deals WHERE year = 2023 AND NOT is_cancelled ORDER BY amount_uzs DESC LIMIT 1"}
- {id: q25, lang: ru, answerable: true, question: "Сколько сделок заключено на двустороннем аукционе в 2025 году?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2025 AND trade_type = 'Двусторонний аукцион'"}
- {id: q26, lang: ru, answerable: true, question: "Сколько форвардных сделок по хлопку-сырцу было в 2024 году?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2024 AND contract_form = 'Форвард' AND product_canonical = 'Хлопок-сырец'"}
- {id: q27, lang: uz, answerable: true, question: "2025-yil yanvar oyida Samarqand viloyatida nechta bitim tuzilgan?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2025 AND month = 1 AND region = 'Samarqand viloyati'"}
- {id: q28, lang: ru, answerable: true, question: "Какая доля сделок была аннулирована в 2025 году?", key_columns: [cancelled_pct],
   gold_sql: "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE is_cancelled) / COUNT(*), 2) AS cancelled_pct FROM deals WHERE year = 2025"}
- {id: q29, lang: ru, answerable: true, question: "Сколько контрактов было зарегистрировано в 2024 году?", key_columns: [contracts],
   gold_sql: "SELECT COUNT(*) AS contracts FROM contracts WHERE year(registered_date) = 2024"}
- {id: q30, lang: ru, answerable: true, question: "Сколько разных компаний покупали бензин в 2025 году?", key_columns: [buyers_valid],
   gold_sql: "SELECT COUNT(DISTINCT buyer_inn) AS buyers_valid FROM deals WHERE product_canonical = 'Бензин автомобильный' AND year = 2025 AND NOT is_cancelled"}
- {id: q31, lang: ru, answerable: true, question: "Какая компания продала цемента на наибольшую сумму в 2024 году?", key_columns: [seller_inn],
   gold_sql: "SELECT seller_inn FROM deals WHERE product_canonical = 'Портландцемент' AND year = 2024 AND NOT is_cancelled GROUP BY seller_inn ORDER BY SUM(amount_uzs) DESC LIMIT 1"}
- {id: q32, lang: uz, answerable: true, question: "2023-yilda kaustik soda bo‘yicha nechta bitim bo‘lgan?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2023 AND product_canonical = 'Сода каустическая'"}
- {id: q33, lang: ru, answerable: true, question: "Сколько сделок было заключено 30.10.2026?", key_columns: [deals_all],
   gold_sql: "SELECT COUNT(*) AS deals_all FROM deals WHERE deal_date = DATE '2026-10-30'"}
- {id: q34, lang: ru, answerable: true, question: "Какой месяц 2025 года был самым активным по числу сделок?", key_columns: [deals],
   gold_sql: "SELECT month, COUNT(*) AS deals FROM deals WHERE year = 2025 AND NOT is_cancelled GROUP BY month ORDER BY deals DESC LIMIT 1"}
- {id: q35, lang: ru, answerable: true, question: "Какой средний срок оплаты по контрактам в днях?", key_columns: [avg_payment_term_days],
   gold_sql: "SELECT AVG(payment_term_days) AS avg_payment_term_days FROM contracts"}
- {id: q36, lang: uz, answerable: true, question: "BK001 broker 2025-yilda nechta bitimda ishtirok etgan?", key_columns: [deals_all],
   gold_sql: "SELECT COUNT(*) AS deals_all FROM deals WHERE year = 2025 AND (seller_broker = 'BK001' OR buyer_broker = 'BK001')"}
- {id: q37, lang: ru, answerable: true, question: "Сколько участников являются плательщиками НДС?", key_columns: [vat_payers],
   gold_sql: "SELECT COUNT(*) AS vat_payers FROM participants WHERE vat_payer"}
- {id: q38, lang: ru, answerable: true, question: "Какова сумма экспортных сделок в долларах за 2025 год?", key_columns: [total_usd_all, total_usd_valid],
   gold_sql: "SELECT SUM(amount_usd) AS total_usd_all, SUM(amount_usd) FILTER (WHERE NOT is_cancelled) AS total_usd_valid FROM deals WHERE year = 2025 AND contract_type = 'Экспортный'"}
- {id: q39, lang: ru, answerable: true, question: "Какой товар принёс наибольшую сумму сделок в 2025 году?", key_columns: [product_canonical],
   gold_sql: "SELECT product_canonical FROM deals WHERE year = 2025 AND NOT is_cancelled GROUP BY product_canonical ORDER BY SUM(amount_uzs) DESC LIMIT 1"}
- {id: q40, lang: uz, answerable: true, question: "2026-yilda eng ko‘p bitim tuzgan sotuvchi kompaniya qaysi?", key_columns: [seller_inn],
   gold_sql: "SELECT seller_inn FROM deals WHERE year = 2026 AND NOT is_cancelled GROUP BY seller_inn ORDER BY COUNT(*) DESC LIMIT 1"}
- {id: q41, lang: ru, answerable: true, question: "Сколько сделок по карбамиду было в 2024 году и на какую сумму?", key_columns: [deals_valid, total_uzs_valid],
   gold_sql: "SELECT COUNT(*) AS deals_valid, SUM(amount_uzs) AS total_uzs_valid FROM deals WHERE product_canonical = 'Карбамид' AND year = 2024 AND NOT is_cancelled"}
- {id: q42, lang: ru, answerable: true, question: "Сколько сделок сейчас в статусе «Оплачен»?", key_columns: [paid],
   gold_sql: "SELECT COUNT(*) AS paid FROM deals WHERE status = 'Оплачен'"}
- {id: q43, lang: ru, answerable: true, question: "Сколько лотов поставлено по частично исполненным сделкам 2025 года?", key_columns: [lots_delivered],
   gold_sql: "SELECT SUM(delivered_lots) AS lots_delivered FROM deals WHERE status = 'Исполнен(Частично)' AND year = 2025"}
- {id: q44, lang: uz, answerable: true, question: "2024-yilda qaysi viloyatda bitimlar summasi eng katta bo‘lgan?", key_columns: [total_uzs],
   gold_sql: "SELECT region, SUM(amount_uzs) AS total_uzs FROM deals WHERE year = 2024 AND NOT is_cancelled GROUP BY region ORDER BY total_uzs DESC LIMIT 1"}
- {id: q45, lang: ru, answerable: true, question: "Какая средняя сумма сделки в 2022 году в сумах?", key_columns: [avg_all, avg_valid],
   gold_sql: "SELECT AVG(amount_uzs) AS avg_all, AVG(amount_uzs) FILTER (WHERE NOT is_cancelled) AS avg_valid FROM deals WHERE year = 2022"}
- {id: q46, lang: ru, answerable: true, question: "Сколько сделок по дизельному топливу было в 2026 году?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2026 AND product_canonical = 'Дизельное топливо'"}
- {id: q47, lang: ru, answerable: true, question: "Кто покупатель по сделке 504708?", key_columns: [buyer_inn],
   gold_sql: "SELECT buyer_inn FROM deals WHERE deal_id = 504708"}
- {id: q48, lang: uz, answerable: true, question: "2026-yilning birinchi choragida nechta bitim tuzilgan?", key_columns: [deals_all, deals_valid],
   gold_sql: "SELECT COUNT(*) AS deals_all, COUNT(*) FILTER (WHERE NOT is_cancelled) AS deals_valid FROM deals WHERE year = 2026 AND month BETWEEN 1 AND 3"}
- {id: q49, lang: ru, answerable: false, question: "Сколько сделок было в 2021 году?"}
- {id: q50, lang: ru, answerable: false, question: "Какая прибыль была у компании «Pudexiz Plast» в 2025 году?"}
- {id: q51, lang: ru, answerable: false, question: "Кто директор компании «Pudexiz Plast»?"}
- {id: q52, lang: ru, answerable: false, question: "Какой будет курс доллара в декабре 2026 года?"}
- {id: q53, lang: ru, answerable: false, question: "Сколько сотрудников у брокера BK001?"}
- {id: q54, lang: uz, answerable: false, question: "Qaysi kompaniya eng ko‘p oltin sotgan?"}
- {id: q55, lang: ru, answerable: false, question: "Сколько сделок по сырой нефти было в 2024 году?"}
- {id: q56, lang: ru, answerable: false, question: "Какой кредитный рейтинг у компании с ИНН 915688255?"}
- {id: q57, lang: uz, answerable: false, question: "2027-yilda nechta bitim bo‘ladi?"}
- {id: q58, lang: ru, answerable: false, question: "Каков был курс евро на 01.01.2024?"}
- {id: q59, lang: ru, answerable: false, question: "Сколько сделок заключил брокер BK999?"}
- {id: q60, lang: uz, answerable: false, question: "BK001 brokerining elektron pochtasi qanday?"}
```

- [ ] **Step 4: Run the eval-set test and inspect gold results**

Run: `uv run pytest tests/test_eval_set.py -v`
Expected: 2 passed. Then print each gold result (`uv run python -c "..."` looping over the YAML with `Database(config.DB_PATH).query`) and read them: every gold answer must be plausible (e.g. q15 = 120, q42 = 5 854). A gold query that looks wrong is fixed now — the eval is only as good as its gold SQL.

- [ ] **Step 5: Implement `eval/run.py`**

```python
"""Evaluate the agent: python -m eval.run [--only q01,q02] [--workers 2]"""
import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import yaml

from app import config
from app.answer.numbers import extract_numbers
from app.db import Database, to_jsonable
from app.pipeline import build_pipeline
from app.text import normalize_name

LATENCY_LIMIT_S = 90


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def value_in_rows(gold, rows) -> bool:
    if _is_num(gold):
        return any(_is_num(c) and abs(float(c) - gold) <= max(0.01, 1e-6 * abs(gold)) for r in rows for c in r)
    return any(c is not None and str(c) == str(gold) for r in rows for c in r)


def value_in_text(gold, text: str) -> bool:
    if _is_num(gold):
        return any(abs(t.value - gold) <= t.tolerance + 1e-6 * abs(gold) for t in extract_numbers(text))
    g = normalize_name(str(gold))
    return (g in normalize_name(text)) if g else str(gold) in text


def evaluate(q: dict, pipeline, db: Database) -> dict:
    t0 = time.monotonic()
    res = pipeline.ask(q["question"])
    latency = time.monotonic() - t0
    row = {"id": q["id"], "lang": q["lang"], "question": q["question"], "answerable": q["answerable"],
           "latency_s": round(latency, 2), "result": asdict(res)}
    if q["answerable"]:
        gold = db.query(q["gold_sql"])
        keys = [[to_jsonable(dict(zip(gold.columns, r))[k]) for k in q["key_columns"]] for r in gold.rows[:5]]
        flat = [v for r in keys for v in r]
        row["gold_keys"] = flat
        row["sql_ok"] = res.answered and all(value_in_rows(v, res.rows) for v in flat)
        row["answer_ok"] = res.answered and res.verified and all(value_in_text(v, res.answer) for v in flat)
        row["hallucination"] = res.answered and not res.verified
    else:
        row["sql_ok"] = None
        row["answer_ok"] = not res.answered
        row["hallucination"] = res.answered
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()
    with open(config.ROOT / "eval" / "questions.yaml", encoding="utf-8") as f:
        questions = yaml.safe_load(f)
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    pipeline, db = build_pipeline(), Database(config.DB_PATH)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(lambda q: evaluate(q, pipeline, db), questions))

    for r in rows:
        mark = lambda v: "-" if v is None else ("ok" if v else "FAIL")
        print(f"{r['id']} {r['lang']} sql={mark(r['sql_ok']):4} answer={mark(r['answer_ok']):4} "
              f"halluc={'YES' if r['hallucination'] else 'no':3} {r['latency_s']:5.1f}s "
              f"[{r['result']['source']}] {r['question'][:60]}")
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    latencies = sorted(r["latency_s"] for r in rows)
    summary = {
        "questions": len(rows),
        "sql_correct": f"{sum(bool(r['sql_ok']) for r in answerable)}/{len(answerable)}",
        "answer_correct": f"{sum(r['answer_ok'] for r in answerable)}/{len(answerable)}",
        "refused_correctly": f"{sum(r['answer_ok'] for r in unanswerable)}/{len(unanswerable)}",
        "hallucinations": sum(r["hallucination"] for r in rows),
        "latency_p50_s": latencies[len(latencies) // 2] if latencies else None,
        "latency_p95_s": latencies[int(len(latencies) * 0.95) - 1] if latencies else None,
        "latency_max_s": latencies[-1] if latencies else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out_dir = config.ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"run-{stamp}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    bad = summary["hallucinations"] > 0 or (summary["latency_max_s"] or 0) > LATENCY_LIMIT_S
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Smoke-run on three questions (needs `GEMINI_API_KEY`)**

Run: `GEMINI_API_KEY=... uv run python -m eval.run --only q01,q15,q49`
Expected: three lines plus a summary; q49 shows `answer=ok` (refused). Failures here are prompt/spec issues to investigate in Task 17, not reasons to change the scoring.

- [ ] **Step 7: Commit**

```bash
git add eval tests/test_eval_set.py
git commit -m "feat: evaluation set with gold SQL and two-axis scoring"
```

---
### Task 17: Docker, README, full evaluation

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: an image serving the app on port 8000; the first full eval report.

- [ ] **Step 1: Create `.dockerignore`**

```
.venv
build
eval/results
.git
**/__pycache__
docs
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY app app
COPY data data
COPY data_dictionary data_dictionary
COPY examples examples
RUN .venv/bin/python -m app.ingest.build
EXPOSE 8000
CMD [".venv/bin/uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

- [ ] **Step 3: Create `README.md`**

````markdown
# Exchange Q&A agent

Answers Russian and Uzbek questions about the exchange deal registry in `data/`. Every fact in an answer is
checked against the database result; if the data has no answer, the service says so.

## Run locally

```bash
uv sync
uv run python -m app.ingest.build
GEMINI_API_KEY=... uv run uvicorn app.api:app --port 8000
```

Open http://localhost:8000 or call the API:

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' -d '{"question": "Сколько сделок было в октябре 2025 года?"}'
```

## Run with Docker

```bash
docker build -t uzex-qa .
docker run -p 8000:8000 -e GEMINI_API_KEY=... uzex-qa
```

## Tests and evaluation

```bash
uv run pytest
GEMINI_API_KEY=... uv run python -m eval.run --workers 2
```

`eval.run` reports SQL correctness, answer correctness, correct refusals, hallucinations (target 0) and
latency (limit 90 s), and writes `eval/results/run-*.json`.

## Settings

| Env | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | — | required |
| `GEMINI_MODEL` | `gemini-2.5-flash` | model for planning and answer writing |
| `UZEX_DEADLINE_S` | `75` | hard deadline per question |
| `UZEX_LLM_TIMEOUT_S` | `25` | per-call LLM timeout |
````

- [ ] **Step 4: Run the whole unit suite**

Run: `uv run pytest`
Expected: all tests pass (live tests deselected).

- [ ] **Step 5: Build and smoke-test the image**

Run: `docker build -t uzex-qa . && docker run -d --rm -p 8000:8000 -e GEMINI_API_KEY="$GEMINI_API_KEY" --name uzex uzex-qa && sleep 5 && curl -s localhost:8000/health && curl -s localhost:8000/ask -H 'Content-Type: application/json' -d '{"question":"Сколько брокерских контор в реестре?"}'; docker stop uzex`
Expected: `{"status":"ok"}` then a JSON answer containing `120`.

- [ ] **Step 6: Run the full evaluation**

Run: `GEMINI_API_KEY=... uv run python -m eval.run --workers 2`
Expected: summary JSON. Acceptance targets: `hallucinations: 0`, `latency_max_s < 90`, refusals 12/12. Record the SQL and answer correctness numbers in the commit message.

For every failing question, open its row in the results JSON and classify the cause before changing anything:
- wrong/missing entity → resolver or dictionary (Task 2/5)
- right entities, wrong spec → semantic layer rules or a new example (Task 9)
- no spec could express it → new measure/dimension in the compiler with a test (Tasks 6–7)
- right rows, answer rejected → composer prompt (Task 14); never loosen the verifier to pass

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore README.md
git commit -m "feat: Docker image, README and first full evaluation"
```
