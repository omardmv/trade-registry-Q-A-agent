from eval.real.run import matches


def test_iso_date_matches_other_formats():
    assert matches("rate_date", "2024-03-09", "самый дешёвый доллар был 09.03.2024")
    assert matches("rate_date", "2024-03-09", "9 марта 2024 года")
    assert matches("d", "2024-03-09", "2024-yil 9-mart")
    assert not matches("rate_date", "2024-03-09", "08.03.2024")


def test_month_and_quarter_labels():
    assert matches("month", 10, "больше всего в октябре")
    assert matches("month", 10, "oktyabr oyida")
    assert not matches("month", 10, "в сентябре")
    assert matches("quarter", 3, "III квартал — 31 659")
    assert matches("quarter", 2, "2-chorak")
    assert not matches("quarter", 2, "III квартал")


def test_numbers_still_strict():
    assert matches("deals", 17808, "17 808 сделок")
    assert not matches("deals", 17808, "17 900 сделок")


def test_common_uzbek_month_spellings():
    assert matches("month", 10, "oktabr oyida")
    assert matches("month", 9, "sentabrda")
