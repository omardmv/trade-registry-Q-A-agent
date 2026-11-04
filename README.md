# Exchange Q&A agent

Answers Russian and Uzbek questions about the exchange deal registry in `data/`. Every fact in an answer is
checked against the database result; if the data has no answer, the service says so. The data is fictional:
companies, tax IDs, brokers, contracts, deals and prices are invented (see `data/README.md`).

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

`eval.run` reports SQL correctness, answer correctness, correct refusals, hallucinations (target 0), `hallucinations_vs_gold` (answer numbers/IDs found neither in the gold rows nor in the
returned rows; this catches verifier bypasses) and `answered_wrong` (answers that do not match the gold values) and
latency (limit 90 s), and writes `eval/results/run-*.json`.

## First live run

The tests run offline. Before relying on the service, check it against the real model:

1. `GEMINI_API_KEY=... uv run pytest -m live` (the live round-trip tests).
2. Start the server and send one question to `/ask`, for example
   `curl -s localhost:8000/ask -H 'content-type: application/json' -d '{"question":"Сколько сделок было в 2025 году?"}'`.
3. Confirm that the model named in `GEMINI_MODEL` is available for your key (a 404 / "model not found" error
   means it is not); change `GEMINI_MODEL` if needed. `GEMINI_THINKING_BUDGET` must be supported by that model;
   set it to `0` to disable thinking.
4. Then run the full evaluation (`eval.run`); it exits non-zero on any hallucination.

## Settings

| Env | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | none | required |
| `GEMINI_MODEL` | `gemini-2.5-flash` | model for planning and answer writing |
| `GEMINI_THINKING_BUDGET` | `1024` | thinking tokens per Gemini call |
| `UZEX_DEADLINE_S` | `75` | hard deadline per question |
| `UZEX_LLM_TIMEOUT_S` | `15` | per-call LLM timeout |
| `UZEX_DATA_DIR` | `data` | directory with the source CSV files |
| `UZEX_DB_PATH` | `build/uzex.duckdb` | path of the built DuckDB file |

## Known limitation

The verifier checks numbers, IDs, dates, quoted names and names that exist in the registry. An invented company
or product name that is neither in the registry nor in quotes cannot be detected.
