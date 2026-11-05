# Realistic evaluation set — design

Goal: measure how the agent does on questions phrased the way real users ask them, in Russian and Uzbek
(Latin and Cyrillic), against expected answers derived from the current definitions — without tuning to
individual questions.

## Principles

1. **Natural phrasing only.** No column or table names, no English hints, no SQL words. A linter rejects
   questions containing schema identifiers (`product_base`, `deal_id`, `amount_uzs`, `group_by`, …).
2. **Intents, not questions.** Each item is an *intent* (what is being asked) with one hand-checked answer and
   three phrasings: Russian, Uzbek Latin, Uzbek Cyrillic. Phrasings vary in style (short, conversational,
   formal, with typos or Russian words mixed into Uzbek).
3. **Two independent queries per answer.** Each intent's expected answer is computed by two separately
   written SQL queries. If they disagree, the intent is adjudicated or dropped. No expected value is typed by
   hand.
4. **Current definitions, written down.** Expected answers follow `app/semantic.yaml`: annulled deals excluded
   from rankings and money unless the question includes them, so'm by default, canonical products unless the
   question names a specific spelling or grade.
5. **Ambiguity is scored, not guessed.** An ambiguous intent lists every acceptable reading, each with its own
   expected values. Two scores are reported: *any reading right* and *all readings shown*.
6. **Coverage by design.** Intents are planned from a matrix of subject × question shape (below), so the
   score shows which kinds of questions fail, not just how many.
7. **Held-out by intent.** Intents are split 50/50 into `dev` (allowed for tuning) and `test` (scored only).
   All phrasings of one intent stay in the same split, so a paraphrase of a test question is never tuned on.

## Coverage matrix

Subjects: deals, contracts, companies (participants), brokers, USD rates.

Question shapes: lookup by ID or name · count with filters · total (money, tonnes) · average / median price ·
share of a subset · top-N · least / cheapest · distinct count · comparison of two periods · trend by month or
quarter · breakdown by category, region or product · date of an extreme.

Unanswerable intents (about 1 in 6): data outside 2022–2026, fields that do not exist (profit, directors,
emails), entities that do not exist (a non-existent broker or product), other currencies, forecasts.

## File format

`eval/real/intents.yaml` — one entry per intent:

```yaml
- id: r001
  split: dev            # dev | test
  subject: deals        # deals | contracts | companies | brokers | usd_rates | none
  shape: count          # see the matrix above
  answerable: true
  intent: "Number of deals concluded in October 2025"
  readings:             # one entry, or several for an ambiguous intent
    - name: all deals
      sql: "SELECT COUNT(*) AS deals FROM deals WHERE year = 2025 AND month = 10"
      sql_check: "…second, independently written query…"
      key: [deals]      # columns whose values must appear in the answer
    - name: without annulled
      sql: "…"
      sql_check: "…"
      key: [deals]
  questions:
    ru: "Сколько сделок было в октябре 2025?"
    uz_latn: "2025-yil oktabrda nechta bitim tuzilgan?"
    uz_cyrl: "2025 йил октябрда нечта битим тузилган?"
```

Expected values are not stored; they are recomputed from `sql` at run time and checked against `sql_check`.

## Scoring

For each phrasing: the answer is correct for a reading when every key value of that reading (first ≤ 5 rows)
appears in the answer. Reported per split, per language, per subject and per shape:

- **any**: at least one reading correct
- **all**: every reading correct (only for ambiguous intents)
- **refused correctly**: unanswerable intents answered with "no data"
- **wrong answers**: answered, but no reading correct
- **latency**: median and maximum

## Known limits

- The phrasings are written by an AI, not by real users. Native-speaker review of the Uzbek phrasings is
  needed, and real tester questions should be added to the set as they arrive.
- The expected answers encode this project's documented definitions. A grader with different definitions
  would score some answers differently; ambiguous intents list the alternatives explicitly.
