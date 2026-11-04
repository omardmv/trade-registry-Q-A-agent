"""Gold eval set: each item has a question as a user would type it (loose, typos, ru/uz) and a gold SQL.
kind: exact | loose | no_data | trap.  For no_data the gold answer is 'NO DATA' (agent must say so, never invent).
Run: .venv/bin/python eval/build_eval.py  -> eval/questions.json (with gold answers computed from the DB)."""
import duckdb, json
c = duckdb.connect('data/uzex.duckdb', read_only=True)
COTTON = "product_name ILIKE 'хлопок%'"
Q = [
# ---- exact, clean
("e01","ru","exact","Сколько всего сделок было заключено в 2024 году?","select count(*) from deals where year(deal_date)=2024",""),
("e02","uz","exact","2023-yilda umumiy bitimlar summasi so'mda qancha bo'lgan?","select round(sum(amount_uzs)) from deals_enriched where year(deal_date)=2023","export deals must be converted to UZS via usd_rates"),
("e03","ru","exact","Какой брокер чаще всего выступал на стороне продавца?","select seller_broker,count(*) n from deals group by 1 order by 2 desc limit 1",""),
("e04","ru","exact","Название компании с ИНН 917899526","select \"Наименование\" from participants where \"ИНН\"='917899526'",""),
# ---- loose / incomplete (the hard part)
("l01","ru","loose","сколько хлопка продали в 2024","select round(sum(volume_units)) from deals_enriched where "+COTTON+" and year(deal_date)=2024","ambiguous metric+unit: answer volume (kg) and state assumption; also mention amount"),
("l02","uz","loose","paxta narxi o'rtacha qancha","select round(sum(amount)/sum(volume_units),2) from deals_enriched where "+COTTON+" and currency='Узбекский сум'","no period given -> overall, UZS per unit; must state it"),
("l03","ru","loose","топ 3 региона по сделкам","select region,count(*) n from deals group by 1 order by 2 desc limit 3","region = warehouse region of the seller"),
("l04","ru","loose","самая большая сделка","select deal_id,amount_uzs from deals_enriched order by amount_uzs desc limit 1","by UZS amount; USD deals converted"),
("l05","uz","loose","eng ko'p sotgan kompaniya","select p.\"Наименование\",round(sum(amount_uzs)) from deals_enriched d join participants p on p.\"ИНН\"=d.seller_inn group by 1 order by 2 desc limit 1","by amount; mention count alternative"),
("l06","ru","loose","пшеница 2025 сколько тонн","select round(sum(volume_units)) from deals_enriched where product_name ILIKE 'пшениц%' and year(deal_date)=2025 and unit='тонна'","unit is already тонна; agent must check unit consistency before summing"),
("l07","ru","loose","что с оплатой в этом году","select status,count(*) from deals where year(deal_date)=2026 group by 1 order by 2 desc","'этот год'=2026 (data ends 30.10.2026); open-ended -> status breakdown"),
("l08","ru","loose","Наманган цемент","select count(*),round(sum(amount_uzs)) from deals_enriched where region='Namangan viloyati' and product_name ILIKE '%цемент%'","two keywords only; region name is Latin-Uzbek in data, user wrote Russian"),
# ---- traps from data quirks
("t01","ru","trap","Сколько сделок по мукe пшеничной? (учти что в реестре пишут по-разному)","select count(*) from deals where product_name ILIKE 'мук%пшенич%'","name variants: МУКА ПШЕНИЧНАЯ / Мука пшеничная / with attributes"),
("t02","ru","trap","Сколько сделок аннулировано из-за неоплаты в 2025?","select count(*) from deals where status='Аннулирован(Не оплачен)' and year(deal_date)=2025",""),
("t03","ru","trap","Общая сумма экспортных сделок в долларах","select round(sum(amount),2) from deals_enriched where currency='Доллар США'","do NOT mix with UZS amounts"),
("t04","ru","trap","Сколько сделок состоялось в 2024?","select count(*) from deals where year(deal_date)=2024 and status<>'Аннулирован(Не оплачен)'","'состоялось' excludes annulled; state the definition"),
("t05","uz","trap","Q4 2025 da qancha bitim bo'lgan","select count(*) from deals where deal_date between '2025-10-01' and '2025-12-31'","Q4 notation"),
("t06","ru","trap","Какая средняя цена за килограмм хлопка-сырца в сумах за март 2023","select round(sum(amount)/sum(volume_units),2) from deals_enriched where "+COTTON+" and unit='килограмм' and currency='Узбекский сум' and deal_date between '2023-03-01' and '2023-03-31'",""),
("t07","ru","trap","Кто крупнейший покупатель в Ташкенте?","select p.\"Наименование\",round(sum(amount_uzs)) from deals_enriched d join participants p on p.\"ИНН\"=d.buyer_inn where p.\"Регион\"='Toshkent shahri' group by 1 order by 2 desc limit 1","'в Ташкенте' = buyer's region (could also mean city vs viloyat): state interpretation"),
("t08","ru","trap","Сколько лотов недопоставлено по частично исполненным?","select round(sum(qty_lots-delivered_lots)) from deals where status='Исполнен(Частично)'",""),
("t09","ru","trap","курс доллара на 15 марта 2024","select uzs_per_usd from usd_rates where rate_date='2024-03-15'",""),
("t10","ru","trap","Есть ли сделки где продавец и покупатель одна компания?","select count(*) from deals where seller_inn=buyer_inn","answer is 0 -> say 'no', with the check shown"),
# ---- no data: must say so, never invent
("n01","ru","no_data","Сколько сделок по золоту было в 2024?","select count(*) from deals where product_name ILIKE '%золот%'","0 rows -> say product not in registry (don't say '0 trades' as if золото were a valid listed product? either is OK if clear)"),
("n02","ru","no_data","Сколько сделок было в ноябре 2026?","select count(*) from deals where deal_date>='2026-11-01'","data ends 30.10.2026"),
("n03","ru","no_data","Назови компанию с ИНН 123456789","select \"Наименование\" from participants where \"ИНН\"='123456789'","STIR not in data"),
("n04","uz","no_data","BK999 broker nechta bitim qilgan?","select count(*) from deals where seller_broker='BK999' or buyer_broker='BK999'","broker doesn't exist"),
("n05","ru","no_data","Какой прогноз цены на хлопок на 2027 год?","select 1 where false","forecast not in data: refuse (could offer historical trend)"),
("n06","ru","no_data","Кто директор компании ИНН 917899526?","select 1 where false","field doesn't exist: must not invent a person"),
("n07","ru","no_data","Сколько сделок в 2021 году?","select count(*) from deals where year(deal_date)=2021","data starts 03.01.2022"),
("n08","uz","no_data","Mahsulot narxi dollarda necha foiz o'sdi 2019 dan 2020 ga?","select 1 where false","years outside range"),
]
out=[]
for id_,lang,kind,q,sql,note in Q:
    rows=c.execute(sql).fetchall()
    out.append(dict(id=id_,lang=lang,kind=kind,question=q,gold_sql=sql,gold=rows[:5],note=note))
json.dump(out,open('eval/questions.json','w'),ensure_ascii=False,indent=1,default=str)
for o in out: print(o['id'],o['kind'],'|',o['question'][:55],'->',o['gold'][:2])
