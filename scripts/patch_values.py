import re
def patch(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert a in s, f"{p}: MISSING {a[:90]}"
        s = s.replace(a, b, 1)
    open(p, 'w').write(s)

G = 'agent/gemini.py'
s = open(G).read()
# ---- schema: enums from the database
s = re.sub(r'"status": \{"type": "STRING", "enum": \["none", "not_annulled", "Новый".*?\]\},',
           '"status": {"type": "STRING", "enum": ["none", "not_annulled", "executed_full_or_partial"] + VV.enums["status"]},', s, flags=re.S)
s = re.sub(r'"contract_type": \{"type": "STRING", "enum": \["none", "Внутренний", "Экспортный"\]\},',
           '"contract_type": {"type": "STRING", "enum": ["none"] + VV.enums["contract_type"]},', s)
s = re.sub(r'"contract_form": \{"type": "STRING", "enum": \["none", "Спот", "Форвард", "Фьючерс"\]\},',
           '"contract_form": {"type": "STRING", "enum": ["none"] + VV.enums["contract_form"]},', s)
s = re.sub(r'"auction_type": \{"type": "STRING", "enum": \["none", "Английский аукцион", "Обратный аукцион", "Двусторонний аукцион"\]\},',
           '"auction_type": {"type": "STRING", "enum": ["none"] + VV.enums["auction_type"]},', s)
s = re.sub(r'"unit": \{"type": "STRING", "enum": \["none", "тонна".*?\]\},', '"unit": {"type": "STRING", "enum": ["none"] + VV.enums["unit"]},', s, flags=re.S)
s = re.sub(r'"product_group": \{"type": "STRING", "enum": \[.*?\]\},', '"product_group": {"type": "STRING", "enum": ["none"] + VV.enums["product_group"]},', s, flags=re.S)
s = re.sub(r'"product_category": \{"type": "STRING", "enum": \[.*?\]\},', '"product_category": {"type": "STRING", "enum": ["none"] + VV.enums["product_category"]},', s, flags=re.S)
s = re.sub(r'"industry": \{"type": "STRING", "enum": \[.*?\]\},', '"industry": {"type": "STRING", "enum": ["none"] + VV.enums["industry"]},', s, flags=re.S)
s = re.sub(r'"delivery_basis": \{"type": "STRING", "enum": \[.*?\]\},', '"delivery_basis": {"type": "STRING", "enum": ["none"] + VV.enums["delivery_basis"]},', s, flags=re.S)
s = re.sub(r'"activity": \{"type": "STRING", "enum": \[.*?\]\},', '"activity": {"type": "STRING", "enum": ["none"] + VV.enums["activity"]},', s, flags=re.S)
s = s.replace('"packaging": S, "tnved": S,', '"packaging": {"type": "STRING", "enum": ["none"] + VV.enums["packaging"]}, "tnved": S,')
s = s.replace('''        "mentioned_products": arr(S), "mentioned_places": arr(S),
        "products": arr(S), "product_variant": S, "product_bases": arr(S),
        "regions": arr(S),''', '''        "mentioned_products": arr({"type": "OBJECT", "properties": {"as_written": S, "registry_term": S}, "required": ["as_written", "registry_term"]}),
        "mentioned_places": arr(S), "product_bases": arr(S),
        "regions": {"type": "ARRAY", "items": {"type": "STRING", "enum": VV.regions}},''')
s = s.replace('''def _schema():
    S, N, B, I''', '''def _schema():
    from .values import V
    VV = V()
    S, N, B, I''')
for a in [r'"status": \{"type": "STRING", "enum": \["none", "not_annulled", "executed_full_or_partial"\] \+ VV.enums\["status"\]\}',
          r'"regions": \{"type": "ARRAY"']:
    assert re.search(a, s), a
# ---- prompt: no hand-written product names; registry list + enums come from data
s = s.replace('''products: canonical names from this list only: {products}
product_variant: grade/brand/form text if the question names a specific one (e.g. "АИ-92", "высший сорт", "спот", "4-класс", "pp-0350"), else "".
mentioned_products: EVERY specific commodity/product the question names, copied exactly as written (any language), even if it is
  not in the registry (e.g. "кукуруза", "золото"). Not generic words ("товар", "продукция") and not categories ("нефтепродукты").''',
'''mentioned_products: EVERY specific commodity/product the question names, one object each:
  as_written = exactly as in the question (any language/script); registry_term = how this Russian registry would name it, in Russian,
  keeping any grade/brand/form the question names (e.g. "bug'doy" -> "пшеница", "qand"/"shakar" -> "сахар", "солярка" -> "дизельное топливо",
  "аи 92" -> "бензин аи-92", "paxta (spot)" -> "хлопок-сырец спот", "кукуруза" -> "кукуруза" even though it may not exist).
  Not generic words ("товар", "продукция") and not categories ("нефтепродукты" is a product_category).''')
s = s.replace('''product_bases: the EXACT registry product names (copy them from this list)''', '''product_bases: the EXACT registry product names (copy them from this list)''')
s = s.replace('''regions: from this list only: {regions}.''', '''regions: registry regions (Latin Uzbek names) that the places in the question belong to — cities map to their region
  (e.g. Термез -> Surxondaryo viloyati, Нукус -> Qoraqalpog‘iston Respublikasi). Only from: {regions}.''')
# examples: use the new mention objects instead of canonical product lists
s = s.replace('''    products ["сахар"], regions ["Samarqand viloyati"], side seller''', '''    mentioned_products [{"as_written":"сахара","registry_term":"сахар"}], regions ["Samarqand viloyati"], side seller''')
s = s.replace('''Q: Qufil Agro 2024-yilda qancha bug'doy sotgan va qancha summaga
 -> metric amount, products ["пшеница"],''', '''Q: Qufil Agro 2024-yilda qancha bug'doy sotgan va qancha summaga
 -> metric amount, extra_metrics ["volume"], mentioned_products [{"as_written":"bug'doy","registry_term":"пшеница"}],''')
s = s.replace('''Q: paxta 2023 va 2024 solishtir -> metric amount, compare year, products ["хлопок-сырец"], years [2023, 2024]''',
              '''Q: paxta 2023 va 2024 solishtir -> metric amount, compare year, mentioned_products [{"as_written":"paxta","registry_term":"хлопок-сырец"}], years [2023, 2024]''')
s = s.replace('''delay_of delivery_after_payment, products ["дизельное топливо"]''', '''delay_of delivery_after_payment, mentioned_products [{"as_written":"дизель","registry_term":"дизельное топливо"}]''')
s = s.replace('''status "Аннулирован(Не оплачен)", products ["цемент"]''', '''status "Аннулирован(Не оплачен)", mentioned_products [{"as_written":"цементу","registry_term":"цемент"}]''')
s = s.replace('''Q: самая крупная сделка по пшенице -> metric deal, deal_by amount, order desc, limit 1, products ["пшеница"]''',
              '''Q: самая крупная сделка по пшенице -> metric deal, deal_by amount, order desc, limit 1, mentioned_products [{"as_written":"пшенице","registry_term":"пшеница"}]''')
s = s.replace('''Q: сколько сделок по рису в 2024 и сколько тонн -> metric count, extra_metrics ["volume"], tonnes true, products ["рис"], date 2024''',
              '''Q: сколько сделок по рису в 2024 и сколько тонн -> metric count, extra_metrics ["volume"], tonnes true, mentioned_products [{"as_written":"рису","registry_term":"рис"}], date 2024''')
s = s.replace('''Q: qancha shartnoma sement bo'yicha ro'yxatdan o'tgan -> kind contracts, metric contracts, products ["цемент"]''',
              '''Q: qancha shartnoma sement bo'yicha ro'yxatdan o'tgan -> kind contracts, metric contracts, mentioned_products [{"as_written":"sement","registry_term":"цемент"}]''')
s = s.replace('''share_condition late, late delivered_late, products ["сахар"]''', '''share_condition late, late delivered_late, mentioned_products [{"as_written":"сахару","registry_term":"сахар"}]''')
s = s.replace('''Statuses: Новый (concluded on the last day)''', '''Statuses (values come from the data): Новый (concluded on the last day)''')
assert 'products ["' not in s, "leftover canonical product example"
open(G, 'w').write(s)

patch('agent/core.py', [
 ('''        p = G.plan(question, [n for n, _, _ in PRODUCTS], list(REGIONS), bases=sorted(BASES()), lang=lang)''',
  '''        from .values import V as _V
        p = G.plan(question, [], _V().regions, bases=_V().products, lang=lang)'''),
 ('''    known = {n for n, _, _ in PRODUCTS}
    e.products = [x for x in (p.get("products") or []) if x in known] or e.products
    regs = [r for r in (_region_of(x) for x in (p.get("regions") or [])) if r]
    e.regions = list(dict.fromkeys(regs)) or e.regions''', '''    # ---- products and regions: resolved against values read from the database (no hand-written lists)
    from .values import V as _V, resolve_product, resolve_region
    vb = _V()
    category_set = any((p.get(f) or "none") != "none" for f in ("product_category", "product_group", "industry"))
    sets = []
    for m in p.get("mentioned_products") or []:
        raw, term = (m.get("as_written", ""), m.get("registry_term", "")) if isinstance(m, dict) else (str(m), str(m))
        names, how = resolve_product(term or raw)
        TR.step("product_resolution", written=raw, registry_term=term, resolved=names[:25], n=len(names), how=how)
        if not names:
            return nodata(lang, "product", "unresolved-product", x=raw or term)
        sets.append((term or raw, names))
    if not sets:
        picks = [b for b in (p.get("product_bases") or []) if b in vb.products]
        if picks: sets.append(("товар", picks))
    e.product_sets = sets
    e.products = [lab for lab, _ in sets]
    p["product_bases"] = sorted({b for _, n in sets for b in n})
    regs = [r for r in (resolve_region(x) for x in (p.get("regions") or [])) if r]
    e.regions = list(dict.fromkeys(regs))'''),
 ('''    resolved_products = [b for b in (p.get("product_bases") or []) if b in BASES()] or e.products
    category_set = any((p.get(f) or "none") != "none" for f in ("product_category", "product_group", "industry"))
    for raw in p.get("mentioned_products") or []:
        if not resolved_products and not category_set:
            return nodata(lang, "product", "unresolved-product", x=raw)
    if (p.get("mentioned_places")''', '''    resolved_products = p.get("product_bases") or []
    if (p.get("mentioned_places")'''),
])

S = 'agent/semantic.py'
patch(S, [
 # compare by product: use the resolved sets, not hand-written regexes
 ('''            elif s.compare == "product": vw = [product_sql(v)]''', '''            elif s.compare == "product":
                sets = dict(getattr(e, "product_sets", []) or [])
                vw = ["product_base IN (" + ",".join(C.sqlq(b) for b in sets[v]) + ")"] if v in sets else [product_sql(v)]'''),
 ('''    picked = [b for b in (p.get("product_bases") or []) if b in BASES()]
    groups = [n for n, rx, _ in PRODUCTS if any(C.q(f"SELECT 1 WHERE {C.sqlq(b)} SIMILAR TO {C.sqlq(rx)}") for b in picked)] if picked else []
    if len(groups) >= 2 and (s.group_by == "product" or s.compare == "product") and not s.share:
        e.products = groups; s.compare = "product"; s.group_by = None; p = {**p, "product_bases": []}''',
  '''    sets = getattr(e, "product_sets", []) or []
    if len(sets) >= 2 and (s.group_by == "product" or s.compare == "product") and not s.share:
        e.products = [lab for lab, _ in sets]; s.compare = "product"; s.group_by = None; p = {**p, "product_bases": []}'''),
])
print("ok")
