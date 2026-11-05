"""Stage 1 of max safety with the word classifier only (app/plan/wordclass.py): which phrasings it lets through.

    uv run python -m eval.real.wordclass_check --write-picks eval/real        # free: pick files, no LLM
    uv run python -m eval.real.wordclass_check --providers qwen_local --limit 10   # classifier only, local model
    uv run python -m eval.real.wordclass_check --providers openai --group fresh3:valid:blocked --max-cost 0.01

Groups: <set>:<valid|noanswer>:<unknown|blocked> - the phrasings with unknown words at stage 1 (unknown) or with a
word the noun gate blocks (blocked). Sets: intents (all splits), fresh, fresh2, fresh3, unanswerable_paraphrases.
fresh4 is never read. --write-picks writes pick-wordclass-<set>-<group>.txt (id/lang lines) for eval.real.run --pick.
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from app import config
from app.agent.llm import usage_totals
from app.catalog import load_catalog
from app.db import Database
from app.plan import wordclass
from app.plan.vocab import build_vocab
from app.resolve import Resolver

REAL = config.ROOT / "eval" / "real"
SETS = {"intents": "intents.yaml", "fresh": "fresh_intents.yaml", "fresh2": "fresh2_intents.yaml",
        "fresh3": "fresh3_intents.yaml", "unanswerable_paraphrases": "unanswerable_paraphrases.yaml"}


def phrasings():
    db = Database(config.DB_PATH)
    vocab = build_vocab(load_catalog(db), Resolver(db))
    for name, file in SETS.items():
        for item in yaml.safe_load((REAL / file).read_text(encoding="utf-8")):
            answerable = item.get("answerable", name != "unanswerable_paraphrases")
            for lang, q in (item.get("questions") or {}).items():
                g = vocab.gate(q)
                words = list(dict.fromkeys(u.word for u in g.blocking + g.wording))
                if not words:
                    continue
                yield dict(set=name, id=item["id"], lang=lang, question=q, words=words,
                           group="valid" if answerable else "noanswer", blocked=bool(g.blocking))


def in_group(p, groups):
    for g in groups:
        s, kind, which = (g.split(":") + ["", "", ""])[:3]
        if (not s or p["set"] == s) and (not kind or p["group"] == kind) and (which != "blocked" or p["blocked"]):
            return True
    return not groups


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", action="append", default=[], help="set:valid|noanswer[:blocked] (repeatable)")
    ap.add_argument("--providers", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-cost", type=float, default=0.0)
    ap.add_argument("--usd-per-m-tokens", type=float, default=0.17)
    ap.add_argument("--write-picks", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    items = [p for p in phrasings() if in_group(p, args.group)]
    if args.write_picks:
        by = {}
        for p in items:
            by.setdefault((p["set"], p["group"]), []).append(f"{p['id']}/{p['lang']}")
        for (s, g), lines in by.items():
            path = Path(args.write_picks) / f"pick-wordclass-{s}-{g}.txt"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"{path}: {len(lines)}")
        return 0
    if args.limit:
        items = items[:args.limit]
    if args.providers:
        config.LLM_PROVIDERS = [x.strip() for x in args.providers.split(",") if x.strip()]
    from app.agent.llm_fallback import build_llm
    llm = build_llm(config.LLM_PROVIDERS, config.LLM_TIMEOUT_S)

    def spent():
        return sum(u[1] for u in usage_totals().values()) / 1e6 * args.usd_per_m_tokens

    def one(p):
        if args.max_cost and spent() >= args.max_cost:
            return None
        out = wordclass.classify(llm, p["question"], p["words"], time.monotonic() + 60, config.LLM_TIMEOUT_S)
        return {**p, "labels": out.labels, "error": out.error,
                "passes": out.ok and not out.other()}

    with ThreadPoolExecutor(args.workers) as ex:
        rows = [r for r in ex.map(one, items) if r is not None]
    for r in rows:
        print(f"{'PASS' if r['passes'] else 'stop'} {r['set']}|{r['id']}/{r['lang']} {r['labels'] or r['error']} "
              f"| {r['question'][:90]}")
    summary = {"asked": len(rows), "not_asked_budget": len(items) - len(rows),
               "passes": sum(r["passes"] for r in rows), "errors": sum(bool(r["error"]) for r in rows),
               "estimated_usd": round(spent(), 4)}
    print(json.dumps(summary))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
