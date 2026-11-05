"""Score the agent on the realistic evaluation set (see docs/eval-design.md).

    uv run python -m eval.real.run --split test            # held-out half (score only)
    uv run python -m eval.real.run --split dev --langs ru  # tuning half, Russian only
    uv run python -m eval.real.run --only r001,r014
    uv run python -m eval.real.run --split dev --providers qwen_local      # one provider (or a chain: a,b)

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
from app.agent.llm import usage_totals
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


def gold_values(db: Database, reading: dict) -> list[tuple[str, object]]:
    """(key column, value) pairs from the first ≤ 5 rows of the reference query."""
    res = db.query(reading["sql"])
    idx = [(k, res.columns.index(k)) for k in reading["key"]]
    return [(k, to_jsonable(row[j])) for row in res.rows[:5] for k, j in idx if row[j] is not None]


# Month stems in Russian, Uzbek Latin and Uzbek Cyrillic (any case/ending), index = month number.
_MONTHS = [None, ("январ", "yanvar", "январ"), ("феврал", "fevral", "феврал"), ("март", "mart", "март"),
           ("апрел", "aprel", "апрел"), ("ма[йяе]", "may", "май"), ("июн", "iyun", "июн"), ("июл", "iyul", "июл"),
           ("август", "avgust", "август"), ("сентябр", "sent[yя]?abr", "сентябр"), ("октябр", "okt[yя]?abr", "октябр"),
           ("ноябр", "noyabr", "ноябр"), ("декабр", "dekabr", "декабр")]
_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}
_QUARTER_WORD = r"(?:квартал\w*|chorak\w*|чорак\w*)"


def _month_named(n: int, text: str) -> bool:
    return any(re.search(rf"(?<!\w){stem}", text, re.I) for stem in _MONTHS[n])


def matches(col: str, value, text: str) -> bool:
    """Is this expected value stated in the answer? Same value in any common format counts."""
    if isinstance(value, bool):  # yes / no, as the answer templates write it
        word = r"да|ha|ҳа" if value else r"нет|yo.q|йўқ"
        return bool(re.search(rf"(?<!\w)(?:{word})(?!\w)", text, re.I))
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        y, m, d = value.split("-")
        if value in text or f"{d}.{m}.{y}" in text:
            return True
        day = str(int(d))
        return bool(re.search(rf"(?<!\d)0?{day}(?!\d)", text)) and _month_named(int(m), text) and y in text
    if col.lower() in ("month", "oy") and isinstance(value, int) and 1 <= value <= 12:
        return _month_named(value, text) or value_in_text(value, text)
    if col.lower() == "quarter" and isinstance(value, int) and 1 <= value <= 4:
        roman = _ROMAN[value]
        return bool(re.search(rf"(?<![\w]){roman}\s*-?\s*{_QUARTER_WORD}|Q{value}\b|(?<!\d){value}\s*-?\s*{_QUARTER_WORD}",
                              text, re.I))
    return value_in_text(value, text)


def score_reading(gold: list[tuple[str, object]], answer: str) -> bool:
    return all(matches(c, v, answer) for c, v in gold)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="test")
    ap.add_argument("--langs", default=",".join(LANGS))
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--providers", default="", help="LLM providers in order, e.g. qwen_local or gemini,openai "
                                                    "(default: LLM_PROVIDERS)")
    ap.add_argument("--pick", default="", help="file with id/lang lines: exactly these phrasings")
    ap.add_argument("--max-cost", type=float, default=0.0, help="stop starting questions once the estimated spend "
                                                                   "reaches this many USD (0 = no limit)")
    ap.add_argument("--usd-per-m-tokens", type=float, default=0.17,
                    help="estimated USD per 1M input tokens, output included (calibrated from the OpenAI usage page: "
                         "$2.06 for 12.23M input tokens with gpt-5.4-mini)")
    ap.add_argument("--rescore", default="", help="re-score a saved results file without calling the model")
    args = ap.parse_args()
    if args.rescore:
        return rescore(args.rescore)

    intents = yaml.safe_load(open(INTENTS, encoding="utf-8"))
    problems = schema_word_problems(intents)
    if problems:
        print("Questions with schema words or English hints (fix before scoring):", *problems, sep="\n  ")
        return 2
    if args.split != "all":
        intents = [i for i in intents if i["split"] == args.split]
    picked = None
    if args.pick:
        picked = {tuple(line.strip().split("/")) for line in open(args.pick, encoding="utf-8") if line.strip()}
        intents = [i for i in intents if i["id"] in {p[0] for p in picked}]
    if args.only:
        wanted = set(args.only.split(","))
        intents = [i for i in intents if i["id"] in wanted]
    langs = args.langs.split(",")

    if args.providers:
        config.LLM_PROVIDERS = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    db = Database(config.DB_PATH)
    golds = {i["id"]: [gold_values(db, r) for r in i.get("readings") or []] for i in intents}
    pipeline = build_pipeline()
    jobs = [(i, lang) for i in intents for lang in langs if (i.get("questions") or {}).get(lang)
            and (picked is None or (i["id"], lang) in picked)]

    def spent() -> float:
        return sum(u[1] for u in usage_totals().values()) / 1e6 * args.usd_per_m_tokens

    def one(job):
        intent, lang = job
        if args.max_cost and spent() >= args.max_cost:
            return None  # budget reached: not asked
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
        hits = [res.answered and score_reading(g, res.answer) for g in golds[intent["id"]]]
        row.update(reading_hits=hits, any_ok=any(hits), all_ok=all(hits))
        return row

    with ThreadPoolExecutor(args.workers) as ex:
        rows = [r for r in ex.map(one, jobs) if r is not None]
    skipped = len(jobs) - len(rows)

    def rate(rs, key="any_ok"):
        return f"{sum(bool(r[key]) for r in rs)}/{len(rs)}" if rs else "-"

    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    amb = [r for r in ans if r["ambiguous"]]
    summary = {
        "providers": ",".join(config.LLM_PROVIDERS),
        "not_asked_budget": skipped,
        "usage": {k: dict(zip(("requests", "input_tokens", "cached_input_tokens", "output_tokens"), v))
                  for k, v in usage_totals().items()},
        "estimated_usd": round(spent(), 3),
        "planner": config.PLANNER,
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


def rescore(path: str) -> int:
    saved = json.load(open(path, encoding="utf-8"))
    intents = {i["id"]: i for i in yaml.safe_load(open(INTENTS, encoding="utf-8"))}
    db = Database(config.DB_PATH)
    golds = {iid: [gold_values(db, r) for r in intents[iid].get("readings") or []] for iid in {r["id"] for r in saved["rows"]}}
    changed = 0
    for r in saved["rows"]:
        if not r["answerable"] or r.get("error"):
            continue
        hits = [r["answered"] and score_reading(g, r["answer"]) for g in golds[r["id"]]]
        changed += hits != r.get("reading_hits")
        r.update(reading_hits=hits, any_ok=any(hits), all_ok=all(hits))
    ans = [r for r in saved["rows"] if r["answerable"]]
    print(f"rescored {path}: {changed} rows changed; answerable {sum(r['any_ok'] for r in ans)}/{len(ans)}, "
          f"wrong {sum(1 for r in ans if r['answered'] and not r['any_ok'])}")
    json.dump(saved, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
