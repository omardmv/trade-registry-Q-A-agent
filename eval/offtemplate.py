import sys; sys.path.insert(0,'.')
from agent.core import answer, q
Q = [("В каком месяце 2024 года было больше всего сделок?","select strftime(deal_date,'%m'),count(*) from deals where year(deal_date)=2024 group by 1 order by 2 desc limit 1"),
 ("Сколько уникальных покупателей было в 2025?","select count(distinct buyer_inn) from deals where year(deal_date)=2025"),
 ("Какова доля экспортных сделок в общем количестве?","select round(100.0*count(*) filter(where contract_type='Экспортный')/count(*),2) from deals"),
 ("Средний срок между сделкой и оплатой в днях","select round(avg(paid_date-deal_date),2) from deals where paid_date is not null"),
 ("2025 yilda nechta kompaniya sotuvchi bo'lgan?","select count(distinct seller_inn) from deals where year(deal_date)=2025"),
 ("На сколько процентов выросло число сделок в 2025 по сравнению с 2024?","select round(100.0*(count(*) filter(where year(deal_date)=2025))/(count(*) filter(where year(deal_date)=2024))-100,2) from deals")]
for text, gold in Q:
    a = answer(text)
    print(f"\n{text}\n  gold={q(gold)} | route={a.route} conf={a.confidence} {a.ms:.0f}ms\n  {a.text[:200]}\n  sql: {a.sql[:200]}")
