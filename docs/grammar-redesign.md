# Redesign: a small, general query grammar

> **Status (2026-11-05):** implemented and the default (`PLANNER=grammar`). Beyond this design it gained a
> mention check (`app/plan/mentions.py`), answers rendered by code from templates (`app/answer/plan_templates.py`,
> no model-written answer text), day-level compare periods, and the missing-condition checks ported from the
> legacy guard. Measured on the realistic set with OpenAI: tuning half 139/153 right, 0 wrong; held-out half
> (scored once) 130/150 right, 10 wrong, 23/30 no-answer questions refused. The README describes the current
> architecture.

## Why

The structured form grew one feature at a time: 7 query kinds, ~30 measures, ~30 groupings, ~25 separately
named filter fields, a 28 000-character planner prompt with 57 examples, and a 350-line keyword guard. A strong
model copes; a small one gives up (local 7B: 7/20), and even strong models lose details (year ranges, silent
top-3). The data is small — five tables. The model's job should be small too.

## The grammar

The planner fills one plan, the same shape for every question:

```json
{
  "subject":  "deals",
  "measures": [{"agg": "sum", "of": "tonnes"}, {"agg": "sum", "of": "amount_uzs"}],
  "filters":  [{"field": "product", "op": "=", "value": "Хлопок-сырец"},
               {"field": "year", "op": "between", "value": [2022, 2026]}],
  "group_by": ["buyer"],
  "order": "desc",
  "limit": 3
}
```

- **subject**: deals · contracts · companies · brokers · usd_rates
- **measures** (1–2): `count`, `sum`, `avg`, `median`, `min`, `max`, `distinct` over a field, plus a few named
  ones defined once in the dictionary (`price_per_kg`, `share`, `days_to_payment`, …). `share` takes its own
  filter list for the numerator.
- **filters**: a list of `{field, op, value}`; `op` is one of `=`, `in`, `!=`, `between`, `>=`, `<=`.
- **group_by** (0–2): any dimension field of the subject, or a time grain (`year`, `quarter`, `month`, `day`,
  `hour`). `group_by: ["deal"]` / `["contract"]` lists individual records (largest deal, longest contract).
- **order**, **limit**: for rankings; "least/cheapest" is just `order: asc`.
- **compare** (optional): two periods side by side with the change.

## The field dictionary (one file, replaces most of spec.py and sql.py)

`app/fields.yaml` declares every field per subject once:

```yaml
deals:
  table: deals d
  fields:
    product:   {type: dimension, sql: d.product_canonical, ground: product, label: {ru: товар, uz: tovar}}
    buyer:     {type: dimension, sql: d.buyer_inn, display: participants.name, ground: company}
    region:    {type: dimension, sql: d.region, ground: region}
    category:  {type: dimension, sql: d.category, ground: catalog}
    tonnes:    {type: number, sql: d.volume_tonnes, aggs: [sum, avg, median, min, max]}
    amount_uzs: {type: number, sql: d.amount_uzs, aggs: [sum, avg, median, min, max]}
    deal_date: {type: date, sql: d.deal_date}
  measures:
    price_per_kg: {sql: "SUM(d.amount_uzs) FILTER (WHERE d.volume_tonnes IS NOT NULL) / NULLIF(SUM(d.volume_tonnes) * 1000, 0)"}
contracts:
  table: contracts c
  fields: {product: …, seller: …, registered_date: {type: date}, payment_term_days: {type: number}, …}
```

Adding a capability = adding a line here, not new code. Contracts, companies, brokers and rates get counts,
rankings, trends, distinct counts and extremes from the same builder as deals.

## What code does

1. **Validate** the plan against the dictionary: subject exists; every field exists for that subject; the op
   fits the field type; the agg fits the field; every value is grounded (`resolve()` result, catalog value, or
   a number/date in range). One generic validator instead of per-field rules.
2. **Check consistency** (generic, language-independent): grouping by a field that is filtered to one value;
   `distinct` of a field that is also grouped; a single-year filter with `group_by: year`; a share whose
   numerator equals its denominator; `limit` missing on a large categorical grouping.
3. **Build SQL** with one generic builder: `SELECT <group cols>, <measures> FROM <table + joins> WHERE
   <filters> GROUP BY … ORDER BY … LIMIT …`. Business rules stay as defaults in the dictionary: money in so'm
   with USD converted at the deal-date rate, annulled deals excluded from rankings and shown separately in
   counts and totals.
4. **Explain** generically from the plan using the dictionary labels: "Расчёт: сделки; товар = Хлопок-сырец;
   2022–2026; по покупателю; топ-3 по тоннам и по сумме".

Kept as they are: name lookup (`resolve.py`), grounding of IDs, the answer checker, the time budget, the
LLM provider fallback chain, the evaluation sets. The keyword guard shrinks to the generic checks in step 2 plus the
period check (a month or date named in the question must appear in the plan).

## Prompt size

The planner sees: the grammar (short), the dictionary rendered compactly (field names + labels + allowed
aggs), the business rules, and ~20 short examples in the new grammar. Target: under 10 000 characters, about a
third of today. Cheaper per question, and a fair chance for small local models.

## Mentions (step 2)

After "Mention Extraction and Linking for SQL Query Generation" (EMNLP 2020). The plan carries
`mentions: [{text, used_as}]`: each phrase of the question the planner used, copied exactly, and the element it
sets (`filter:<field>`, `group_by:<field>`, `measure:<i>`, `order`, `limit`, `subject`, `compare`).
`app/plan/mentions.py` checks that each mention is in the question (normalize_text: case, whitespace, Cyrillic or
Latin), points to an element of the plan, that every filter and group_by is linked to a mention (a resolved value
through the resolve() call of its phrase), that the numbers of a mention agree with the plan (years of a period
phrase, the limit of "топ-3", a threshold), and that every number, date, id and catalog value of the question is
in some mention. Failures go back to the planner as fix instructions. The legacy missing-condition guard also runs
on plans (`checks.plan_missing_condition_error`), and a year or month named in the question must be inside the
plan's period.

## Answers by code (step 2)

In grammar mode no model writes answer text: `app/answer/plan_templates.py` renders the answer from the plan, the
rows and the dictionary labels (ru, uz, uz-Cyrl) for every shape, and appends the explanation. Shapes without a
template fall back to the row table. The answer checker still runs and passes by construction.

## How we switch safely

1. Build the new planner path next to the old one (a setting chooses which).
2. Measure on the realistic tuning half: first on the free local model, then a small OpenAI run (cost stated
   first). Compare with today's baseline (111/153, 33 wrong).
3. Switch when it is at least as accurate with no more wrong answers; then delete the old kinds, templates and
   most of the guard.
4. Score the held-out half once.
