"""Offline check of the closed vocabulary (app/plan/vocab.py); no LLM calls.

    uv run python -m eval.real.vocab_check                 # eval set (360 phrasings) + paraphrase set
    uv run python -m eval.real.vocab_check --show-known    # also list the refused words of every phrasing

Stage 1 (before the planner): unknown words that are not a word of any entity name in the database.
Stage 2 proxy (after the plan): lexicon misses that are not words of the entities the gold SQL uses (INNs and
broker codes in the reference readings, quoted product / region values) - what stage 2 refuses when the planner
uses the same entities as the gold query. For no-answer intents the proxy takes the entities their question
names by INN / code only.
"""
import argparse
import re
from collections import defaultdict

import yaml

from app import config
from app.catalog import load_catalog
from app.db import Database
from app.plan.vocab import build_vocab
from app.resolve import Resolver

INTENTS = config.ROOT / "eval" / "real" / "intents.yaml"
PARAPHRASES = config.ROOT / "eval" / "real" / "unanswerable_paraphrases.yaml"
ANSWERABLE = config.ROOT / "eval" / "real" / "answerable_paraphrases.yaml"
LANGS = ("ru", "uz_latn", "uz_cyrl")
_INN = re.compile(r"(?<!\d)\d{9}(?!\d)")
_BK = re.compile(r"\bBK\d{3}\b", re.I)
_QUOTED = re.compile(r"'([^']+)'")


def gold_names(resolver: Resolver, intent: dict, question: str) -> list[str]:
    sql = " ".join(r.get("sql", "") for r in intent.get("readings") or ())
    out = []
    for inn in set(_INN.findall(sql + " " + question)):
        out += resolver.entity_names("company", inn)
    for code in set(_BK.findall(sql + " " + question)):
        out += resolver.entity_names("broker", code)
    for value in set(_QUOTED.findall(sql)):
        for kind in ("product", "product_family", "region"):
            out += [n for n in resolver.entity_names(kind, value) if n != value] + [value]
    return out


def run(db_path=config.DB_PATH, verbose=False) -> dict:
    db = Database(db_path)
    resolver = Resolver(db)
    vocab = build_vocab(load_catalog(db), resolver)
    intents = yaml.safe_load(INTENTS.read_text(encoding="utf-8"))
    paraphrases = yaml.safe_load(PARAPHRASES.read_text(encoding="utf-8"))
    stats = defaultdict(lambda: [0, 0])          # key -> [refused, total]
    lines = []
    for i in intents:
        group = "answerable" if i["answerable"] else "no-answer"
        for lang in LANGS:
            q = i["questions"][lang]
            s1 = vocab.unknown(q)
            s2 = vocab.unknown_in_plan(q, gold_names(resolver, i, q))
            for stage, unk in (("stage1", s1), ("stage2", s2), ("any", s1 or s2)):
                st = stats[(stage, group, i["split"])]
                st[0] += bool(unk)
                st[1] += 1
            if (group == "answerable" and (s1 or s2)) or (group == "no-answer") or verbose:
                tag = ("FALSE REFUSAL" if group == "answerable" and (s1 or s2) else
                       "refused" if s1 or s2 else "passed" if group == "no-answer" else "ok")
                lines.append(f"{i['id']} {i['split']:4} {lang:7} {group:10} {tag:13} "
                             f"s1={[u.word for u in s1]} s2={[u.word for u in s2]} | {q}")
    for p in paraphrases:
        for lang in LANGS:
            q = p["questions"][lang]
            s1 = vocab.unknown(q)
            s2 = vocab.unknown_in_plan(q, gold_names(resolver, {}, q))
            for stage, unk in (("stage1", s1), ("stage2", s2), ("any", s1 or s2)):
                st = stats[(stage, "paraphrase", p["kind"])]
                st[0] += bool(unk)
                st[1] += 1
            tag = "refused" if s1 else "s2 only" if s2 else "PASSED"
            lines.append(f"{p['id']} {p['kind']:6} {lang:7} paraphrase {tag:8} s1={[u.word for u in s1]} "
                         f"s2={[u.word for u in s2]} | {q}")
    for a in yaml.safe_load(ANSWERABLE.read_text(encoding="utf-8")):
        for lang in LANGS:
            q = a["questions"][lang]
            s1 = vocab.unknown(q)
            s2 = vocab.unknown_in_plan(q, gold_names(resolver, {}, q) + _named(resolver, q))
            for stage, unk in (("stage1", s1), ("stage2", s2), ("any", s1 or s2)):
                st = stats[(stage, "answerable-paraphrase", "new")]
                st[0] += bool(unk)
                st[1] += 1
            if s1 or s2 or verbose:
                lines.append(f"{a['id']} new    {lang:7} answerable-paraphrase {'FALSE REFUSAL' if s1 or s2 else 'ok'} "
                             f"s1={[u.word for u in s1]} s2={[u.word for u in s2]} | {q}")
    return {"stats": dict(stats), "lines": lines}


def _named(resolver: Resolver, question: str) -> list[str]:
    """Names of the companies a question names in words (no gold SQL): what resolve(company) would return."""
    out = []
    for m in re.finditer(r"[A-Z][a-z]+ [A-Z][a-z]+", question):
        out += [c.name for c in resolver.resolve("company", m.group(0), limit=1)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show-known", action="store_true")
    args = ap.parse_args()
    out = run(verbose=args.show_known)
    print("\n".join(out["lines"]))
    print()
    for key in sorted(out["stats"]):
        refused, total = out["stats"][key]
        print(f"{' / '.join(key):40} refused {refused:3}/{total}")


if __name__ == "__main__":
    main()
