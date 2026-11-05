"""Offline snapshot of max-safety stage 1 (Vocab.gate) over every phrasing of the real sets; no LLM calls.

    uv run python -m eval.real.stage1_snapshot --save eval/results/stage1-before.json     # on the old commit
    uv run python -m eval.real.stage1_snapshot --compare eval/results/stage1-before.json  # on the new commit
    uv run python -m eval.real.stage1_snapshot --compare BEFORE --write-picks DIR         # pick files of openings

--compare prints, per set, the phrasings stage 1 refuses before -> now (valid and no-answer separately), every
phrasing whose blocking words changed, and the no-answer phrasings stage 1 refused before and lets through now (those
must go through the full pipeline: eval.real.run --pick). Sets: intents.yaml (all splits), fresh_intents.yaml,
fresh2_intents.yaml, fresh3_intents.yaml, answerable_paraphrases.yaml, unanswerable_paraphrases.yaml.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from app import config
from app.catalog import load_catalog
from app.db import Database
from app.plan.vocab import build_vocab
from app.resolve import Resolver

REAL = config.ROOT / "eval" / "real"
SETS = {"intents": "intents.yaml", "fresh": "fresh_intents.yaml", "fresh2": "fresh2_intents.yaml",
        "fresh3": "fresh3_intents.yaml", "answerable_paraphrases": "answerable_paraphrases.yaml",
        "unanswerable_paraphrases": "unanswerable_paraphrases.yaml"}


def snapshot() -> dict:
    db = Database(config.DB_PATH)
    vocab = build_vocab(load_catalog(db), Resolver(db))
    out = {}
    for name, file in SETS.items():
        for item in yaml.safe_load((REAL / file).read_text(encoding="utf-8")):
            answerable = item.get("answerable", name == "answerable_paraphrases")
            for lang, q in (item.get("questions") or {}).items():
                g = vocab.gate(q)
                out[f"{name}|{item['id']}|{lang}"] = dict(answerable=answerable, question=q,
                                                          blocking=[u.word for u in g.blocking],
                                                          wording=[u.word for u in g.wording])
    return out


def compare(before: dict, after: dict, picks: str = ""):
    counts = Counter()
    for k, b in before.items():
        a = after.get(k)
        if a is None:
            continue
        key = (k.split("|")[0], "valid" if b["answerable"] else "no-answer")
        counts[key + ("before",)] += bool(b["blocking"])
        counts[key + ("after",)] += bool(a["blocking"])
        counts[key + ("total",)] += 1
    print("set / group: refused at stage 1 before -> after (of total)")
    for name in SETS:
        for group in ("valid", "no-answer"):
            n = counts[(name, group, "total")]
            if n:
                print(f"  {name:26} {group:9} {counts[(name, group, 'before')]:3} -> "
                      f"{counts[(name, group, 'after')]:3}  (of {n})")
    print("\nblocking words changed:")
    opened = []
    for k, b in before.items():
        a = after.get(k)
        if a is None or a["blocking"] == b["blocking"]:
            continue
        print(f"  {k} ({'valid' if b['answerable'] else 'no-answer'}): {b['blocking']} -> {a['blocking']} "
              f"wording {a['wording']} | {b['question']}")
        if not b["answerable"] and b["blocking"] and not a["blocking"]:
            opened.append(k)
    print(f"\nno-answer phrasings stage 1 refused before and lets through now: {len(opened)}")
    for k in opened:
        print(f"  {k}")
    if picks:
        by_set = defaultdict(list)
        for k in opened:
            name, ident, lang = k.split("|")
            by_set[name].append(f"{ident}/{lang}")
        for name, lines in by_set.items():
            path = Path(picks) / f"pick-stage1-{name}.txt"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"wrote {path} ({len(lines)})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", default="")
    ap.add_argument("--compare", default="")
    ap.add_argument("--write-picks", default="")
    args = ap.parse_args()
    snap = snapshot()
    if args.save:
        Path(args.save).write_text(json.dumps(snap, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"wrote {args.save} ({len(snap)} phrasings)")
    if args.compare:
        compare(json.loads(Path(args.compare).read_text(encoding="utf-8")), snap, args.write_picks)


if __name__ == "__main__":
    main()
