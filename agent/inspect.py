"""See what happens inside one request.

    .venv/bin/python -m agent.inspect "сколько сделок по сахару в 2024"
    .venv/bin/python -m agent.inspect "..." --mode hybrid      # rules path
    .venv/bin/python -m agent.inspect "..." --json             # raw trace
"""
import json, sys
from . import core as C


def show(trace):
    print(f"\nQUESTION: {trace['question']}")
    print(f"mode={trace.get('mode')}  route={trace.get('route')}  no_data={trace.get('no_data')}  total={trace.get('total_ms')} ms\n")
    for st in trace["steps"]:
        stage, t = st.pop("stage"), st.pop("t_ms")
        if stage == "sql":
            print(f"  [{t:7.1f} ms] sql ({st['rows']} rows, {st['db_ms']} ms)\n      {st['sql'][:600]}")
            if st.get("sample"): print(f"      sample: {st['sample']}")
        elif stage == "llm_plan":
            print(f"  [{t:7.1f} ms] llm_plan  model={st['model']} {st['llm_ms']} ms  tokens={st.get('usage', {}).get('totalTokenCount')}")
            for k, v in st["plan"].items(): print(f"      {k}: {v}")
        elif stage == "answer":
            print(f"  [{t:7.1f} ms] answer  ({st['route']}, confidence={st['confidence']})\n" + "\n".join("      " + l for l in st["text"].splitlines()))
        else:
            print(f"  [{t:7.1f} ms] {stage}: " + json.dumps(st, ensure_ascii=False, default=str)[:700])


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--mode" in sys.argv: C.MODE = sys.argv[sys.argv.index("--mode") + 1]; args = [a for a in args if a != C.MODE]
    else: C.MODE = "gemini"
    a = C.answer(" ".join(args))
    print(json.dumps(a.trace, ensure_ascii=False, indent=1, default=str)) if "--json" in sys.argv else show(a.trace)
