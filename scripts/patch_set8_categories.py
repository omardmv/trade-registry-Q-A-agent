def patch(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert a in s, f"{p}: MISSING {a[:90]}"
        s = s.replace(a, b, 1)
    open(p, 'w').write(s)
patch('agent/gemini.py', [
 ('''"distinct_of": {"type": "STRING", "enum": ["none", "seller", "buyer", "company", "broker", "product", "region"]},''',
  '''"distinct_of": {"type": "STRING", "enum": ["none", "seller", "buyer", "company", "broker", "seller_broker", "buyer_broker", "product", "region"]},
        "statuses": {"type": "ARRAY", "items": {"type": "STRING", "enum": VV.enums["status"]}}, "include_annulled": B,'''),
 ('''"month", "year", "quarter", "hour", "status",''', '''"month", "year", "quarter", "hour", "unit", "status",'''),
 ('''  as_written = exactly as in the question (any language/script); registry_term = how this Russian registry would name it, in Russian,
  keeping any grade/brand/form the question names''', '''  as_written = exactly as in the question (any language/script); registry_term = how this Russian registry would name it, in Russian:
  the GENERIC product name (e.g. "известь", "арматура", "кирпич") unless the question itself names a grade/brand/form — then keep it''')
 ,('''years: the years if two years are compared. status: filter if asked''', '''statuses: several statuses at once ("ждёт оплату или оплачен") — then status=none. include_annulled: true when the question
  explicitly says to count annulled/cancelled deals too ("включая аннулированные", "bekor qilinganlarni ham").
years: the years if two years are compared. status: filter if asked'''),
])
patch('agent/semantic.py', [
 ('''DISTINCT_OF = ("seller", "buyer", "company", "broker", "product", "region")''', '''DISTINCT_OF = ("seller", "buyer", "company", "broker", "seller_broker", "buyer_broker", "product", "region")'''),
 ('''        col = {"seller": "seller_inn", "buyer": "buyer_inn", "product": "product_base", "region": "region"}.get(s.of)
        if col: return [f"count(DISTINCT {col})"], [{"seller": "продавцов", "buyer": "покупателей", "product": "наименований товара", "region": "регионов"}[s.of]], [], "считаются различные значения, встречающиеся в сделках"''',
  '''        col = {"seller": "seller_inn", "buyer": "buyer_inn", "product": "product_base", "region": "region", "seller_broker": "seller_broker", "buyer_broker": "buyer_broker"}.get(s.of)
        if col: return [f"count(DISTINCT {col})"], [{"seller": "продавцов", "buyer": "покупателей", "product": "наименований товара", "region": "регионов",
                                                     "seller_broker": "брокеров продавца", "buyer_broker": "брокеров покупателя"}[s.of]], [], "считаются различные значения, встречающиеся в сделках"'''),
 ('''DIMS = ("hour",''', '''DIMS = ("unit", "hour",'''),
 ('''        "hour": (''', '''        "unit": ("unit", "NULL", "", None),
        "hour": ('''),
 ('''    st = p.get("status")
    if st and st != "none":''', '''    sts = [x for x in (p.get("statuses") or []) if isinstance(x, str)]
    if len(sts) >= 2:
        put("status", "status IN (" + ",".join(C.sqlq(x) for x in sts) + ")", "статусы: " + ", ".join(sts) + " (состояние на конец дня 30.10.2026)"); F.has_status = True
    elif p.get("include_annulled") is True:
        F.has_status = True; F.notes.append("включая аннулированные сделки (по условию вопроса)")
    st = p.get("status") if len(sts) < 2 else None
    if st and st != "none":'''),
])
# lenient scorer: '10 %' == 10.0 and '03.01.2022' == '2022-01-03'
p = 'eval/run_agent.py'; s = open(p).read()
a = '''        if LENIENT and (num(g) or num(o)) and str(g).strip() == str(o).strip(): return True  # 2023 vs '2023\''''
assert a in s
s = s.replace(a, a + '''
        if LENIENT:
            import re as _re
            def as_num(x):
                m = _re.fullmatch(r"\\s*(-?\\d+(?:[.,]\\d+)?)\\s*%?\\s*", str(x)); return float(m.group(1).replace(",", ".")) if m else None
            def as_date(x):
                m = _re.fullmatch(r"(\\d{2})\\.(\\d{2})\\.(\\d{4})", str(x).strip()); return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else str(x).strip()[:10]
            if as_num(g) is not None and as_num(o) is not None and close(as_num(g), as_num(o)): return True    # '10 %' vs 10.0
            if as_date(g) == as_date(o) and _re.match(r"\\d{4}-\\d{2}-\\d{2}", as_date(g)): return True            # 03.01.2022 vs 2022-01-03''')
open(p, 'w').write(s)
print("ok")
