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
    row = {"id": q["id"], "lang": q["lang"], "question": q["question"], "answerable": q["answerable"]}
    try:
        res = pipeline.ask(q["question"])
    except Exception as e:  # a crashing call is a failed answer, not a crashed run
        row.update(latency_s=round(time.monotonic() - t0, 2), result={"source": "error"}, error=f"{type(e).__name__}: {e}",
                   sql_ok=False if q["answerable"] else None, answer_ok=False, hallucination=False)
        return row
    latency = time.monotonic() - t0
    row.update(latency_s=round(latency, 2), result=asdict(res))
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
              f"[{r['result']['source']}] {r['question'][:60]}" + (f" ERROR {r['error']}" if r.get("error") else ""))
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    latencies = sorted(r["latency_s"] for r in rows)
    summary = {
        "questions": len(rows),
        "sql_correct": f"{sum(bool(r['sql_ok']) for r in answerable)}/{len(answerable)}",
        "answer_correct": f"{sum(r['answer_ok'] for r in answerable)}/{len(answerable)}",
        "refused_correctly": f"{sum(r['answer_ok'] for r in unanswerable)}/{len(unanswerable)}",
        "hallucinations": sum(r["hallucination"] for r in rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "latency_p50_s": latencies[len(latencies) // 2] if latencies else None,
        "latency_p95_s": latencies[max(int(len(latencies) * 0.95) - 1, 0)] if latencies else None,
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
