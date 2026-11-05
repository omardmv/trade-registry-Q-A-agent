"""Generic consistency checks and business defaults of the grammar planner."""
from app.plan.checks import DEFAULT_LIMIT, apply_defaults, consistency_error
from tests.plan_helpers import D, prepare


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


def check(plan, q=""):
    return consistency_error(prepare(plan, q), D, q)


def test_grouped_field_filtered_to_one_value_with_limit():
    msg = check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("product", "Сахар")],
                 "group_by": ["product"], "limit": 3})
    assert "one row" in msg
    assert check({"subject": "deals", "measures": [{"agg": "count"}],
                  "filters": [{"field": "product", "op": "in", "values": ["Сахар", "Пшеница"]}],
                  "group_by": ["product"], "limit": 3}) is None


def test_single_year_with_group_by_year():
    assert "one row" in check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                               "group_by": ["year"]}, "по годам 2025")
    assert check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                  "group_by": ["month"]}, "по месяцам 2025") is None


def test_distinct_of_grouped_field():
    assert "always 1" in check({"subject": "deals", "measures": [{"agg": "distinct", "of": "seller"}],
                                "group_by": ["seller"]})


def test_share_numerator_equals_scope():
    msg = check({"subject": "deals", "measures": [{"agg": "share", "where": [F("product", "Сахар")]}],
                 "filters": [F("product", "Сахар")]})
    assert "100%" in msg


def test_default_limit_for_large_grouping():
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["buyer"]})
    assert p.limit == DEFAULT_LIMIT and p.default_limit
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["contract_form"]})
    assert p.limit is None
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["month"],
                 "filters": [F("year", 2024)]})
    assert p.limit is None
    p = prepare({"subject": "deals", "filters": [F("deal", "387830")], "group_by": ["deal"]})
    assert p.limit is None and p.scope == "all"


def test_status_defaults():
    base = {"subject": "deals", "measures": [{"agg": "count"}]}
    assert prepare(base).scope == "split"
    assert prepare({**base, "group_by": ["region"]}).scope == "valid"
    assert prepare({**base, "filters": [F("status", "Исполнен")]}, "исполненные сделки").scope == "all"
    assert prepare({"subject": "deals", "measures": [{"agg": "cancelled_share"}]}).scope == "all"
    # a "without annulled" filter the question does not ask for falls back to the split view
    p = prepare({**base, "filters": [F("cancelled", False)]}, "Сколько сделок в 2024?")
    assert p.scope == "split" and p.filters == ()
    p = prepare({**base, "filters": [F("cancelled", False)]}, "Сколько сделок без аннулированных?")
    assert p.scope == "all" and p.filters[0].field == "cancelled"
    # other status filters are kept even without a status word
    p = prepare({**base, "filters": [F("status", "Новый")]}, "Сколько новых сделок?")
    assert p.scope == "all" and p.filters
    assert prepare({"subject": "contracts", "measures": [{"agg": "count"}]}).scope == ""


def test_period_named_in_question_must_be_in_plan():
    q = "Сколько сделок было в октябре 2025?"
    msg = check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)]}, q)
    assert "month/quarter" in msg
    assert check({"subject": "deals", "measures": [{"agg": "count"}],
                  "filters": [F("year", 2025), F("month", 10)]}, q) is None
    msg = check({"subject": "deals", "measures": [{"agg": "count"}]}, "Сколько сделок было 12.03.2024?")
    assert "12.03.2024" in msg
    msg = check({"subject": "deals", "measures": [{"agg": "count"}]}, "Сколько сделок в 2024 году?")
    assert "year 2024" in msg
    assert check({"subject": "deals", "measures": [{"agg": "count"}], "compare": [{"year": 2023}, {"year": 2024}]},
                 "2023 и 2024") is None
    assert check({"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)],
                  "group_by": ["quarter"]}, "В каком квартале 2025?") is None


def test_year_with_undated_subject_points_to_deals():
    msg = check({"subject": "companies", "measures": [{"agg": "count"}]}, "Сколько компаний покупали в 2024?")
    assert "subject deals" in msg


def test_apply_defaults_is_pure():
    p = prepare({"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["buyer"]})
    assert apply_defaults(p, D, "") == p


def test_record_grouping_with_overall_aggregate():
    msg = check({"subject": "deals", "measures": [{"agg": "median", "of": "deal_price_per_kg"}],
                 "filters": [F("product", "Пшеница")], "group_by": ["deal"]})
    assert "drop group_by" in msg
    assert check({"subject": "deals", "measures": [{"agg": "max", "of": "amount_uzs"}], "group_by": ["deal"],
                  "limit": 1}) is None


# ---- fresh-set fixes ----
def test_uzbek_marta_is_times_not_march():
    """«бир марта» (once), «охирги марта» (the last time), «oxirgi marta» are not March (fresh f012, f034)."""
    plan = {"subject": "deals", "measures": [{"agg": "distinct", "of": "buyer"}], "filters": [F("year", 2025)]}
    for q in ("2025 йилда мис катодни сотиб олган харидорлар сони нечта (ҳар бир компания бир марта)?",
              "2025 yilda nechta xaridor bir marta sotib olgan?",
              "2025 йилда энг охирги марта қачон харид қилган?"):
        assert check(plan, q) is None, q
    # March itself is still a month in all three languages
    for q in ("сделки 8 марта 2025", "2025 yil mart oyida nechta xaridor", "2025 йил мартда нечта харидор"):
        assert "month" in (check(plan, q) or "") or "day" in (check(plan, q) or ""), q


def test_all_statuses_word_makes_grouped_scope_all():
    """«все статусы» / «barcha holatlar» / «барча ҳолатлар» / «включая аннулированные»: every state counts also in
    breakdowns, rankings and trends (fresh f039); without such words the grouped default stays without annulled."""
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2022, "between")],
            "group_by": ["year"]}
    plan["filters"] = [{"field": "year", "op": "between", "values": [2022, 2026]}]
    for q in ("сколько сделок по годам с 2022 по 2026, все статусы", "сделки по годам включая аннулированные",
              "yillar bo‘yicha bitimlar, barcha holatlar", "hamma holatlar bilan yillar bo‘yicha bitimlar",
              "ҳар йили нечтадан битим (2022–2026, барча ҳолатлар)?", "бекор қилинганлар билан ҳар йили нечта битим"):
        assert prepare(plan, q).scope == "all", q
    assert prepare(plan, "сколько сделок по годам с 2022 по 2026").scope == "valid"
    assert prepare(plan, "сделки по годам без аннулированных").scope == "valid"
    # a "without annulled" filter the planner added is dropped when the question asks for all statuses
    p = prepare({**plan, "filters": plan["filters"] + [F("cancelled", False)]}, "по годам, все статусы")
    assert p.scope == "all" and not p.filters_on("cancelled")


def test_share_of_a_named_party_takes_the_party_as_the_part():
    """«Доля SITO‘TOM в продажах хлопка», «... paxta sotuvining necha foizini egallagan», «... сотувида неча фоиз
    улушга эга»: the seller is the numerator, the product / period the scope (fresh f029)."""
    inside = {"subject": "deals", "measures": [{"agg": "share", "of": "amount_uzs",
                                                "where": [F("product", "Хлопок-сырец")]}],
              "filters": [F("seller", "925910430"), F("year", 2024)]}
    right = {"subject": "deals", "measures": [{"agg": "share", "of": "amount_uzs",
                                               "where": [F("seller", "925910430")]}],
             "filters": [F("product", "Хлопок-сырец"), F("year", 2024)]}
    for q in ("Какая доля SITO‘TOM TEKSTIL в продажах хлопка-сырца за 2024 год?",
              "Sitotom Tekstil 2024 yilda paxta xomashyosi sotuvining necha foizini egallagan?",
              "СИТЎТОМ ТЕКСТИЛ 2024 йилда пахта хом ашёси сотувида неча фоиз улушга эга бўлган?"):
        assert "numerator:true" in (check(inside, q) or ""), q
        assert check(right, q) is None, q
    # the party's own deals as the scope, said so: «в своих продажах», «o‘z sotuvida», «ўз сотувида»
    for q in ("Какую долю в своих продажах за 2024 год SITO‘TOM TEKSTIL получила от хлопка-сырца?",
              "Sitotom Tekstil 2024 yilda o‘z sotuvida paxtaning ulushi necha foiz?",
              "СИТЎТОМ ТЕКСТИЛ 2024 йилда ўз сотувида пахтанинг улуши неча фоиз?"):
        assert check(inside, q) is None, q
    # a broker on either side as the part (r083): fine; a broker as a plain filter with another part: fix
    assert check({"subject": "deals", "measures": [{"agg": "share", "where": [F("broker", "BK111")]}],
                  "filters": [F("year", 2025)]}, "В каком проценте сделок 2025 года участвовал брокер BK111?") is None
    assert "numerator:true" in check({"subject": "deals", "measures": [{"agg": "share",
                                                                        "where": [F("product", "Сахар")]}],
                                      "filters": [F("broker", "BK111"), F("year", 2025)]},
                                     "Доля брокера BK111 в сделках с сахаром за 2025 год")


def test_time_grain_only_when_the_question_asks_per_period():
    """«харидорлар сони нечта» split by month answers another question (fresh f012); per-period, which-period,
    when and trend words, or several periods named, allow the grain."""
    by_month = {"subject": "deals", "measures": [{"agg": "distinct", "of": "buyer"}], "filters": [F("year", 2025)],
                "group_by": ["month"]}
    for q in ("Сколько разных покупателей купили медный катод в 2025 году?",
              "2025-yilda mis katodni nechta turli xaridor sotib olgan?",
              "2025 йилда мис катодни сотиб олган харидорлар сони нечта?"):
        assert "drop month" in (check(by_month, q) or ""), q
    for q in ("Сколько покупателей было в каждом месяце 2025 года?", "в каком месяце 2025 года больше всего покупателей",
              "2025 yilda oylar bo‘yicha xaridorlar soni", "2025 yilning qaysi oyida xaridorlar ko‘p bo‘lgan?",
              "2025 йилда ойма-ой харидорлар сони", "Как менялось число покупателей в 2025 году?",
              "2025 йилда харидорлар сони қандай ўзгарган?"):
        assert check(by_month, q) is None, q
    by_year = {"subject": "deals", "measures": [{"agg": "count"}], "group_by": ["year"],
               "filters": [{"field": "year", "op": "in", "values": [2024, 2025]}]}
    assert check(by_year, "Сколько сделок было в 2024 и 2025 годах?") is None
    assert "drop year" in check({**by_year, "filters": [F("product", "Сахар")]}, "Сколько всего сделок с сахаром?")
    assert check({**by_year, "filters": []}, "Сколько сделок по годам?") is None


def test_extreme_period_is_ranked_by_a_total():
    """«В какой день был самый большой оборот», «qaysi kuni eng katta savdo aylanmasi», «қайси кунида энг катта
    пуллик савдо»: the day with the largest total, not the day of the largest single deal (fresh f016)."""
    plan = {"subject": "deals", "measures": [{"agg": "max", "of": "amount_uzs"}], "filters": [F("year", 2026)],
            "group_by": ["day"], "limit": 1}
    for q in ("В какой день 2026 года на бирже был самый большой оборот в сумах?",
              "2026 yilda qaysi kuni birjada eng katta savdo aylanmasi bo‘lgan (so‘mda)?",
              "2026 йилнинг қайси кунида биржада энг катта пуллик савдо бўлган?"):
        assert "agg: sum" in (check(plan, q) or ""), q
        assert check({**plan, "measures": [{"agg": "sum", "of": "amount_uzs"}]}, q) is None, q
    # a single deal named: the largest deal of each day is what is asked
    for q in ("В какой день 2026 года была самая крупная сделка?", "2026 yilda eng katta bitim qaysi kuni bo‘lgan?",
              "2026 йилда энг катта битим қайси куни тузилган?"):
        assert check(plan, q) is None, q


def test_id_is_used_as_the_record_kind_the_question_names():
    """«100091-сонли битимнинг харидори»: 100091 is a deal, not the contract with the same number (fresh f059)."""
    as_contract = {"subject": "contracts", "filters": [F("contract", "100091")], "group_by": ["contract"]}
    as_deal = {"subject": "deals", "filters": [F("deal", "100091")], "group_by": ["deal"]}
    for q in ("Кто был покупателем по сделке № 100091?", "100091 raqamli bitimda xaridor kim bo‘lgan?",
              "100091-сонли битимнинг харидори ким?"):
        assert "as a deal" in (check(as_contract, q) or ""), q
        assert check(as_deal, q) is None, q
    for q in ("Кто продавец по контракту № 100091?", "100091-sonli shartnoma bo‘yicha sotuvchi kim?",
              "100091 рақамли шартноманинг сотувчиси ким?"):
        assert "as a contract" in (check(as_deal, q) or ""), q
        assert check(as_contract, q) is None, q
    # deals of a contract: the contract filter on deals is right
    assert check({"subject": "deals", "measures": [{"agg": "sum", "of": "amount_uzs"}],
                  "filters": [F("contract", "102267")]}, "На какую сумму в сумах заключено сделок по контракту 102267?") is None


def test_trades_outside_the_exchange_are_not_in_the_data():
    """«биржадан ташқари савдолари ҳам» (fresh f054) and the general forms in three languages."""
    from app.plan.checks import not_in_data_error
    for q in ("Выручка TUMIZO NEFT за 2025 год вместе с продажами вне биржи", "продажи помимо биржи за 2024",
              "сколько продано за пределами биржи", "внебиржевые продажи хлопка",
              "Tumizo Neftning birjadan tashqari sotuvlari bilan birga tushumi", "birjasiz savdolar qancha",
              "boshqa savdo maydonchalarida sotilgan paxta",
              "ТУМИЗО НЕФТ 2025 йилда жами қанча тушум олган, биржадан ташқари савдолари ҳам қўшиб?",
              "биржадан бошқа каналлар орқали сотувлар"):
        assert "outside exchange" in (not_in_data_error(q) or ""), q
    for q in ("Birja boshqaruvi raisi kim?", "оборот биржи за 2025 год", "2025 yilda birjada nechta bitim",
              "2025 yilgi bitimlarning necha foizida sotuvchi ham, xaridor ham bitta brokerdan foydalangan?"):
        err = not_in_data_error(q)
        assert err is None or "outside exchange" not in err and "profit" not in err, q
