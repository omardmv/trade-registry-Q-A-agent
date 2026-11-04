def patch(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert s.count(a) >= 1, f"{p}: MISSING {a[:90]}"
        s = s.replace(a, b, 1)
    open(p, 'w').write(s)

patch('agent/core.py', [
 ('''from .lexicon import unknown_words, add_known''', '''from .lexicon import unknown_words, add_known
from . import trace as TR'''),
 ('''    no_data: bool = False
    ms: float = 0.0''', '''    no_data: bool = False
    ms: float = 0.0
    trace: dict = field(default_factory=dict)'''),
 ('''def nodata(lang, why_key, route, **kw):
    why = T[why_key][lang].format(**kw)''', '''def nodata(lang, why_key, route, **kw):
    why = T[why_key][lang].format(**kw)
    TR.step("refuse", reason=route, why_key=why_key, detail=kw)'''),
 ('''def q(sql):
    return con().cursor().execute(sql).fetchall()   # cursor per query: safe under a threaded web server''',
  '''def q(sql):
    t0 = time.perf_counter()
    rows = con().cursor().execute(sql).fetchall()   # cursor per query: safe under a threaded web server
    TR.step("sql", sql=sql, rows=len(rows), db_ms=round((time.perf_counter() - t0) * 1000, 2), sample=rows[:3])
    return rows'''),
 ('''def answer(question: str, use_llm=True) -> Answer:
    t0 = time.perf_counter()
    a = _answer(question, use_llm)
    a.ms = (time.perf_counter() - t0) * 1000
    return a''', '''def answer(question: str, use_llm=True) -> Answer:
    t0 = time.perf_counter()
    tr = TR.start(question)
    try:
        a = _answer(question, use_llm)
    except Exception as ex:
        TR.step("error", error=repr(ex)[:500])
        raise
    a.ms = (time.perf_counter() - t0) * 1000
    TR.step("answer", route=a.route, confidence=a.confidence, no_data=a.no_data, text=a.text, total_ms=round(a.ms, 1))
    tr.data.update(mode=MODE, route=a.route, no_data=a.no_data, total_ms=round(a.ms, 1))
    a.trace = tr.data
    return a'''),
 ('''    e = extract(question)
    if MODE == "gemini" and use_llm:''', '''    e = extract(question)
    TR.step("input", lang=lang, normalized=t, mode=MODE)
    TR.step("rule_entities", products=e.products, regions=e.regions, inns=e.inns, brokers=e.brokers,
            period=e.period_label, years=e.years, unknown_product=e.unknown_product, out_of_range=e.out_of_range)
    if MODE == "gemini" and use_llm:'''),
 ('''    if not p: return None
    interp = (p.get("interpretation") or "").strip()''', '''    if not p:
        TR.step("llm_error", error="no plan returned"); return None
    interp = (p.get("interpretation") or "").strip()'''),
 ('''    kind = p.get("kind") or "deals"
    basis = {"ru": "Основание", "uz": "Asos"}[lang]''', '''    kind = p.get("kind") or "deals"
    TR.step("verify", kind=kind, products=resolved_products, product_bases=[b for b in (p.get("product_bases") or []) if b in BASES()],
            dropped_bases=[b for b in (p.get("product_bases") or []) if b not in BASES()], regions=e.regions, inns=e.inns,
            brokers=e.brokers, period=e.period_label, years=e.years, mentioned_products=p.get("mentioned_products"),
            mentioned_places=p.get("mentioned_places"))
    basis = {"ru": "Основание", "uz": "Asos"}[lang]'''),
 ('''    for nm in p.get("company_names") or []:
        m = match_company(nm)''', '''    for nm in p.get("company_names") or []:
        m = match_company(nm)
        TR.step("company_match", written=nm, key=name_key(nm), matched=m)'''),
 ('''    if not conf: return None
    try:
        out = S.run_spec(spec, t, e, lang)''', '''    TR.step("rule_spec", spec=spec.__dict__, confident=conf)
    if not conf: return None
    try:
        out = S.run_spec(spec, t, e, lang)'''),
])
patch('agent/gemini.py', [
 ('''            r = json.load(urllib.request.urlopen(req, timeout=timeout))
            txt = r["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(txt)''', '''            t0 = time.perf_counter()
            r = json.load(urllib.request.urlopen(req, timeout=timeout))
            txt = r["candidates"][0]["content"]["parts"][0]["text"]
            plan_ = json.loads(txt)
            from . import trace as TR
            TR.step("llm_plan", model=MODEL, think=THINK, attempt=attempt + 1, llm_ms=round((time.perf_counter() - t0) * 1000),
                    usage=r.get("usageMetadata", {}), plan={k: v for k, v in plan_.items() if v not in ("", "none", [], None, False, 0)})
            return plan_'''),
 ('''            if ex.code in (429, 500, 503): time.sleep(1.5 * (attempt + 1)); continue''', '''            from . import trace as TR
            TR.step("llm_error", code=ex.code, message=msg[:200], attempt=attempt + 1)
            if ex.code in (429, 500, 503): time.sleep(1.5 * (attempt + 1)); continue'''),
])
patch('agent/semantic.py', [
 ('''    F = filters_from_plan(p, e, t, s)
    sql, shape = compile_spec(s, F, e, 0 if lang == "ru" else 1)
    if not sql: return None''', '''    F = filters_from_plan(p, e, t, s)
    from . import trace as TR
    TR.step("spec", source="gemini", spec={k: v for k, v in s.__dict__.items() if v not in (None, False, "")})
    TR.step("filters", where=F.where, cond=F.cond, notes=F.notes)
    sql, shape = compile_spec(s, F, e, 0 if lang == "ru" else 1)
    TR.step("compiled", shape=shape)
    if not sql: return None'''),
 ('''def run_contracts(p, t, e, lang):''', '''def run_contracts(p, t, e, lang):
    from . import trace as TR
    TR.step("spec", source="gemini", kind="contracts", metric=p.get("metric"), group_by=p.get("group_by"))'''),
])
print("ok")
