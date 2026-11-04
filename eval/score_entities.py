import json, re, time, duckdb
qs = {q["id"]: q for q in json.load(open("eval/questions.json"))}
gold = json.load(open("eval/entities_gold.json"))
gl = json.load(open("eval/gliner_out.json"))
c = duckdb.connect("data/uzex.duckdb", read_only=True)
# ---- closed vocabularies from the data itself
regions = [r[0] for r in c.sql("select distinct region from deals").fetchall()]
REGION_ALIASES = {  # stems: ru / uz / latin
 "Toshkent shahri":["ташкент","toshkent","tashkent"], "Namangan viloyati":["наманган","namangan"],
 "Buxoro viloyati":["бухар","buxoro","bukhara"], "Samarqand viloyati":["самарканд","samarqand"],
 "Sirdaryo viloyati":["сырдарь","sirdaryo"], "Toshkent viloyati":["ташкентск","toshkent viloyat"],
 "Qashqadaryo viloyati":["кашкадарь","qashqadaryo"], "Xorazm viloyati":["хорезм","xorazm"],
 "Andijon viloyati":["андижан","andijon"], "Farg‘ona viloyati":["ферган","farg"],
 "Surxondaryo viloyati":["сурхандарь","surxondaryo"], "Navoiy viloyati":["навои","navoiy"],
 "Qoraqalpog‘iston Respublikasi":["каракалпак","qoraqalpog"], "Jizzax viloyati":["джизак","jizzax"]}
prods = {p.split()[0].lower().rstrip("аыиеюя").replace("-сырец","") for (p,) in
         c.sql('select distinct "Наименование продукта" from contracts').fetchall()}
PROD_SYN = {"paxta":"хлопок","bug'doy":"пшениц","bugdoy":"пшениц","un":"мука","sement":"цемент","oltin":"золот",
            "xlopok":"хлопок","пшеница":"пшениц","пшеничн":"пшениц"}
def rules(text):
    t = text.lower(); out = {"product":set(),"region":set(),"inn":set(),"broker":set(),"year":set()}
    out["inn"] |= set(re.findall(r"\b\d{9}\b", t))
    out["broker"] |= {b.upper() for b in re.findall(r"\bbk\d{3}\b", t)}
    out["year"] |= set(re.findall(r"\b(20\d{2})\b", t))
    for reg, al in REGION_ALIASES.items():
        if any(a in t for a in al): out["region"].add(reg)
    if "toshkent viloyat" not in t and "ташкентск" not in t: out["region"].discard("Toshkent viloyati")
    for w in re.findall(r"[\w'’-]+", t):
        s = PROD_SYN.get(w) or next((p for p in prods if len(p)>=4 and w[:4]==p[:4]), None)
        if s: out["product"].add(s)
    if re.search(r"мук|\bun\b", t): out["product"].add("мука")
    out["product"] = {("хлопок" if p.startswith("хлопок") else p) for p in out["product"]}
    return out
def gl_ents(r):
    e = r["result"]["entities"] if "result" in r else r["entities"]
    o = {"product":set(),"region":set(),"inn":set(),"broker":set(),"year":set()}
    for k in ("product","region","inn","broker"):
        for x in e.get(k, []) or []: o[k].add(x["text"].lower())
    for x in e.get("time", []) or []: o["year"] |= set(re.findall(r"20\d{2}", x["text"]))
    return o
def match(kind, g, preds):
    if kind == "product": return any(any(w[:4]==g[:4] for w in re.findall(r"[\w'’-]+", p.lower())) for p in preds)
    if kind == "region": return any(g.lower()==p.lower() or p.lower() in REGION_ALIASES.get(g,[]) or any(a in p.lower() for a in REGION_ALIASES.get(g,[])) for p in preds)
    return g.lower() in {p.lower() for p in preds}
def score(name, fn, timer=None):
    tp=fp=fn_=0; perfect=0; bad=[]
    for qid, g in gold.items():
        pred = fn(qid); ok=True
        for kind in ("product","region","inn","broker","year"):
            gs = g.get(kind, []); ps = pred[kind]
            hit = [x for x in gs if match(kind, x, ps)]
            tp += len(hit); fn_ += len(gs)-len(hit)
            ok &= len(hit)==len(gs)
            matched_p = {p for p in ps if any(match(kind, x, {p}) for x in gs)}
            extra = [p for p in ps if p not in matched_p]
            fp += len(extra)
            if extra: ok=False
            if len(hit)!=len(gs) or extra: bad.append((qid,kind,gs,sorted(ps)))
        perfect += ok
    P=tp/(tp+fp) if tp+fp else 0; R=tp/(tp+fn_) if tp+fn_ else 0
    print(f"{name:28s} P={P:.2f} R={R:.2f} F1={2*P*R/(P+R):.2f}  questions fully right {perfect}/{len(gold)}")
    return bad
t0=time.perf_counter()
bad_r = score("rules + closed vocab", lambda q: rules(qs[q]["question"]))
rt=(time.perf_counter()-t0)/len(gold)*1000
bad_g = score("GLiNER2 (base-v1)", lambda q: gl_ents(gl[q]))
print(f"latency/question: rules {rt:.2f} ms | GLiNER2 {sum(v['ms'] for v in gl.values())/len(gl):.0f} ms")
def combo(q):
    r=rules(qs[q]["question"]); g=gl_ents(gl[q]); o={k:set(r[k]) for k in r}
    for k in ("year","inn","broker"): o[k]|=g[k]
    return o
score("rules + GLiNER2 (time/id)", combo)
print("\nrules misses/extras:"); [print(" ",b) for b in bad_r]
print("GLiNER2 misses/extras:"); [print(" ",b) for b in bad_g]
