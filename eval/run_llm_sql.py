import json, re, sys, time, urllib.request, duckdb
sys.path.insert(0, "eval")
MODEL = sys.argv[1]; HINTS = sys.argv[2] == "hints"; OUT = sys.argv[3]
import importlib.util
src = open("eval/score_entities.py").read().split("t0=time.perf_counter()")[0]
ns = {}; exec(src, ns); rules = ns["rules"]
qs = json.load(open("eval/questions.json"))
con = duckdb.connect("data/uzex.duckdb", read_only=True)
SCHEMA = """DuckDB view deals_enriched (one row per trade; 2022-01-03..2026-10-30):
 deal_id, deal_date DATE, auction_type, qty_lots, amount (in contract currency), start_price, contract_id,
 product_name (full name, many spelling variants: use ILIKE), lot_size, unit (тонна/килограмм/литр/штука/куб.метр/кв.метр/метр),
 region (seller warehouse region, Latin Uzbek e.g. 'Namangan viloyati','Toshkent shahri'), contract_type (Внутренний/Экспортный),
 contract_form (Спот/Форвард/Фьючерс),
 status (Исполнен, Исполнен(Частично), Оплачен, Ждёт оплату, Заключен, Не подписан, Новый, 'Аннулирован(Не оплачен)'),
 seller_inn, buyer_inn, seller_broker, buyer_broker (codes BK001..BK120), paid_date, delivery_date, delivered_lots,
 volume_units (=qty_lots*lot_size), price_per_unit, currency ('Узбекский сум' or 'Доллар США'), amount_uzs (amount converted to UZS).
Other tables: participants("ИНН","Наименование","Регион","Вид деятельности","Роль"), brokers("Код брокера","Наименование","ИНН","Регион"), usd_rates(rate_date, uzs_per_usd).
Rules: use amount_uzs for money totals; sum volume_units only within one unit; one SQL query only; if the question asks for something the tables cannot contain, output exactly: SELECT 'NO_DATA'."""
def ask(msgs):
    body = json.dumps({"model": MODEL, "messages": msgs, "stream": False, "options": {"temperature": 0, "num_predict": 220}}).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/chat", body, {"content-type":"application/json"}), timeout=300))
    return r["message"]["content"]
def clean(t):
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S); t = m.group(1) if m else t
    return t.strip().rstrip(";")
def run(sql):
    return con.execute(sql).fetchall()[:5]
def flat(rows): return [x for r in rows for x in r]
def same(gold, got):
    gv = flat(gold); ov = flat(got)
    if not gv: return False
    for g in gv:
        if not any((isinstance(g,(int,float)) and isinstance(o,(int,float)) and abs(g-o)<=max(1e-6,abs(g)*0.005)) or (isinstance(g,str) and str(o)==g) for o in ov): return False
    return True
res = []
for q in qs:
    user = q["question"]
    if HINTS:
        e = rules(user); h = []
        if e["region"]: h.append("region = " + ", ".join(sorted(e["region"])))
        if e["product"]: h.append("product_name ILIKE pattern(s): " + ", ".join(f"'{p}%'" if p!='мука' else "'мук%'" for p in sorted(e["product"])))
        if e["inn"]: h.append("INN: " + ", ".join(e["inn"]))
        if e["broker"]: h.append("broker: " + ", ".join(e["broker"]))
        if e["year"]: h.append("year(s): " + ", ".join(sorted(e["year"])))
        if h: user += "\n[verified entities: " + "; ".join(h) + "]"
    msgs = [{"role":"system","content":SCHEMA+"\nAnswer with SQL only."},{"role":"user","content":user}]
    t = time.perf_counter(); sql = clean(ask(msgs)); err = None
    try: got = run(sql)
    except Exception as ex:
        err = str(ex)[:200]
        msgs += [{"role":"assistant","content":sql},{"role":"user","content":f"Error: {err}. Fix the SQL, output SQL only."}]
        sql = clean(ask(msgs))
        try: got = run(sql); err = None
        except Exception as ex2: got = []; err = str(ex2)[:200]
    ms = (time.perf_counter()-t)*1000
    said_nodata = got and "NO_DATA" in [str(x) for x in flat(got)]
    empty = (not got) or all(v is None for v in flat(got)) or said_nodata
    gold = [tuple(r) for r in q["gold"]]
    if q["kind"] == "no_data":
        gz = (not gold) or all(v in (0,None) for v in flat(gold))
        ok = empty or (gz and flat(got)[:1]==[0]) or (not empty and False)
        halluc = not ok
    else:
        ok = bool(got) and not said_nodata and same(gold, got); halluc = False
    res.append(dict(id=q["id"],kind=q["kind"],ok=bool(ok),halluc=halluc,ms=ms,err=err,sql=sql,got=[list(r) for r in got]))
    print(q["id"], "OK " if ok else "BAD", f"{ms/1000:.1f}s", sql.replace("\n"," ")[:110], flush=True)
json.dump(res, open(OUT,"w"), ensure_ascii=False, default=str, indent=1)
for k in ("exact","loose","trap","no_data"):
    r=[x for x in res if x["kind"]==k]; print(k, sum(x["ok"] for x in r), "/", len(r))
print("total", sum(x["ok"] for x in res), "/", len(res), "| hallucinated no_data:", sum(x["halluc"] for x in res), "| median s", sorted(x["ms"] for x in res)[len(res)//2]/1000)
