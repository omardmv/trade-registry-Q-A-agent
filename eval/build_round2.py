"""Round 2: written after the templates, run once without edits first. Phrasings deliberately different."""
import duckdb, json
c = duckdb.connect('data/uzex.duckdb', read_only=True)
B = "product_base SIMILAR TO '{}'"
Q = [
("r01","ru","loose","а сколько всего денег прошло через биржу за 2022","select round(sum(amount_uzs)) from deals_enriched where year(deal_date)=2022",""),
("r02","uz","loose","andijonda 2024 da qancha bitim","select count(*) from deals where region='Andijon viloyati' and year(deal_date)=2024",""),
("r03","ru","loose","кто больше всех купил бензина","select p.\"Наименование\",round(sum(amount_uzs)) from deals_enriched d join participants p on p.\"ИНН\"=d.buyer_inn where "+B.format('бензин.*')+" group by 1 order by 2 desc limit 1","by amount"),
("r04","ru","exact","Средняя цена сахара в 2023 году?","select round(sum(amount)/sum(volume_units),2) from deals_enriched where currency='Узбекский сум' and year(deal_date)=2023 and "+B.format('сахар.*'),""),
("r05","ru","loose","экспорт хлопка сколько долларов","select round(sum(amount),2) from deals_enriched where currency='Доллар США' and "+B.format('хлопок.?сырец.*'),""),
("r06","uz","exact","Qaysi viloyatda eng ko'p bitim tuzilgan?","select region,count(*) from deals group by 1 order by 2 desc limit 1",""),
("r07","ru","loose","мука 2026 тонны","select round(sum(volume_units)) from deals_enriched where year(deal_date)=2026 and "+B.format('мука.*'),""),
("r08","ru","exact","Сколько сделок провёл брокер BK042 в 2025 году?","select count(*) from deals where year(deal_date)=2025 and (seller_broker='BK042' or buyer_broker='BK042')","either side"),
("r09","ru","trap","Сколько сделок по обратному аукциону в 2024?","select count(*) from deals where auction_type='Обратный аукцион' and year(deal_date)=2024",""),
("r10","ru","no_data","Сколько стоит акция биржи?","select 1 where false","not in data"),
("r11","uz","no_data","2030 yilda nechta bitim bo'ladi?","select 1 where false","future"),
("r12","ru","loose","средний чек сделки в 2025","select round(avg(amount_uzs)) from deals_enriched where year(deal_date)=2025","average deal size"),
("r13","ru","trap","Сколько дней в среднем занимает поставка после оплаты?","select round(avg(delivery_date-paid_date),2) from deals where delivery_date is not null and paid_date is not null",""),
("r14","ru","loose","доля аннулированных сделок 2025","select round(100.0*count(*) filter(where status='Аннулирован(Не оплачен)')/count(*),2) from deals where year(deal_date)=2025",""),
("r15","ru","exact","Какая компания с ИНН 925910430 и сколько она продала?","select round(sum(amount_uzs)) from deals_enriched where seller_inn='925910430'","name + amount as seller"),
("r16","uz","loose","paxta 2023 va 2024 solishtir","select count(*) filter(where year(deal_date)=2023), count(*) filter(where year(deal_date)=2024) from deals_enriched where "+B.format('хлопок.?сырец.*'),"comparison; count or volume both acceptable"),
]
out=[]
for id_,lang,kind,q,sql,note in Q:
    rows=c.execute(sql).fetchall()
    out.append(dict(id=id_,lang=lang,kind=kind,question=q,gold_sql=sql,gold=rows[:5],note=note))
json.dump(out,open('eval/round2.json','w'),ensure_ascii=False,indent=1,default=str)
