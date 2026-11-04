# Exchange Q&A Agent — Design

Date: 2026-11-04
Status: approved in brainstorming, pending spec review

## 1. Goal

A web service with an AI agent that answers questions in **Russian and Uzbek** (Latin and Cyrillic) about the
commodity-exchange deal registry in `data/` (fictional data, 2022-01-03 … 2026-10-30).

Hard requirements:

1. **Accurate, grounded answers.** Exact numbers, names, INNs, broker codes, contract numbers — all taken from
   query results.
2. **No hallucination.** If the data has no answer, the agent says so plainly. Inventing a number, company,
   product or code is a failure, worse than not answering.
3. **≤ 90 s per answer**, end to end.

Two problems are measured and engineered separately:

- **SQL correctness** — the right context was retrieved and the right SQL was generated and executed.
- **Answer correctness** — the final text faithfully represents what the database returned.

Out of scope: multi-turn memory beyond the previous question, charts, authentication, writes to data.

## 2. Data facts that shape the design

From `data/README.md` and profiling:

- 500 000 deals in `deals_2022.csv … deals_2026.csv`; 6 616 contracts; 14 392 participants; 120 brokers;
  daily USD rate.
- Numbers are text with space thousands separators (`304 547 938.4`); dates are `ДД.ММ.ГГГГ`.
- Statuses: Исполнен 431 061 · Аннулирован(Не оплачен) 33 719 · Исполнен(Частично) 22 648 · Оплачен 5 854 ·
  Заключен 3 954 · Ждёт оплату 1 714 · Новый 964 · Не подписан 86. Annulled deals did not take place.
- Currency is a contract property: 148 contracts / 6 590 deals in USD (export), the rest in UZS.
  USD↔UZS conversion uses the rate on the deal date.
- Volume = Количество × Лот (in the unit of measure). Unit price = Сумма ÷ volume.
- Text is inconsistent:
  - Company names mix Cyrillic and Latin (`"ҲАРАЙЎМ КИМЁ" МЧЖ`, `"Pudexiz Plast" MChJ`).
  - Regions are Uzbek Latin in deals/participants/brokers (`Namangan viloyati`) but Russian in contracts
    (`Ферганская область`).
  - Product names are seller-written free text: ≥12 spellings of raw cotton, `Хлопковое масло` vs
    `Масло хлопковое`, upper case, attributes inline or in separate columns.
  - `Группа товара` has only 2 values — useless for product filtering.

## 3. Architecture

Adapted from LinkedIn's SQL Bot. Kept: semantic metadata, curated SQL examples, query writer, validators,
limited fixer, value resolution (extended). Dropped: table/column retrieval and ranking (5 tables fit in the
prompt). Merged: researcher agent → exploration tools of the writer. Added: deterministic answer verifier and a
two-axis evaluation harness.

```
question
  → language detect + intent (answerable / out-of-scope / chit-chat)
  → writer agent loop (Gemini, tools: resolve, get_examples, explore_sql)
  → validators → fixer (≤2 retries)
  → execute (read-only DuckDB)
  → answer composer (Gemini, rows only)
  → answer verifier (deterministic) → regenerate once → template fallback
  → response {answer, sql, rows, assumptions}
```

### 3.1 Data layer (`ingest`, offline, once)

Loads CSVs into a DuckDB file with English snake_case columns, typed numbers/dates.

Tables: `deals`, `contracts`, `participants`, `brokers`, `usd_rates`, `products` (canonical dictionary),
`regions` (RU↔UZ map).

Derived columns on `deals`:

| column | definition |
|---|---|
| `volume` | quantity × lot |
| `unit_price` | amount / volume |
| `currency` | from contract (`UZS` / `USD`) |
| `usd_rate` | rate on deal_date |
| `amount_uzs` | amount if UZS else amount × usd_rate |
| `amount_usd` | amount if USD else amount / usd_rate |
| `is_completed` | status ≠ `Аннулирован(Не оплачен)` |
| `product_id` | FK to `products` via contract |
| `year`, `month` | from deal_date |

Name normalization columns (`name_norm`) on participants and brokers: lower case, Cyrillic→Latin
transliteration (Uzbek rules incl. ў→o‘, қ→q, ғ→g‘, ҳ→h), legal-form tokens unified (МЧЖ/MChJ/ООО/
MAS’ULIYATI CHEKLANGAN JAMIYATI → `mchj`), quotes and punctuation stripped.

**Canonical product dictionary** (`products`): every distinct contract product string maps to
`product_canonical` (e.g. `Хлопок-сырец`), `product_family` (e.g. `хлопок`, to scope by-products), and
parsed `grade`, `class`, `brand`, `size`, `form`. Built by a rule pass plus a Gemini-assisted pass, written to a
reviewed CSV checked into the repo (`data_dictionary/products.csv`); ingest reads the reviewed CSV, never calls
an LLM at runtime.

The runtime DB connection is read-only.

### 3.2 Semantic layer (`semantic.yaml`)

Single file, injected whole into the writer prompt:

- Table and column descriptions in RU and UZ, with synonyms (`bitim`/`сделка`, `xaridor`/`покупатель`,
  `paxta`/`хлопок`, …).
- Business rules: volume, unit price, currency conversion, status meanings, join keys, date range of data.
- **Fixed ambiguity registry** — the only axes on which answers may split:
  1. `status_scope`: all deals vs completed (excluding annulled)
  2. `measure`: volume vs value
  3. `currency`: UZS vs USD (converted at deal-date rate)
  4. `product_scope`: canonical product vs whole product family (e.g. raw cotton vs cotton incl. meal/oil/yarn)
  5. `role`: as buyer vs as seller (for "traded the most" questions)
- Defaults for each axis, used when a split is not shown.

### 3.3 Value resolution — `resolve(kind, text)`

`kind ∈ {company, broker, product, region, contract}`. Normalizes input the same way as ingest, then:
exact ID match (INN, broker code, contract number) → exact normalized match → trigram/Jaro-Winkler fuzzy.
Returns up to 10 candidates `{id, display_name, score, deal_count}`.

Rules enforced by validators: SQL filters on entities must use IDs/canonical values returned by `resolve` in
this request, never raw user text.

If one candidate clearly wins (score gap threshold), use it. If several are close, the agent either answers
per candidate (≤5) or lists them with INNs and asks which was meant.

### 3.4 Writer agent

Gemini with function calling. Tools:

- `resolve(kind, text)` — above.
- `get_examples(question)` — top-k (k=5) curated question→SQL pairs by embedding similarity
  (Gemini embeddings, precomputed). ~80 examples in RU and UZ covering the ambiguity registry.
- `explore_sql(sql)` — read-only, `LIMIT 50` enforced, 5 s timeout; for DISTINCT values / sanity checks.
- `submit_sql(sql, assumptions, splits)` — final query plus which ambiguity axes it splits on (≤2) and which
  defaults it applied.

**Ambiguity policy:** compute every relevant interpretation in **one** SQL (conditional aggregation /
GROUP BY), up to 2 split axes; remaining axes use defaults and are stated. If a split does not change the
result (same top company by volume and value), the answer states it once.

Intent pre-step: questions outside the dataset (profit, CEO, years outside 2022–2026, other exchanges,
forecasts) get a fixed "no data" reply without SQL.

### 3.5 Validators and fixer

Validators on submitted SQL (sqlglot parse, DuckDB dialect):

- single SELECT/WITH statement; no DDL/DML; only known tables/columns
- row limit present (≤1000)
- money aggregation over `amount` without currency handling → reject (must use `amount_uzs`/`amount_usd`
  or group by currency)
- entity filters use resolved IDs
- `EXPLAIN` succeeds

Execution errors or validator failures go back to the writer with the error message. Max 2 fix retries.

### 3.6 Answer composer and verifier

Composer: Gemini receives the question, language, executed SQL, result rows (truncated to 200 rows with total
count), assumptions and splits. Instructed to use only values present in rows, keep IDs exact, state
assumptions in one line, reply in the question's language and script.

Verifier (deterministic Python):

- Extract numbers (handling space/nbsp thousands separators, decimal comma/point, "млн/mln/млрд/mlrd"),
  INNs (9 digits), broker codes (`BK\d{3}`), contract numbers, dates, and quoted/known entity names.
- Each must match a value in the rows (numbers: exact or within rounding of displayed precision; scaled units
  allowed) or appear in the question / assumptions.
- Empty result set → answer must be the "no data" template.

On failure: regenerate once with the list of unsupported tokens. On second failure: template answer built
directly from the rows (table-like text), no free generation.

"No data" template: RU «В данных нет ответа на этот вопрос.» / UZ «Ma’lumotlarda bu savolga javob yo‘q.»
optionally followed by what the data does cover.

### 3.7 Time budget

| stage | budget |
|---|---|
| intent + language | 5 s |
| writer loop incl. tools and fixes | 50 s |
| execution | 5 s |
| compose + verify (+1 regen) | 15 s |
| **hard deadline** | **75 s**, then honest "could not answer in time" |

All Gemini calls have per-call timeouts; DuckDB queries have statement timeouts.

## 4. Web service

- FastAPI. `POST /ask {question, previous?}` →
  `{answer, lang, sql, rows, row_count, assumptions, splits, verified, timings}`.
- `GET /health`.
- Single static HTML page: chat box, answer, collapsible "SQL & rows" panel.
- Docker image with the DuckDB file built at image build time from `data/` + `data_dictionary/`.
- Config via env: `GEMINI_API_KEY`, model names, timeouts.

## 5. Evaluation

`eval/questions.yaml`: ~60 questions, RU and UZ (Latin; a few Cyrillic), each with:

- `gold_sql` — hand-verified
- `expected` — result rows from `gold_sql`
- `must_mention` — key facts (numbers/IDs) the answer must contain
- `answerable: false` for ~10 questions whose answer is not in the data

Coverage: counts by period, sums with currency conversion, top-N rankings, broker/contract lookups, entity
resolution by messy names, product-family scoping, ambiguity splits, joins across all tables, unanswerable
questions.

`eval/run.py` reports per question and in aggregate:

- **SQL correctness** — execution match of agent rows vs `expected` (order-insensitive unless ranked,
  numeric tolerance 1e-6 relative).
- **Answer correctness** — verifier pass, `must_mention` coverage, and for unanswerable questions: refusal
  given and no invented entities.
- **Hallucination count** — any unsupported number/name in a final answer (target: 0).
- **Latency** — p50/p95/max (target: max < 90 s).

## 6. Components and layout

```
app/
  ingest/        build DuckDB, normalization, transliteration
  semantic.yaml
  resolve.py     value resolution
  agent/         intent, writer loop, tools, validators, fixer
  answer/        composer, verifier, templates
  api.py         FastAPI
  static/        chat UI
data_dictionary/ products.csv, regions.csv (reviewed)
examples/        curated question→SQL pairs
eval/            questions.yaml, run.py
Dockerfile
```

Each unit is testable alone: ingest (row counts, derived columns), resolve (known messy names → INN),
validators (bad SQL rejected), verifier (fabricated numbers caught), eval end-to-end.

## 7. Risks

- **Product dictionary quality** directly limits product-question accuracy → reviewed CSV, tests on known
  spellings.
- **Gemini latency variance** → hard deadline, small tool budget (≤6 tool calls per question).
- **Verifier false rejections** on derived numbers (percentages, differences) → composer may only state
  numbers computed in SQL; SQL computes derived values as columns.
