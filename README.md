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

`eval.run` reports SQL correctness, answer correctness, correct refusals, hallucinations (target 0) and
latency (limit 90 s), and writes `eval/results/run-*.json`.

## Settings

| Env | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | none | required |
| `GEMINI_MODEL` | `gemini-2.5-flash` | model for planning and answer writing |
| `UZEX_DEADLINE_S` | `75` | hard deadline per question |
| `UZEX_LLM_TIMEOUT_S` | `15` | per-call LLM timeout |
| `UZEX_DATA_DIR` | `data` | directory with the source CSV files |
| `UZEX_DB_PATH` | `build/uzex.duckdb` | path of the built DuckDB file |
