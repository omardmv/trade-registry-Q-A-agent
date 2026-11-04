import json, sys
sys.path.insert(0, ".")
from agent.core import answer
files = [a for a in sys.argv[1:] if not a.startswith("--")]; use_llm = "--no-llm" not in sys.argv
LENIENT = "--lenient" in sys.argv
def flat(rows): return [x for r in rows for x in r]
def same(gold, got):
    gv, ov = flat(gold), flat(got)
    if not gv: return False
    def eq(g, o):
        num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
        close = lambda a, b: abs(a - b) <= max(1e-6, abs(a) * 0.005)
        if num(g) and num(o):
            return close(g, o) or (LENIENT and (close(g, o * 1000) or close(g * 1000, o)))   # kg vs tonnes
        if LENIENT and (num(g) or num(o)) and str(g).strip() == str(o).strip(): return True  # 2023 vs '2023'
        if LENIENT:
            import re as _re
            def as_num(x):
                m = _re.fullmatch(r"\s*(-?\d+(?:[.,]\d+)?)\s*%?\s*", str(x)); return float(m.group(1).replace(",", ".")) if m else None
            def as_date(x):
                m = _re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", str(x).strip()); return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else str(x).strip()[:10]
            if as_num(g) is not None and as_num(o) is not None and close(as_num(g), as_num(o)): return True    # '10 %' vs 10.0
            if as_date(g) == as_date(o) and _re.match(r"\d{4}-\d{2}-\d{2}", as_date(g)): return True            # 03.01.2022 vs 2022-01-03
        return isinstance(g, str) and str(o).strip() == g.strip()
    return all(any(eq(g, o) for o in ov) for g in gv)
import os, time as _time
from agent import core as _C
os.makedirs("eval/traces", exist_ok=True)
for f in files:
    qs = json.load(open(f)); tot = {}; fab = 0
    print(f"\n== {f}")
    tpath = f"eval/traces/{os.path.basename(f).replace('.json','')}__{_C.MODE}{'__lenient' if LENIENT else ''}__{_time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    tout = open(tpath, "w")
    for q in qs:
        a = answer(q["question"], use_llm=use_llm)
        gold = [tuple(r) for r in q["gold"]]
        gz = (not flat(gold)) or all(v in (0, None) for v in flat(gold))
        if gz and q["kind"] != "no_data":
            ok = a.no_data or flat(a.rows)[:1] == [0]
        elif q["kind"] == "no_data":
            ok = a.no_data or (all(v in (0, None) for v in flat(gold)) and flat(a.rows)[:1] == [0])
            if not ok: fab += 1
        else:
            ok = (not a.no_data) and same(gold, a.rows)
        k = q["kind"]; tot.setdefault(k, [0, 0]); tot[k][0] += ok; tot[k][1] += 1
        tout.write(json.dumps({"id": q["id"], "kind": q["kind"], "lang": q["lang"], "question": q["question"], "gold": q["gold"],
                               "gold_sql": q["gold_sql"], "note": q.get("note", ""), "ok": bool(ok), "route": a.route, "no_data": a.no_data,
                               "rows": [list(r) for r in a.rows[:8]], "text": a.text, "sql": a.sql, "ms": round(a.ms), "trace": a.trace},
                              ensure_ascii=False, default=str) + "\n")
        print(f"{q['id']} {'OK ' if ok else 'BAD'} {a.route:22s} {a.ms:7.0f}ms | {a.text.splitlines()[0][:90]}")
        if not ok: print("     gold:", q["gold"][:2], "| got:", a.rows[:2], "\n     sql:", a.sql[:160])
    s = sum(v[0] for v in tot.values()); n = sum(v[1] for v in tot.values())
    print(" | ".join(f"{k} {v[0]}/{v[1]}" for k, v in tot.items()), f"| TOTAL {s}/{n} | fabricated on no-data: {fab}")
    tout.close(); print("traces:", tpath)
