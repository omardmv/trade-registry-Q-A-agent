"""Benchmark on version 1's question sets (expected values stored with each question).

    uv run python -m eval.v1_bench --split dev     # bigset … bigset6 (used for tuning)
    uv run python -m eval.v1_bench --split test    # bigset7, bigset8, holdout (held out)
    uv run python -m eval.v1_bench --sets bigset8  # any explicit sets

A question counts as correct when every expected value (first ≤5 rows) appears in the answer text.
"no_data" questions count as correct when the agent refuses or every number in its answer is 0.
"""
import argparse
import datetime as dt
import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from app import config
from app.answer.numbers import extract_numbers
from app.pipeline import build_pipeline
from eval.run import value_in_text

SETS = {"dev": ["bigset", "bigset2", "bigset3", "bigset4", "bigset5", "bigset6"],
        "test": ["bigset7", "bigset8", "holdout"]}
DIR = config.ROOT / "eval" / "v1"


_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def gold_in_answer(gold, answer: str) -> bool:
    """value_in_text, plus: an ISO date (YYYY-MM-DD) in gold also matches the same date written dd.mm.yyyy."""
    if isinstance(gold, str) and (m := _ISO_DATE.fullmatch(gold.strip())):
        y, mo, d = m.groups()
        return gold.strip() in answer or f"{d}.{mo}.{y}" in answer
    return value_in_text(gold, answer)


def score(q: dict, res) -> bool:
    if q["kind"] == "no_data":
        main = res.answer.split("\n\n")[0]
        return (not res.answered) or all(abs(t.value) < 1e-9 for t in extract_numbers(main))
    gold = [v for row in (q.get("gold") or [])[:5] for v in row if v is not None]
    return res.answered and all(gold_in_answer(v, res.answer) for v in gold)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=sorted(SETS))
    ap.add_argument("--sets", default="")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    names = args.sets.split(",") if args.sets else SETS[args.split or "test"]
    questions = [dict(q, set=n) for n in names for q in json.load(open(DIR / f"{n}.json", encoding="utf-8"))]
    pipeline = build_pipeline()

    def one(q):
        t0 = time.monotonic()
        try:
            res = pipeline.ask(q["question"])
        except Exception as e:  # recorded, not fatal
            return {**q, "ok": False, "error": str(e), "latency_s": 0.0}
        return {**q, "ok": score(q, res), "latency_s": round(time.monotonic() - t0, 2), "source": res.source,
                "answer": res.answer, "sql": res.sql, "trace": getattr(res, "trace", None)}

    with ThreadPoolExecutor(args.workers) as ex:
        rows = list(ex.map(one, questions))
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        for key in (r["set"], r["kind"], "TOTAL"):
            by[key][0] += r["ok"]
            by[key][1] += 1
    summary = {k: f"{a}/{b}" for k, (a, b) in by.items()}
    summary["latency_max_s"] = max(r["latency_s"] for r in rows)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for r in rows:
        if not r["ok"]:
            print(f"MISS {r['set']}:{r['id']} {r['kind']} {r['question'][:90]} | gold {r.get('gold')} | "
                  f"got {(r.get('answer') or r.get('error', ''))[:140]!r}")
    out = config.ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"v1bench-{'-'.join(names)}-{stamp}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
