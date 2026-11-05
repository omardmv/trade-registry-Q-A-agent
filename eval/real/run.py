"""Score the agent on the realistic evaluation set (see docs/eval-design.md).

    uv run python -m eval.real.run --split test            # held-out half (score only)
    uv run python -m eval.real.run --split dev --langs ru  # tuning half, Russian only
    uv run python -m eval.real.run --only r001,r014

For every phrasing: a reading is correct when all of its key values (first ≤ 5 rows) appear in the answer.
Reported: any reading right, all readings shown (ambiguous intents), refused correctly, wrong answers, latency,
broken down by language, subject and shape. Writes eval/results/real-<split>-<time>.json.
"""
import argparse
import datetime as dt
import json
import re
import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import yaml

from app import config
from app.db import Database, to_jsonable
from app.pipeline import build_pipeline
from eval.run import value_in_text

INTENTS = config.ROOT / "eval" / "real" / "intents.yaml"
LANGS = ("ru", "uz_latn", "uz_cyrl")
SCHEMA_WORDS = re.compile(
    r"\b(?:product_\w+|amount_\w+|deal_id|contract_id|buyer_inn|seller_inn|\w+_broker|is_cancelled|volume_\w+|"
    r"group_by|status_scope|select|where|count\(|distinct)\b", re.I)


def schema_word_problems(intents: list[dict]) -> list[str]:
    out = []
    for i in intents:
        for lang, q in (i.get("questions") or {}).items():
            english_hint = lang != "uz_latn" and re.search(r"\([A-Za-z ]{3,}\)", q)  # Uzbek Latin is Latin
            if SCHEMA_WORDS.search(q) or english_hint:
                out.append(f"{i['id']}/{lang}: {q}")
    return out


def gold_values(db: Database, reading: dict) -> list:
    res = db.query(reading["sql"])
    idx = [res.columns.index(k) for k in reading["key"]]
    return [to_jsonable(row[j]) for row in res.rows[:5] for j in idx if row[j] is not None]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="test")
    ap.add_argument("--langs", default=",".join(LANGS))
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    intents = yaml.safe_load(open(INTENTS, encoding="utf-8"))
    problems = schema_word_problems(intents)
    if problems:
        print("Questions with schema words or English hints (fix before scoring):", *problems, sep="\n  ")
        return 2
    if args.split != "all":
        intents = [i for i in intents if i["split"] == args.split]
    if args.only:
        wanted = set(args.only.split(","))
        intents = [i for i in intents if i["id"] in wanted]
    langs = args.langs.split(",")

    db = Database(config.DB_PATH)
    golds = {i["id"]: [gold_values(db, r) for r in i.get("readings") or []] for i in intents}
    pipeline = build_pipeline()
    jobs = [(i, lang) for i in intents for lang in langs if (i.get("questions") or {}).get(lang)]

    def one(job):
        intent, lang = job
        question = intent["questions"][lang]
        t0 = time.monotonic()
        try:
            res = pipeline.ask(question)
            err = ""
        except Exception as e:  # recorded, not fatal
            res, err = None, f"{type(e).__name__}: {e}"
        latency = round(time.monotonic() - t0, 2)
        row = {"id": intent["id"], "lang": lang, "question": question, "subject": intent["subject"],
               "shape": intent["shape"], "answerable": intent["answerable"], "ambiguous": len(golds[intent["id"]]) > 1,
               "latency_s": latency, "error": err}
        if res is None:
            row.update(any_ok=False, all_ok=False, answered=False, answer="")
            return row
        row.update(answer=res.answer, source=res.source, sql=res.sql, answered=res.answered,
                   providers=getattr(res, "providers_used", None))
        if not intent["answerable"]:
            row.update(any_ok=not res.answered, all_ok=not res.answered)
            return row
        hits = [res.answered and all(value_in_text(v, res.answer) for v in g) for g in golds[intent["id"]]]
        row.update(reading_hits=hits, any_ok=any(hits), all_ok=all(hits))
        return row

    with ThreadPoolExecutor(args.workers) as ex:
        rows = list(ex.map(one, jobs))

    def rate(rs, key="any_ok"):
        return f"{sum(bool(r[key]) for r in rs)}/{len(rs)}" if rs else "-"

    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    amb = [r for r in ans if r["ambiguous"]]
    summary = {
        "phrasings": len(rows),
        "answerable_any": rate(ans),
        "ambiguous_all_readings": rate(amb, "all_ok"),
        "refused_correctly": rate(unans),
        "wrong_answers": sum(1 for r in ans if r["answered"] and not r["any_ok"]),
        "refused_answerable": sum(1 for r in ans if not r["answered"]),
        "errors": sum(1 for r in rows if r["error"]),
        "latency_median_s": statistics.median(r["latency_s"] for r in rows) if rows else None,
        "latency_max_s": max((r["latency_s"] for r in rows), default=None),
    }
    breakdown = {}
    for key in ("lang", "subject", "shape"):
        groups = defaultdict(list)
        for r in ans:
            groups[r[key]].append(r)
        breakdown[key] = {k: rate(v) for k, v in sorted(groups.items())}
    print(json.dumps({"summary": summary, "breakdown": breakdown}, ensure_ascii=False, indent=1))
    for r in rows:
        if not r["any_ok"]:
            kind = "ERROR" if r["error"] else ("REFUSED" if not r["answered"] else
                                               "ANSWERED (should refuse)" if not r["answerable"] else "WRONG")
            print(f"MISS {r['id']}/{r['lang']} {kind} [{r['subject']}/{r['shape']}] {r['question'][:80]} | "
                  f"got {r['answer'][:120]!r}")

    out = config.ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"real-{args.split}-{stamp}.json").write_text(json.dumps(
        {"summary": summary, "breakdown": breakdown, "rows": rows,
         "golds": {k: v for k, v in golds.items()}}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
