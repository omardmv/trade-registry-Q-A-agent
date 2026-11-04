"""Held-out questions, written BEFORE the templates were built. Don't tune templates to these."""
import duckdb, json
c = duckdb.connect('data/uzex.duckdb', read_only=True)
B = 'contract_id in (select "Номер контракта"::BIGINT from contracts where lower("Наименование продукта") similar to {})'
Q = [
("h01","ru","exact","Сколько сделок по рису в 2023?","select count(*) from deals where year(deal_date)=2023 and "+B.format("'рис.*'"),""),
("h02","uz","exact","2025-yilda Samarqand viloyatida nechta bitim tuzilgan?","select count(*) from deals where year(deal_date)=2025 and region='Samarqand viloyati'",""),
("h03","ru","exact","Общая сумма сделок, где BK005 был брокером продавца","select round(sum(amount_uzs)) from deals_enriched where seller_broker='BK005'",""),
("h04","ru","loose","топ-5 покупателей по сумме 2024","select p.\"Наименование\",round(sum(amount_uzs)) s from deals_enriched d join participants p on p.\"ИНН\"=d.buyer_inn where year(deal_date)=2024 group by 1 order by 2 desc limit 5",""),
("h05","ru","loose","средняя цена портландцемента 2025","select round(sum(amount)/sum(volume_units),2) from deals_enriched where year(deal_date)=2025 and currency='Узбекский сум' and "+B.format("'(портландцемент|цемент портланд).*'"),"per unit (тонна)"),
("h06","uz","exact","2024 yil eksport bitimlari soni","select count(*) from deals where year(deal_date)=2024 and contract_type='Экспортный'",""),
("h07","ru","loose","сколько аннулировано в Бухаре","select count(*) from deals where status='Аннулирован(Не оплачен)' and region='Buxoro viloyati'",""),
("h08","ru","exact","Какой брокер чаще всего был на стороне покупателя в 2026?","select buyer_broker,count(*) from deals where year(deal_date)=2026 group by 1 order by 2 desc limit 1",""),
("h09","ru","exact","курс доллара 1 января 2025","select uzs_per_usd from usd_rates where rate_date='2025-01-01'",""),
("h10","ru","exact","Как называется брокер BK010?","select \"Наименование\" from brokers where \"Код брокера\"='BK010'",""),
("h11","uz","loose","937937230 STIR kompaniya sotuvchi sifatida qancha savdo qilgan","select round(sum(amount_uzs)) from deals_enriched where seller_inn='937937230'","as seller, amount in UZS"),
("h12","ru","trap","Сколько тонн хлопкового масла продано в 2022?","select round(sum(volume_units)) from deals_enriched where year(deal_date)=2022 and unit='тонна' and "+B.format("'(масло хлопков|хлопковое масло).*'"),"must not match хлопок-сырец"),
("h13","ru","no_data","Сколько сделок было в декабре 2026?","select count(*) from deals where deal_date>='2026-12-01'","data ends 30.10.2026"),
("h14","uz","no_data","BK500 broker nomi nima?","select \"Наименование\" from brokers where \"Код брокера\"='BK500'",""),
("h15","ru","no_data","Какой номер телефона у компании 917899526?","select 1 where false","no phone field"),
("h16","ru","loose","самый активный регион в 2022","select region,count(*) from deals where year(deal_date)=2022 group by 1 order by 2 desc limit 1",""),
("h17","ru","exact","Количество форвардных сделок","select count(*) from deals where contract_form='Форвард'",""),
("h18","uz","exact","2024-yil mart oyida nechta bitim bo'lgan","select count(*) from deals where deal_date between '2024-03-01' and '2024-03-31'",""),
("h19","ru","trap","сумма сделок по пшенице за 2 квартал 2024","select round(sum(amount_uzs)) from deals_enriched where deal_date between '2024-04-01' and '2024-06-30' and "+B.format("'пшениц.*'"),"wheat, not flour"),
("h20","ru","no_data","Сколько сделок по кофе?","select count(*) from deals where product_name ILIKE '%коф%'","product not traded"),
("h21","uz","loose","shakar 2025 qancha sotilgan","select round(sum(volume_units)) from deals_enriched where year(deal_date)=2025 and "+B.format("'сахар.*'"),"volume; state unit"),
("h22","ru","trap","Сколько сделок исполнено частично в 2023?","select count(*) from deals where status='Исполнен(Частично)' and year(deal_date)=2023",""),
]
out=[]
for id_,lang,kind,q,sql,note in Q:
    rows=c.execute(sql).fetchall()
    out.append(dict(id=id_,lang=lang,kind=kind,question=q,gold_sql=sql,gold=rows[:5],note=note))
json.dump(out,open('eval/holdout.json','w'),ensure_ascii=False,indent=1,default=str)
for o in out: print(o['id'],o['kind'],'|',o['question'][:50],'->',o['gold'][:2])
