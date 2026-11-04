"""Run every question twice; flag questions whose answer changes (the commission gets one try).

    .venv/bin/python eval/stability.py eval/bigset7.json
"""
import json, sys
sys.path.insert(0, ".")
import agent.core as C
C.MODE = "gemini"
def sig(a): return (a.route, a.no_data, json.dumps([list(r)[:6] for r in a.rows[:3]], default=str))
qs = json.load(open(sys.argv[1])); unstable = []
for q in qs:
    a1, a2 = C.answer(q["question"]), C.answer(q["question"])
    if sig(a1) != sig(a2): unstable.append((q["id"], q["question"], a1.route, a2.route, a1.rows[:2], a2.rows[:2]))
print(f"unstable: {len(unstable)}/{len(qs)}")
for u in unstable: print(f"  {u[0]} {u[1][:80]}\n     run1 {u[2]} {str(u[4])[:90]}\n     run2 {u[3]} {str(u[5])[:90]}")
