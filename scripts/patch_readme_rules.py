def patch(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert a in s, f"{p}: MISSING {a[:90]}"
        s = s.replace(a, b, 1)
    open(p, 'w').write(s)

S = 'agent/semantic.py'
patch(S, [
 # prices in the contract currency: no hidden sum-only filter; USD only when asked
 ('''        return ["round(sum(amount)/sum(volume_units),2)", "count(*)", "round(avg(price_per_unit),2)"], ["@price", "сделок", "@price"], [f"currency='{cur}'"],''',
  '''        return ["round(sum(amount)/sum(volume_units),2)", "count(*)", "round(avg(price_per_unit),2)"], ["@price", "сделок", "@price"], (["currency='Доллар США'"] if s.usd else []),'''),
 ('''        return [f"round({f}(price_per_unit),2)", "count(*)"], ["@price", "сделок"], [f"currency='{cur}'"],''',
  '''        return [f"round({f}(price_per_unit),2)", "count(*)"], ["@price", "сделок"], (["currency='Доллар США'"] if s.usd else []),'''),
 ('''    unit_expr = "CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END" if (s.tonnes and m == "volume") else "unit"''',
  '''    unit_expr = "CASE WHEN unit='килограмм' THEN 'тонна' ELSE unit END" if (s.tonnes and m == "volume") else "unit"
    if m in ("avg_price", "price_stat"): unit_expr = "unit || '|' || currency"     # prices stay in each contract's currency'''),
 ('''    if lab == "@price": return f"{C.fmt(v, 2)} {cur} за 1 {unit}"''', '''    if lab == "@price":
        if "|" in (unit or ""):
            unit, cn = unit.split("|", 1)
            cur = "USD" if cn == "Доллар США" else {"ru": "сум", "uz": "so'm"}[lang]
        return f"{C.fmt(v, 2)} {cur} за 1 {unit}"
    if lab == "лотов_": return f"{C.fmt(v)} " + {"ru": "лотов", "uz": "lot"}[lang]'''),
 # most expensive / cheapest deal: compare in UZS-converted unit price, show contract currency
 ('''        col = "price_per_unit" if s.stat == "price" else "amount_uzs"
        w = base_where + cond + (["currency='Узбекский сум'"] if col == "price_per_unit" else [])
        sql = (f"SELECT deal_id, deal_date, product_name, round({col},2), unit, round(amount_uzs), qty_lots, lot_size, "''',
  '''        col = "amount_uzs / NULLIF(volume_units, 0)" if s.stat == "price" else "amount_uzs"
        if s.stat == "price": F.notes.append("цены сравниваются в сумах (экспорт пересчитан по курсу на дату сделки)")
        w = base_where + cond
        sql = (f"SELECT deal_id, deal_date, product_name, round(price_per_unit,2) || CASE WHEN currency='Доллар США' THEN ' USD' ELSE '' END, unit, round(amount_uzs), qty_lots, lot_size, "'''),
 # lots metric + hour dimension
 ('''    if m == "count": return ["count(*)"], ["сделок"], [], None''', '''    if m == "count": return ["count(*)"], ["сделок"], [], None
    if m == "lots": return ["round(sum(qty_lots))", "count(*)", "round(sum(volume_units),2)"], ["лотов", "сделок", "@unit"], [], "лоты = поле «Количество» (сколько лотов куплено)"'''),
 ('''        "auction_type": ("auction_type", "NULL", "", None),''', '''        "auction_type": ("auction_type", "NULL", "", None),
        "hour": ("substr(deal_time, 1, 2) || ':00–' || substr(deal_time, 1, 2) || ':59'", "CAST(substr(deal_time, 1, 2) AS INT)", "", None),'''),
 ('''METRICS = ("price_change",''', '''METRICS = ("lots", "price_change",'''),
 ('''DIMS = ("product_group",''', '''DIMS = ("hour", "product_group",'''),
 # statuses are "as of end of 30.10.2026"
 ('''        sql, note = STATUS_SQL.get(st, (f"status={C.sqlq(st)}", f"статус: {st}"))
        put("status", sql, note); F.has_status = True''', '''        sql, note = STATUS_SQL.get(st, (f"status={C.sqlq(st)}", f"статус: {st}"))
        put("status", sql, note + " (состояние на конец дня 30.10.2026)"); F.has_status = True'''),
])
s = open(S).read()
# deal rendering: price string already carries currency
s = s.replace('''            val = (f"{C.fmt(v, 2)} " + {"ru": "сум за 1 ", "uz": "so'm / 1 "}[lang] + u) if s.stat == "price" else f"{C.fmt(amt)} " + {"ru": "сум", "uz": "so'm"}[lang]''',
              '''            vv = str(v); usd = vv.endswith(" USD"); num = float(vv.replace(" USD", "")) if vv.replace(" USD", "").replace(".", "", 1).isdigit() else vv
            val = (f"{C.fmt(num, 2)} " + ("USD" if usd else {"ru": "сум", "uz": "so'm"}[lang]) + {"ru": " за 1 ", "uz": " / 1 "}[lang] + u) if s.stat == "price" else f"{C.fmt(amt)} " + {"ru": "сум", "uz": "so'm"}[lang]''')
open(S, 'w').write(s)

patch('agent/gemini.py', [
 ('''"delay", "delivered", "undelivered", "deal", "price_change",''', '''"delay", "delivered", "undelivered", "deal", "price_change", "lots",'''),
 ('''"month", "year", "quarter", "status", "contract_form", "contract_type", "side",''', '''"month", "year", "quarter", "hour", "status", "contract_form", "contract_type", "side",'''),
 ('''  price_change = how much the final unit price differs from the start price, in % (set stat).''', '''  price_change = how much the final unit price differs from the start price, in % (set stat); lots = number of lots bought (field Количество).
  Prices are in each contract's currency (UZS or USD); usd=true only when the question asks for dollars.
  group_by hour = time of day (deals happen 10:00–17:00 Tashkent time). Deal statuses are as of the end of 30.10.2026.'''),
])
print("ok")
