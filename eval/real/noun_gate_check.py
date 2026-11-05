"""Offline check of max-safety stage 1 (app/pipeline.py, Vocab.gate): no LLM calls.

    uv run python -m eval.real.noun_gate_check                    # counts + the no-answer phrasings that now pass
    uv run python -m eval.real.noun_gate_check --show-valid       # also the valid phrasings still refused
    uv run python -m eval.real.noun_gate_check --write-picks DIR  # pick files of the no-answer phrasings that pass

Before: stage 1 refused any word outside the lexicon and every DB name (Vocab.unknown). After: only an unknown word
that can be a noun refuses (Vocab.gate, app/plan/wordkind.py); the other unknown words go to the planner and the
judge. Sets: intents.yaml (all splits), fresh_intents.yaml, fresh2_intents.yaml, answerable_paraphrases.yaml,
unanswerable_paraphrases.yaml. fresh3_intents.yaml is not read.
"""
import argparse
from collections import defaultdict
from pathlib import Path

import yaml

from app import config
from app.catalog import load_catalog
from app.db import Database
from app.plan.vocab import build_vocab
from app.resolve import Resolver

REAL = config.ROOT / "eval" / "real"
SETS = {"intents": "intents.yaml", "fresh": "fresh_intents.yaml", "fresh2": "fresh2_intents.yaml",
        "answerable_paraphrases": "answerable_paraphrases.yaml",
        "unanswerable_paraphrases": "unanswerable_paraphrases.yaml"}


def run():
    db = Database(config.DB_PATH)
    vocab = build_vocab(load_catalog(db), Resolver(db))
    stats = defaultdict(lambda: [0, 0, 0])      # (set, group) -> [refused before, refused after, total]
    passing, still = [], []
    for name, file in SETS.items():
        for item in yaml.safe_load((REAL / file).read_text(encoding="utf-8")):
            answerable = item.get("answerable", name == "answerable_paraphrases")
            group = "valid" if answerable else "no-answer"
            for lang, q in (item.get("questions") or {}).items():
                before = vocab.unknown(q)
                g = vocab.gate(q)
                st = stats[(name, group)]
                st[0] += bool(before)
                st[1] += bool(g.blocking)
                st[2] += 1
                row = dict(set=name, id=item["id"], lang=lang, split=item.get("split", ""), question=q,
                           blocking=[(u.word, g.kinds[u.word]) for u in g.blocking],
                           wording=[(u.word, g.kinds[u.word]) for u in g.wording])
                if not answerable and before and not g.blocking:
                    passing.append(row)
                if answerable and g.blocking:
                    still.append(row)
    return stats, passing, still


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show-valid", action="store_true")
    ap.add_argument("--write-picks", default="")
    args = ap.parse_args()
    stats, passing, still = run()
    print("set / group: refused at stage 1 before -> after (of total)")
    for (name, group), (b, a, n) in sorted(stats.items()):
        print(f"  {name:26} {group:9} {b:3} -> {a:3}  (of {n})")
    print(f"\nno-answer phrasings stage 1 refused before and lets through now: {len(passing)}")
    for r in passing:
        print(f"  {r['set']} {r['id']}/{r['lang']}: wording {r['wording']} | {r['question']}")
    if args.show_valid:
        print(f"\nvalid phrasings still refused at stage 1: {len(still)}")
        for r in still:
            print(f"  {r['set']} {r['id']}/{r['lang']}: nouns {r['blocking']} wording {r['wording']} | {r['question']}")
    if args.write_picks:
        out = Path(args.write_picks)
        by_set = defaultdict(list)
        for r in passing:
            by_set[r["set"]].append(f"{r['id']}/{r['lang']}")
        for name, lines in by_set.items():
            (out / f"pick-noun-gate-{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"wrote {out / f'pick-noun-gate-{name}.txt'} ({len(lines)})")


if __name__ == "__main__":
    main()
