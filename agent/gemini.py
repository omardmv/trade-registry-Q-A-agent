"""Gemini as the language-understanding layer.

The model reads the question (Russian / Uzbek Latin / Uzbek Cyrillic) and returns a JSON plan:
  - answerable + missing: can this database answer it at all (replaces the hard-coded word list)
  - the semantic Spec (metric, grouping, order, limit, share, compare...)
  - entities picked from CLOSED lists (canonical products, regions, statuses) or copied from the text (company names)
It never sees data rows and never produces numbers. Code validates every field and runs the query.
"""
import json, os, re, time, urllib.request
from datetime import date

MODEL = os.environ.get("UZEX_GEMINI_MODEL", "gemini-3.5-flash-lite")
THINK = os.environ.get("UZEX_GEMINI_THINK", "minimal")
_KEY = None


def key():
    global _KEY
    if _KEY is None:
        _KEY = os.environ.get("GEMINI_API_KEY")
        if not _KEY and os.path.exists(".env"):
            for line in open(".env"):
                if line.startswith("GEMINI_API_KEY="): _KEY = line.split("=", 1)[1].strip()
    return _KEY


def available(): return bool(key())


def _schema():
    from .values import V
    VV = V()
    S, N, B, I = {"type": "STRING"}, {"type": "NUMBER"}, {"type": "BOOLEAN"}, {"type": "INTEGER"}
    arr = lambda x: {"type": "ARRAY", "items": x}
    props = {
        "analysis": S, "answerable": B, "missing": S, "interpretation": S,
        "kind": {"type": "STRING", "enum": ["deals", "contracts", "contract_lookup", "usd_rate", "company_info", "broker_info", "participants_directory", "brokers_directory"]},
        "metric": {"type": "STRING", "enum": ["count", "amount", "volume", "avg_price", "price_stat", "amount_stat", "distinct",
                                               "delay", "delivered", "undelivered", "deal", "price_change", "lots",
                                               "contracts", "base_price", "deposit", "pay_term", "delivery_term"]},
        "extra_metrics": arr({"type": "STRING", "enum": ["count", "amount", "volume", "avg_price", "amount_stat", "price_change", "delay"]}),
        "stat": {"type": "STRING", "enum": ["none", "avg", "median", "min", "max", "stddev"]},
        "distinct_of": {"type": "STRING", "enum": ["none", "seller", "buyer", "company", "broker", "product", "region"]},
        "delay_of": {"type": "STRING", "enum": ["none", "payment", "delivery", "delivery_after_payment"]},
        "deal_by": {"type": "STRING", "enum": ["none", "amount", "price"]},
        "group_by": {"type": "STRING", "enum": ["none", "region", "seller", "buyer", "seller_broker", "buyer_broker", "any_broker", "product",
                                                 "month", "year", "quarter", "hour", "status", "contract_form", "contract_type", "side",
                                                 "product_group", "product_category", "industry", "delivery_basis", "packaging", "tnved", "auction_type"]},
        "order": {"type": "STRING", "enum": ["desc", "asc"]}, "limit": I,
        "share": B, "share_condition": {"type": "STRING", "enum": ["none", "status", "contract_type", "contract_form", "auction_type", "late", "region",
                                                         "product", "delivery_basis", "packaging", "product_category", "industry"]},
        "contract_ids": arr(S),
        "lookup_fields": arr({"type": "STRING", "enum": ["tnved", "deposit_pct", "delivery_basis", "packaging", "pay_term_days", "delivery_term_days",
                                                        "base_price", "warehouse_address", "reg_date", "valid_to", "product_base", "seller", "region",
                                                        "brand", "grade", "pclass", "unit", "lot_size", "currency", "contract_form", "contract_type",
                                                        "auction_type", "product_category", "product_group", "industry"]}),
        "activity": {"type": "STRING", "enum": ["none"] + VV.enums["activity"]},
        "compare": {"type": "STRING", "enum": ["none", "region", "product", "year"]},
        "usd": B, "tonnes": B,
        "unit": {"type": "STRING", "enum": ["none"] + VV.enums["unit"]},
        "mentioned_products": arr({"type": "OBJECT", "properties": {"as_written": S, "registry_term": S}, "required": ["as_written", "registry_term"]}),
        "mentioned_places": arr(S), "product_bases": arr(S),
        "regions": {"type": "ARRAY", "items": {"type": "STRING", "enum": VV.regions}}, "region_scope": {"type": "STRING", "enum": ["warehouse", "seller_company", "buyer_company"]},
        "company_names": arr(S), "inns": arr(S), "brokers": arr(S),
        "side": {"type": "STRING", "enum": ["any", "seller", "buyer"]},
        "date_from": S, "date_to": S, "years": arr(I),
        "status": {"type": "STRING", "enum": ["none", "not_annulled", "executed_full_or_partial"] + VV.enums["status"]},
        "contract_type": {"type": "STRING", "enum": ["none"] + VV.enums["contract_type"]},
        "contract_form": {"type": "STRING", "enum": ["none"] + VV.enums["contract_form"]},
        "auction_type": {"type": "STRING", "enum": ["none"] + VV.enums["auction_type"]},
        "vat": {"type": "STRING", "enum": ["none", "yes", "no"]},
        "late": {"type": "STRING", "enum": ["none", "delivered_late", "delivered_on_time", "paid_late", "paid_on_time"]},
        "product_group": {"type": "STRING", "enum": ["none"] + VV.enums["product_group"]},
        "product_category": {"type": "STRING", "enum": ["none"] + VV.enums["product_category"]},
        "industry": {"type": "STRING", "enum": ["none"] + VV.enums["industry"]},
        "delivery_basis": {"type": "STRING", "enum": ["none"] + VV.enums["delivery_basis"]},
        "packaging": {"type": "STRING", "enum": ["none"] + VV.enums["packaging"]}, "tnved": S,
        "contract_date_mode": {"type": "STRING", "enum": ["none", "registered", "active"]},
        "rate_date": S, "rate_mode": {"type": "STRING", "enum": ["none", "on_date", "avg", "max", "min", "first", "last", "change"]},
    }
    return {"type": "OBJECT", "properties": props, "required": list(props), "propertyOrdering": list(props)}


def _prompt(products, regions, bases=()):
    return f"""You are the query planner for a question-answering bot over a commodity-exchange trades registry (UZEX, fictional data).
Questions come in Russian, Uzbek Latin or Uzbek Cyrillic. You do NOT answer; you output a JSON plan that code will execute.

WHAT THE DATABASE CONTAINS (nothing else):
- deals 03.01.2022–30.10.2026: date, product (Russian names), lots, lot size, unit, amount (UZS, or USD for export), start price,
  region of the seller's warehouse, contract type (Внутренний/Экспортный), form (Спот/Форвард/Фьючерс), auction type, status,
  seller INN, buyer INN, seller broker code, buyer broker code (BK001–BK120), payment date, delivery date, delivered lots.
  Each deal belongs to a contract card (contracts.csv): product group (Высоколиквидные/Прочие товары), product category
  (Нефтепродукты, Строительные материалы, Продовольственные товары...), industry (Сельское хозяйство, Металлургия...), customs code ТН ВЭД,
  brand/size/grade/type/class, unit and lot, currency, base price per unit at registration, deposit % (Сумма залога), delivery basis
  (EXW/FCA/DAP/CPT), packaging (навалом, в мешках...), warehouse region and address, payment term and delivery term in days,
  registration date and valid-until date. Derived per deal: delivered late (delivery date > payment date + delivery term),
  paid late (payment date > deal date + payment term), price change vs start price in %.
- participants: INN (STIR), company name, registration region, activity type, role (seller or buyer), VAT payer yes/no.
- brokers: code, name, INN, region.  - USD/UZS exchange rate per day.
Statuses (values come from the data): Новый (concluded on the last day), Не подписан (not signed), Заключен (forward/futures signed), Ждёт оплату (awaiting payment),
  Оплачен (paid, not yet delivered), Исполнен (FULLY executed: paid and fully delivered; "to'liq ijro/bajarilgan", "полностью исполнен"),
  Исполнен(Частично) (partially delivered), Аннулирован(Не оплачен) (cancelled, not paid: the deal did not happen).
  Use executed_full_or_partial only when the question explicitly includes both full and partial execution.
Set answerable=false (and say in "missing" what is absent, in the question's language) for anything else: forecasts, reasons/why,
people (directors, founders, employees), phones, addresses of offices, other currencies than USD, other exchanges/world prices,
taxes, profits, ratings, insurance, transport, warehouses count, standards, sessions, fees, commissions, subsidies, anything not listed.
Questions about deals of a product/region/period that simply may have zero rows ARE answerable.
NEVER set answerable=false just because a company, product or place name looks unfamiliar or is misspelled: put it in the right field
(company_names, product_bases, regions) and code will check it against the registry.

PLAN FIELDS:
kind: contract_lookup = read card fields: of specific contracts by number (contract_ids, lookup_fields), or which values a field takes
      for a product ("какой код ТН ВЭД у хлопка", "какой залог у цемента"; lookup_fields + product_bases) |
      deals | contracts (questions about contract cards themselves: how many contracts, base price, deposit %, payment/delivery
      term of contracts, contracts registered/valid in a period; metric contracts/base_price/deposit/pay_term/delivery_term) | usd_rate | company_info (who is INN X / company name) | broker_info (who is broker BKxxx) |
      participants_directory (count/share of registered companies by region/role/activity/VAT) | brokers_directory (count brokers by region)
metric (for deals): count=number of deals; amount=money total; volume=quantity traded; avg_price=average price per unit;
  price_stat=min/max/median/stddev of unit price (set stat); amount_stat=avg/median/min/max of one deal's amount (average deal size → avg);
  distinct=number of different sellers/buyers/companies/brokers/products/regions (set distinct_of); delay=days between deal/payment/delivery
  (set delay_of, stat); delivered / undelivered = lots delivered / not delivered; deal=show specific deal(s) (largest/cheapest; set deal_by, order, limit);
  price_change = how much the final unit price differs from the start price, in % (set stat); lots = number of lots bought (field Количество).
  Prices are in each contract's currency (UZS or USD); usd=true only when the question asks for dollars.
  group_by hour = time of day (deals happen 10:00–17:00 Tashkent time). Deal statuses are as of the end of 30.10.2026.
extra_metrics: further metrics when the question asks for several things at once ("how many deals AND for what amount", "tonnes and number of deals").
late: deals delivered/paid late or on time (use share=true, share_condition=late for "what % were delivered late").
product_group / product_category / industry / delivery_basis / packaging / tnved: contract-card filters when the question names them
  (e.g. "нефтепродукты" → product_category Нефтепродукты; "строительная индустрия" → industry). Also usable in group_by.
contract_date_mode: for kind=contracts with a period: registered (default) or active (valid during the period).
group_by: dimension when the answer is a ranking or breakdown ("which region", "top-3 buyers", "per month", "by status"). none otherwise.
  any_broker = broker on either side. seller/buyer = companies. side = split one company's/broker's numbers into "as seller" vs "as buyer". For "which month/year had most" use month/year with limit 1.
order: desc for most/largest/top, asc for least/cheapest. limit: N for top-N, 1 for "which one / who", 0 if not a ranking.
share: true for percentages; share_condition = the attribute that defines the numerator (e.g. status for "% annulled").
compare: region/product/year when two of them are compared ("A or B, where more"). Put both values in regions/products/years.
usd: true only if the answer must be in US dollars. tonnes: true if tonnes are asked. unit: set if the question names a unit (metres, litres...).
mentioned_products: EVERY specific commodity/product the question names, one object each:
  as_written = exactly as in the question (any language/script); registry_term = how this Russian registry would name it, in Russian,
  keeping any grade/brand/form the question names (e.g. "bug'doy" -> "пшеница", "qand"/"shakar" -> "сахар", "солярка" -> "дизельное топливо",
  "аи 92" -> "бензин аи-92", "paxta (spot)" -> "хлопок-сырец спот", "кукуруза" -> "кукуруза" even though it may not exist).
  Not generic words ("товар", "продукция") and not categories ("нефтепродукты" is a product_category).
mentioned_places: EVERY region/city/country the question names, exactly as written (e.g. "в Самарканде", "Москва"). [] if none.
product_bases: the EXACT registry product names (copy them from this list) that match what the user means. People use synonyms,
  nicknames, Uzbek or Russian words and different spellings for the same thing; map them onto these names. The registry spells one
  product in several ways — include every spelling of it. If the user names a general product (e.g. "сахар"), include all its names;
  if they name a specific grade/brand/form (e.g. "сахар белый", "АИ-92", "высший сорт", "спот"), include only the names of that
  grade (all spellings). When the user's words name one registry product exactly (e.g. "стекло листовое"), pick only that product,
  not neighbouring products (e.g. not "стекло флоат"). [] if no product is mentioned. Registry names: {bases}
regions: registry regions (Latin Uzbek names) that the places in the question belong to — cities map to their region
  (e.g. Термез -> Surxondaryo viloyati, Нукус -> Qoraqalpog‘iston Respublikasi). Only from: {regions}. region_scope: warehouse (default: where goods were sold) | seller_company | buyer_company
  (when the question is about where the company is registered/located).
company_names: companies named in words, exactly as written (any script). inns: 9-digit numbers. brokers: codes like BK045.
side: seller/buyer when the question says the company/broker sold or bought; any otherwise.
date_from/date_to: ISO dates of the asked period (spelled-out years like "ikki ming yigirma to'rtinchi" = 2024; Q4 = Oct–Dec), "" if none.
years: the years if two years are compared. status: filter if asked ("состоялось"/"amalga oshgan"/"not cancelled" → not_annulled).
contract_type / contract_form / auction_type: filters if asked (when they are compared, use group_by instead). vat: for directory questions.
rate_date / rate_mode: for usd_rate questions (change = % change of the rate between the start and end of the period).
FIELD MEANINGS (from the data README) for contract_lookup / filters:
  brand (Марка товара), psize (Размер), grade (Сорт), ptype (Тип), pclass (Класс) = product attributes as written by the seller on the
  contract card (e.g. "ЦЕМ I 42,5 Н", "высший сорт"); they are NOT registered trademarks, and the data has NO registration numbers of
  trademarks, certificates, licences or standards — such questions are answerable=false.
  tnved = customs classification code of the product; deposit_pct = deposit as % of the deal amount; base_price = price per unit on the
  contract registration date; pay_term_days = days from the deal date to pay; delivery_term_days = days from the payment date to deliver;
  reg_date / valid_to = period in which deals are concluded under the contract; warehouse_address = where the goods are stored
  (not the company's office address). Only answer with a field when its meaning matches what is asked.
activity: participant activity type for directory questions. share_condition may also be region/product/delivery_basis/... :
  the numerator is that filter, the denominator is everything else (e.g. "% of bearing deals in Samarkand" → share_condition region).
volume with stat avg = average volume per deal. For "when was X cheapest/most expensive" use metric deal, deal_by price (not price_stat).
interpretation: one short sentence describing exactly what will be computed, written in the ANSWER LANGUAGE given with the question
  (ru = Russian; uz = Uzbek in Latin script).
analysis (fill FIRST, 1-3 short sentences in English): what is asked (metric), is it a ranking/breakdown (group_by + limit),
  which entities are named (product, region, company, broker, period), which side (seller/buyer), any status/form/type filter.
Every field must be present: use "none", "", [] , false or 0 when not applicable. Never invent periods or entities that are not in the question.
years: ONLY when two or more years are compared; otherwise [] and use date_from/date_to.

EXAMPLES (only the important fields shown; all others are none/empty):
Q: кто топ 3 продавца сахара в Самарканде по объёму
 -> analysis "ranking of sellers by volume, top 3, product sugar, warehouse region Samarkand", metric volume, group_by seller, order desc, limit 3,
    mentioned_products [{{"as_written":"сахара","registry_term":"сахар"}}], regions ["Samarqand viloyati"], side seller
Q: Qufil Agro 2024-yilda qancha bug'doy sotgan va qancha summaga
 -> metric amount, extra_metrics ["volume"], mentioned_products [{{"as_written":"bug'doy","registry_term":"пшеница"}}], company_names ["Qufil Agro"], side seller, date_from 2024-01-01, date_to 2024-12-31
Q: самарканд ва сирдарё - қайсисида битим кўп бўлган 2024 йилда
 -> metric count, compare region, regions ["Samarqand viloyati","Sirdaryo viloyati"], date_from 2024-01-01, date_to 2024-12-31
Q: paxta 2023 va 2024 solishtir -> metric amount, compare year, mentioned_products [{{"as_written":"paxta","registry_term":"хлопок-сырец"}}], years [2023, 2024]
Q: дизель: сколько в среднем дней от оплаты до поставки -> metric delay, stat avg, delay_of delivery_after_payment, mentioned_products [{{"as_written":"дизель","registry_term":"дизельное топливо"}}]
Q: какой процент сделок по цементу аннулирован -> metric count, share true, share_condition status, status "Аннулирован(Не оплачен)", mentioned_products [{{"as_written":"цементу","registry_term":"цемент"}}]
Q: в каком месяце 2025 года было больше всего сделок -> metric count, group_by month, order desc, limit 1, date_from 2025-01-01, date_to 2025-12-31
Q: самая крупная сделка по пшенице -> metric deal, deal_by amount, order desc, limit 1, mentioned_products [{{"as_written":"пшенице","registry_term":"пшеница"}}]
Q: кто директор компании Zoket Invest -> answerable false, missing "в данных нет сведений о руководителях компаний"
Q: сколько сделок по рису в 2024 и сколько тонн -> metric count, extra_metrics ["volume"], tonnes true, mentioned_products [{{"as_written":"рису","registry_term":"рис"}}], date 2024
Q: qancha shartnoma sement bo'yicha ro'yxatdan o'tgan -> kind contracts, metric contracts, mentioned_products [{{"as_written":"sement","registry_term":"цемент"}}]
Q: какой процент поставок по сахару был с опозданием -> metric count, share true, share_condition late, late delivered_late, mentioned_products [{{"as_written":"сахару","registry_term":"сахар"}}]
Q: сколько сделок по нефтепродуктам в 2025 -> metric count, product_category "Нефтепродукты", date 2025
Q: на сколько процентов в среднем выросла цена от стартовой на английском аукционе -> metric price_change, stat avg, auction_type "Английский аукцион"
Return JSON only.""".replace("{bases}", " | ".join(sorted(bases)))


def plan(question, products, regions, timeout=30, bases=(), lang="ru"):
    body = {"systemInstruction": {"parts": [{"text": _prompt(products, regions, bases)}]},
            "contents": [{"role": "user", "parts": [{"text": f"ANSWER LANGUAGE: {lang}\nQUESTION: {question}"}]}],
            "generationConfig": {"temperature": 0, "seed": 20261105, "responseMimeType": "application/json", "responseSchema": _schema(),
                                 "thinkingConfig": {"thinkingLevel": THINK}}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, json.dumps(body).encode(), {"content-type": "application/json", "x-goog-api-key": key()})
            t0 = time.perf_counter()
            r = json.load(urllib.request.urlopen(req, timeout=timeout))
            txt = r["candidates"][0]["content"]["parts"][0]["text"]
            plan_ = json.loads(txt)
            from . import trace as TR
            TR.step("llm_plan", model=MODEL, think=THINK, attempt=attempt + 1, llm_ms=round((time.perf_counter() - t0) * 1000),
                    usage=r.get("usageMetadata", {}), plan={k: v for k, v in plan_.items() if v not in ("", "none", [], None, False, 0)})
            return plan_
        except urllib.error.HTTPError as ex:
            msg = ex.read().decode()[:300]
            if ex.code == 400 and "thinking" in msg.lower() and "thinkingConfig" in body["generationConfig"]:
                body["generationConfig"].pop("thinkingConfig"); continue
            from . import trace as TR
            TR.step("llm_error", code=ex.code, message=msg[:200], attempt=attempt + 1)
            if ex.code in (429, 500, 503): time.sleep(1.5 * (attempt + 1)); continue
            raise RuntimeError(f"gemini {ex.code}: {msg}")
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    return None
