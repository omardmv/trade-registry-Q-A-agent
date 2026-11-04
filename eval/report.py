"""Failure report from a traced eval run: which stage broke, with plan, filters and SQL next to the gold.

    .venv/bin/python eval/report.py eval/traces/<run>.jsonl        -> eval/reports/<run>.md + summary
"""
import json, os, re, sys, collections

def steps(r, name): return [s for s in r["trace"].get("steps", []) if s.get("stage") == name]

def classify(r):
    if r["ok"]: return "ok"
    gs = r["gold_sql"].lower()
    plan = (steps(r, "llm_plan") or [{}])[-1].get("plan", {})
    filt = (steps(r, "filters") or [{}])[-1]
    where = " ".join(filt.get("where", []) + filt.get("cond", [])).lower()
    shape = (steps(r, "compiled") or [{}])[-1].get("shape")
    refuse = steps(r, "refuse")
    if r["kind"] == "no_data": return "FABRICATION (answered a no-data question)"
    if r["route"] == "gemini:not-answerable": return "plan: wrongly judged not answerable"
    if refuse: return f"verify: refused ({refuse[-1].get('reason')})"
    if not plan: return "fallback path (no Gemini plan)"
    if "group by" in gs and shape in ("scalar", "scalar_units"): return "plan: missing grouping/ranking"
    if re.search(r"deal_date|year\(|month\(|between", gs) and "deal_date" not in where and "year(" not in (r.get("sql") or "").lower(): return "plan: missing period"
    if "product_base" in gs and "product_base" not in where: return "plan: missing product"
    if re.search(r"\bfrom contracts\b|join contracts", gs) and not r["route"].startswith("gemini:contracts"): return "plan/feature: contract-card question"
    if re.search(r"order by .*limit 1", gs) and "deal" not in r["route"] and shape in ("scalar", "scalar_units"): return "plan: wanted a specific deal/date"
    return "value differs (definition, filter detail or compiler) — inspect"

def main(path):
    rs = [json.loads(l) for l in open(path)]
    labs = [(r, classify(r)) for r in rs]
    cnt = collections.Counter(l for _, l in labs)
    out = [f"# Failure report — {os.path.basename(path)}", "", f"Correct: {cnt['ok']}/{len(rs)}", "", "| Stage | Count |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in cnt.most_common() if k != "ok"]
    lat = sorted(r["ms"] for r in rs)
    tok = [s.get("usage", {}).get("totalTokenCount", 0) for r in rs for s in steps(r, "llm_plan")]
    out += ["", f"Latency: median {lat[len(lat)//2]} ms, max {lat[-1]} ms. Gemini tokens per question: median {sorted(tok)[len(tok)//2] if tok else 0}.", ""]
    for r, l in labs:
        if l == "ok": continue
        plan = (steps(r, "llm_plan") or [{}])[-1].get("plan", {})
        filt = (steps(r, "filters") or [{}])[-1]
        out += [f"## {r['id']} [{r['kind']}, {r['lang']}] — {l}", "", f"**Q:** {r['question']}", "",
                f"**Gold:** `{str(r['gold'])[:300]}`  ", f"**Gold SQL:** `{r['gold_sql'][:400]}`  ",
                f"**Note:** {r.get('note','')[:300]}", "", f"**Route:** `{r['route']}`  ", f"**Got rows:** `{str(r['rows'][:4])[:300]}`", "",
                "**Plan:** " + ("`" + json.dumps({k: v for k, v in plan.items() if k not in ('interpretation',)}, ensure_ascii=False)[:900] + "`" if plan else "_none_"), "",
                f"**Filters:** `{filt.get('where', [])} cond={filt.get('cond', [])}`  ", f"**SQL:** `{(r.get('sql') or '')[:500]}`", "",
                "**Answer:**", "```", r["text"][:700], "```", ""]
    os.makedirs("eval/reports", exist_ok=True)
    rp = "eval/reports/" + os.path.basename(path).replace(".jsonl", ".md")
    open(rp, "w").write("\n".join(out))
    print("\n".join(out[:8 + len(cnt)])); print("report:", rp)

if __name__ == "__main__": main(sys.argv[1])
