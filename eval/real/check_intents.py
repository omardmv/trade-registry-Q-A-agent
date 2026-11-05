"""Validate eval/real/intents.yaml and run every reference query.

Usage: uv run python -m eval.real.check_intents [--show] [--db build/uzex.duckdb] [--file eval/real/fresh_intents.yaml]

Checks field presence and values, runs `sql` (and `sql_check` when present) for each reading,
requires answerable readings to return 1..5 rows with non-NULL key values, compares `sql` with
`sql_check` key values, and prints counts per subject x shape and per split.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
INTENTS = Path(__file__).with_name("intents.yaml")
SUBJECTS = ["deals", "contracts", "companies", "brokers", "usd_rates", "none"]
SHAPES = [
    "lookup", "count", "total", "average", "share", "top_n", "least", "distinct_count",
    "compare_periods", "trend", "breakdown", "date_of_extreme",
]
SPLITS = {"dev", "test", "fresh", "fresh2"}
MAX_ROWS = 5


def _key_values(con, sql: str, key: list[str]) -> tuple[list[tuple], list[str]]:
    """Run sql; return key-column tuples of the first MAX_ROWS rows and the list of problems."""
    problems: list[str] = []
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - report any DB error
        return [], [f"error: {exc}"]
    missing = [k for k in key if k not in cols]
    if missing:
        return [], [f"key columns not in result: {missing} (have {cols})"]
    if not rows:
        problems.append("empty result")
    if len(rows) > MAX_ROWS:
        problems.append(f"{len(rows)} rows > {MAX_ROWS}")
    idx = [cols.index(k) for k in key]
    vals = [tuple(r[i] for i in idx) for r in rows[:MAX_ROWS]]
    for row in vals:
        for k, v in zip(key, row):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                problems.append(f"NULL key value in {k}")
    return vals, problems


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-6)
    return str(a) == str(b)


def validate(intents: list[dict], con, show: bool = False) -> int:
    errors = 0
    seen: set[str] = set()

    def err(iid: str, msg: str) -> None:
        nonlocal errors
        errors += 1
        print(f"[{iid}] {msg}")

    for it in intents:
        iid = it.get("id", "?")
        if iid in seen:
            err(iid, "duplicate id")
        seen.add(iid)
        for f in ("id", "split", "subject", "shape", "answerable", "intent", "readings"):
            if f not in it:
                err(iid, f"missing field {f}")
        if it.get("split") not in SPLITS:
            err(iid, f"bad split {it.get('split')}")
        if it.get("subject") not in SUBJECTS:
            err(iid, f"bad subject {it.get('subject')}")
        if it.get("shape") not in SHAPES:
            err(iid, f"bad shape {it.get('shape')}")
        readings = it.get("readings") or []
        if not it.get("answerable"):
            if readings:
                err(iid, "unanswerable intent must have readings: []")
            continue
        if not readings:
            err(iid, "answerable intent without readings")
        names = [r.get("name") for r in readings]
        if len(set(names)) != len(names):
            err(iid, "duplicate reading names")
        for r in readings:
            for f in ("name", "sql", "key"):
                if f not in r:
                    err(iid, f"reading {r.get('name')}: missing {f}")
            if "sql" not in r or "key" not in r:
                continue
            vals, probs = _key_values(con, r["sql"], r["key"])
            for p in probs:
                err(iid, f"reading '{r['name']}': {p}")
            if show:
                print(f"  {iid} [{r['name']}] {vals}")
            if r.get("sql_check"):
                cvals, cprobs = _key_values(con, r["sql_check"], r["key"])
                for p in cprobs:
                    err(iid, f"reading '{r['name']}' sql_check: {p}")
                if not cprobs and not probs:
                    ok = len(vals) == len(cvals) and all(
                        all(_same(a, b) for a, b in zip(ra, rb)) for ra, rb in zip(vals, cvals)
                    )
                    if not ok:
                        err(iid, f"reading '{r['name']}': sql {vals} != sql_check {cvals}")
    return errors


def report(intents: list[dict]) -> None:
    cells = Counter((i["subject"], i["shape"]) for i in intents)
    subjects = [s for s in SUBJECTS if any(i["subject"] == s for i in intents)]
    w = max(len(s) for s in SHAPES) + 1
    print("\nsubject x shape (all intents; unanswerable in parentheses)")
    print("".ljust(w) + "".join(s[:9].rjust(10) for s in subjects))
    unans = Counter((i["subject"], i["shape"]) for i in intents if not i["answerable"])
    for sh in SHAPES:
        line = sh.ljust(w)
        for s in subjects:
            n, u = cells[(s, sh)], unans[(s, sh)]
            line += (f"{n}({u})" if u else str(n) if n else ".").rjust(10)
        print(line)
    n_ans = sum(1 for i in intents if i["answerable"])
    n_amb = sum(1 for i in intents if len(i.get("readings") or []) > 1)
    print(f"\ntotal {len(intents)}: answerable {n_ans}, unanswerable {len(intents) - n_ans}, ambiguous {n_amb}")
    for sp in sorted(SPLITS):
        if not any(i["split"] == sp for i in intents):
            continue
        sub = [i for i in intents if i["split"] == sp]
        a = sum(1 for i in sub if i["answerable"])
        amb = sum(1 for i in sub if len(i.get("readings") or []) > 1)
        by_subj = Counter(i["subject"] for i in sub)
        print(f"{sp}: {len(sub)} (answerable {a}, ambiguous {amb}) " + ", ".join(f"{k}={v}" for k, v in sorted(by_subj.items())))
    empty = [f"{s}x{sh}" for s in SUBJECTS if s != "none" for sh in SHAPES if not cells[(s, sh)]]
    print("empty cells:", ", ".join(empty) or "none")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "build" / "uzex.duckdb"))
    ap.add_argument("--file", default=str(INTENTS))
    ap.add_argument("--show", action="store_true", help="print key values of every reading")
    args = ap.parse_args()
    intents = yaml.safe_load(Path(args.file).read_text(encoding="utf-8"))
    con = duckdb.connect(args.db, read_only=True)
    errors = validate(intents, con, show=args.show)
    report(intents)
    print(f"\n{errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
