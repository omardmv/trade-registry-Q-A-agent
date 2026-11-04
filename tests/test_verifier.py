import datetime as dt

import pytest

from app.answer.numbers import extract_numbers, format_number
from app.answer.templates import NO_DATA, no_data, table_answer
from app.answer.verifier import verify_answer


def values(text):
    return [t.value for t in extract_numbers(text)]


def test_extract_numbers_formats():
    assert values("Заключено 115 763 сделки") == [115763]
    assert values("на 1,2 млрд сум") == [1.2e9]
    assert values("доля 7,29%") == [7.29]
    assert values("2.5 mln so‘m") == [2.5e6]
    assert values("в 2025 году 9 сделок") == [2025, 9]


def test_tolerance_follows_displayed_precision():
    [t] = extract_numbers("1,23 млрд")
    assert t.tolerance == pytest.approx(0.005e9)


def test_format_number():
    assert format_number(1234567.891) == "1 234 567,89"
    assert format_number(115763.0) == "115 763"


Q = "Сколько сделок было заключено в октябре 2025 года?"


def test_supported_answer_passes():
    assert verify_answer("В октябре 2025 года заключено 9 912 сделок, из них состоялось 9 300.", Q,
                         [(9912, 9300)]).ok


def test_invented_number_fails():
    r = verify_answer("Заключено 10 500 сделок.", Q, [(9912, 9300)])
    assert not r.ok and r.unsupported == ["10 500"]


def test_scaled_number_passes():
    rows = [("915688255", '"ҲАРАЙЎМ КИМЁ" МЧЖ', 1_234_567_890.0)]
    answer = "Больше всего купила «ҲАРАЙЎМ КИМЁ» (ИНН 915688255) — на 1,23 млрд сум."
    assert verify_answer(answer, "Кто купил больше всего?", rows).ok


def test_invented_inn_broker_name_date_fail():
    rows = [("915688255", "BK001", dt.date(2025, 6, 15))]
    assert not verify_answer("ИНН 999999999", "?", rows).ok
    assert not verify_answer("брокер BK002", "?", rows).ok
    assert not verify_answer("компания «Небыло»", "?", rows).ok
    assert not verify_answer("дата 16.06.2025", "?", rows).ok
    assert verify_answer("15.06.2025 брокер BK001, ИНН 915688255", "?", rows).ok


def test_template_answers_always_verify():
    rows = [("915688255", "Х", 1234567.891, dt.date(2025, 6, 15), None)]
    text = table_answer("ru", ["inn", "name", "total_uzs", "date", "x"], rows, "Расчёт: сделки за 2025 год.")
    assert verify_answer(text, "?", rows, extra_text="Расчёт: сделки за 2025 год.").ok


def test_no_data_texts():
    assert no_data("ru").startswith(NO_DATA["ru"])
    assert no_data("uz").startswith("Ma’lumotlarda bu savolga javob yo‘q.")


# ---- realistic answer shapes ----

def test_sep_contains_real_unicode_spaces():
    from app.answer import numbers
    assert numbers._SEP == "[ \u00a0\u202f]"
    assert len(numbers._SEP) == 5


@pytest.mark.parametrize("answer,rows", [
    ("Оборот составил 1 234 567,89 сум.", [(1234567.89,)]),
    ("Оборот составил 1\u00a0234\u00a0567,89 сум.", [(1234567.89,)]),
    ("Оборот составил 1\u202f234\u202f567,89 сум.", [(1234567.89,)]),
    ("Оборот около 1,23 млрд сум.", [(1_234_567_890,)]),
    ("Объём 12,5 тыс. тонн.", [(12500.0,)]),
    ("Доля составила 7,29 %.", [(7.29,)]),
    ("Доля 7,29%.", [(7.29,)]),
    ("Jami 1,2 mlrd so‘m.", [(1_234_567_890,)]),
    ("Jami 115 763 ta bitim.", [(115763,)]),
    ("Оборот 1,2 млрд", [(1_234_567_890,)]),
    ("Сумма 1 234 567 890 сум", [(1234567890,)]),
])
def test_realistic_shapes_pass(answer, rows):
    r = verify_answer(answer, "?", rows)
    assert r.ok, r.unsupported


def test_decimal_cells_pass():
    from decimal import Decimal
    assert verify_answer("Итого 1 234,50 сум", "?", [(Decimal("1234.50"),)]).ok


def test_wrong_rounding_fails():
    rows = [(1_234_567_890,)]
    assert not verify_answer("Оборот 1,3 млрд сум", "?", rows).ok
    assert not verify_answer("Оборот 1,25 млрд сум", "?", rows).ok


def test_invented_inn_in_list_fails():
    r = verify_answer("ИНН 123456789, 975923536", "?", [("975923536",)])
    assert not r.ok and r.unsupported == ["123456789"]


def test_model_computed_percent_fails():
    rows = [(50, 200)]
    assert not verify_answer("Доля составила 25,5 %", "?", rows).ok


def test_invented_scaled_and_decimal_fail():
    assert not verify_answer("Объём 99,9 тыс. тонн", "?", [(12500.0,)]).ok
    assert not verify_answer("Jami 987 654 ta bitim", "?", [(115763,)]).ok


def test_year_and_date_from_question_pass():
    q = "Сколько сделок было 2019 году и 05.03.2021?"
    assert verify_answer("В 2019 году и 05.03.2021 было 3 сделки.", q, [(3,)]).ok
    assert not verify_answer("В 2018 году было 3 сделки.", q, [(3,)]).ok


def test_date_cell_components_pass_and_iso_date():
    rows = [(dt.date(2023, 4, 17),)]
    assert verify_answer("Дата 2023-04-17, день 17, месяц 4", "?", rows).ok
    assert not verify_answer("Дата 2023-04-18", "?", rows).ok


def test_quoted_name_legal_form_and_script_variants_pass():
    rows = [('"HARAYOM KIMYO" MChJ',), ("ООО Ромашка",)]
    assert verify_answer("Покупатель «ҲАРАЙЎМ КИМЁ» МЧЖ", "?", rows).ok
    assert verify_answer("Покупатель «Harayom Kimyo» MChJ", "?", rows).ok
    assert verify_answer('Покупатель "Romashka"', "?", rows).ok
    assert not verify_answer("Покупатель «Тюльпан»", "?", rows).ok


def test_quoted_name_from_extra_text_and_question():
    assert verify_answer("«Алмаз»", "Что такое «Алмаз»?", []).ok
    assert verify_answer("«Алмаз»", "?", [], extra_text="продукт Алмаз").ok


def test_unquoted_invented_name_not_detected_by_design():
    # documented limitation: only «quoted» names are checked
    assert verify_answer("Больше всего купила компания Тюльпан.", "?", [(1,)]).ok


def test_all_numbers_in_string_cells_supported():
    rows = [("Уголь марки 2Б, 25 кг",)]
    assert verify_answer("Товар: Уголь марки 2Б, 25 кг", "?", rows).ok


# ---- review round: strict counts/years, tolerance, names, signs, ids, templates ----

@pytest.mark.parametrize("answer,question,rows,extra,ok", [
    ("Заключено 7 сделок.", "?", [(5,)], "", False),
    ("Заключено 5 сделок.", "?", [(5,)], "", True),
    ("Участвовало 10 брокеров.", "?", [(4,)], "", False),
    ("Топ-3 покупателя:\n1. «Alfa» — 100 сум\n2. «Beta» — 50 сум\n3. «Gamma» — 20 сум", "?",
     [("Alfa", 100), ("Beta", 50), ("Gamma", 20)], "", True),
    ("Первое 1-е место и 2-й и 3 место и 1-chi o‘rin", "?", [(1,)], "", True),
    ("В 2024 году заключено 5 сделок.", "Сколько сделок в 2025 году?", [(5,)], "", False),
    ("В 2024 году заключено 5 сделок.", "Сколько сделок в 2025 году?", [(5,)], "Расчёт: сделки за 2024 год.", True),
    # tolerance table
    ("Оборот 1,2 млрд", "?", [(1.234e9,)], "", True),
    ("Оборот 1 млрд", "?", [(1.4e9,)], "", False),
    ("Оборот 1 млрд", "?", [(0.6e9,)], "", False),
    ("Оборот 2 млн", "?", [(2.49e6,)], "", False),
    ("Доля 7 %", "?", [(6.6,)], "", False),
    ("Оборот 15 млрд", "?", [(15.49e9,)], "", True),
    # quoted names
    ("«Ali»", "?", [("Kalit savdo",)], "", False),
    ("«Plast Цемент»", "?", [("Plast", "Цемент")], "", False),
    ("«OOO»", "?", [("OOO",)], "", False),
    ("«Pudexiz Plast»", "?", [("Pudexiz Plast MChJ",)], "", True),
    ("«Ташкент»", "?", [("Ташкентская обл.",)], "", False),
    # signs
    ("Изменение -17 сум", "?", [(17.0,)], "", False),
    ("Изменение -17 сум", "?", [(-17.0,)], "", True),
    ("Изменение −17 сум", "?", [(-17.0,)], "", True),
    ("Изменение 17 сум", "?", [(-17.0,)], "", False),
    # ids are set members, not substrings
    ("ИНН 234567890", "?", [("1234567890123",)], "", False),
    ("ИНН 234567890", "?", [("234567890",)], "", True),
    ("ИНН 234567890", "Кто 234567890?", [], "", True),
    ("ИНН 234567890", "?", [], "ИНН 234567890", True),
    # numbers inside string cells are not allowed numbers
    ("Средняя цена 92 сум", "?", [("АИ-92",)], "", False),
    ("Бензин АИ-92", "?", [("АИ-92",)], "", True),
])
def test_review_probes(answer, question, rows, extra, ok):
    r = verify_answer(answer, question, rows, extra_text=extra)
    assert r.ok is ok, r.unsupported


def test_quote_normalizing_to_empty_reports_original():
    r = verify_answer("«OOO»", "?", [("OOO",)])
    assert r.unsupported == ["OOO"]


def test_format_number_negative_and_small():
    assert format_number(-1234.5) == "-1 234,50"
    assert format_number(-1115) == "-1 115"


def test_template_round_trip_property():
    import random
    from decimal import Decimal
    rng = random.Random(7)  # fixed seed
    explanation = "Расчёт: сделки за 2024 год, 30 kun, с 01.03.2024 по 31.03.2024."
    for trial in range(30):
        rows = []
        for i in range(rng.choice([1, 5, 25])):
            rows.append((
                rng.choice(["915688255", "BK007", 'ООО "Ромашка"', "АИ-92", "5\" труба", "12"]),
                rng.choice([-1234.5, -7, 0, 3.14159, 0.004, 1e-7, 123456789.987, Decimal("-0.5"), Decimal("12345.678")]),
                rng.choice([dt.date(2025, 6, 15), dt.datetime(2025, 6, 15, 13, 45, 59), None]),
                rng.choice([True, False, None]),
                rng.randint(-10**9, 10**12),
                rng.choice([dt.time(12, 30, 5), "Plast Цемент", None]),
            ))
        text = table_answer("ru", ["a", "b", "c", "d", "e", "f"], rows, explanation)
        r = verify_answer(text, "?", rows, extra_text=explanation)
        assert r.ok, (r.unsupported, text)
        assert "13:45" not in text


def test_template_has_no_extra_numbers_beyond_ellipsis():
    rows = [(i,) for i in range(30)]
    text = table_answer("ru", ["n"], rows)
    assert text.splitlines()[-1] == "…"
    assert verify_answer(text, "?", rows).ok


# ---- re-review: code tokens, ordinal dates, percent words, month-year, row_count, time ----

@pytest.mark.parametrize("answer,question,rows,extra,row_count,ok", [
    ("Покупатель «AGROMIR-2»", "?", [('ООО «AGROMIR-2»',)], "", None, True),
    ("Лидер — «Agromir-2» — 45,6 млн сум.", "?", [('ООО «AGROMIR-2»', 45_600_000.0)], "", None, True),
    ("1. «Agromir-2» (STIR 915688255) — 45,6 mln so‘m", "?",
     [("915688255", 'ООО «AGROMIR-2»', 45.6e6)], "", None, True),
    ("Бензин АИ-92 продан на 1 234 сум", "?", [("Бензин АИ-92 премиум", 1234)], "", None, True),
    ("Кормосмесь КС-65 — 25 тонн", "?", [("Комбикорм КС-65 гранулы", 25)], "", None, True),
    ("Товар ПЦ 400: 5 сделок", "?", [("Цемент ПЦ 400 Д20", 5)], "", None, True),
    ("Профиль 21х32мм — 5 сделок", "?", [("Профиль 21х32мм труба", 5)], "", None, True),
    ("Продукт АИ-92 — 5 сделок", "Про АИ-92?", [(5,)], "", None, True),
    ("Средняя цена 92 сум", "?", [("Бензин АИ-92 премиум",)], "", None, False),
    ("Бензин АИ-95 продан", "?", [("Бензин АИ-92 премиум",)], "", None, False),
    ("Бензин АИ-92 продан на 77 сум", "?", [("Бензин АИ-92 премиум", 1234)], "", None, False),
    ("«Alfa» 7 сделок", "?", [("Alfa", 5)], "", None, False),
    # ordinal + month / kun
    ("Сделка 17-го октября 2025 года", "?", [(dt.date(2025, 10, 15),)], "", None, False),
    ("Сделка 17-го октября 2025 года", "?", [(dt.date(2025, 10, 17),)], "", None, True),
    ("Сделка 17-го числа", "?", [(dt.date(2025, 10, 15),)], "", None, False),
    ("17-chi oktabr kuni", "?", [(dt.date(2025, 10, 15),)], "", None, False),
    ("17-chi oktabr kuni", "17 oktabr", [], "", None, True),
    ("Сделка 17-го октября 2025 года", "?", [(dt.date(2025, 10, 15),)], "Расчёт 17 октября", None, True),
    ("Участвовал 3-й брокер", "?", [("BK001",)], "", None, True),
    # percent words
    ("Доля 7 процентов", "?", [(6.6,)], "", None, False),
    ("Ulushi 7 foiz", "?", [(6.6,)], "", None, False),
    ("Доля 7 процентов", "?", [(7.0,)], "", None, True),
    # month-year
    ("- 01.2025: 8 123", "?", [(2025, 1, 8123)], "", None, True),
    ("- 01.2025: 8 123", "?", [(2025, 2, 8123)], "", None, False),
    ("- 01.2025: 8 123", "?", [(dt.date(2025, 1, 15), 8123)], "", None, True),
    ("- 01.2025: 8 123", "?", [(dt.date(2025, 2, 15), 8123)], "", None, False),
    ("- 01.2025: 8 123", "за 01.2025", [(8123,)], "", None, True),
    ("- 01.2025: 8 123", "?", [(8123,)], "период 01.2025", None, True),
    ("- 01.2025: 8 123", "?", [(8123,)], "", None, False),
    # row_count
    ("Найдено 3 покупателя", "?", [("A",), ("B",), ("C",)], "", 3, True),
    ("Найдено 3 покупателя", "?", [("A",), ("B",), ("C",)], "", None, False),
    ("Найдено 4 покупателя", "?", [("A",), ("B",), ("C",)], "", 3, False),
    # time cells
    ("Сделка в 09:05", "?", [(dt.time(9, 5, 7),)], "", None, True),
    ("Сделка в 09:06", "?", [(dt.time(9, 5, 7),)], "", None, False),
])
def test_rereview_probes(answer, question, rows, extra, row_count, ok):
    r = verify_answer(answer, question, rows, extra_text=extra, row_count=row_count)
    assert r.ok is ok, r.unsupported


# ---- third re-review: uppercase-only short codes, units, grouped numbers, letterless quotes ----

@pytest.mark.parametrize("answer,question,rows,ok", [
    ("Продано по 50 тонн", "?", [("в мешках по 50 кг",)], False),
    ("Продано АО 7 тонн", "?", [("Срок 7 дней",)], False),
    ("Продано ok 7 тонн", "?", [("Срок 7 дней",)], False),
    ("Сделки на 1 234 567 сум", "Сделки на 1 октября?", [(234567,)], False),
    ("Продано КС-65 тонн", "?", [("Комбикорм КС-65 гранулы",)], False),
    ("Доля «10 %», затем 10 % снова", "?", [("Топ-10",)], False),
    ("«Alfa 5» и Alfa 5", "?", [("Alfa 5 plus",)], False),
    ("Товар ПЦ 400: 5 сделок", "?", [("Цемент ПЦ 400 Д20", 5)], True),
    ("Объём SDR 17: 5 сделок", "?", [("Труба ПЭ 100 SDR 17", 5)], True),
    ("Бензин АИ-92 продан на 1 234 сум", "?", [("Бензин АИ-92 премиум", 1234)], True),
    ("Лидер — «Agromir-2» — 45,6 млн сум.", "?", [('ООО «AGROMIR-2»', 45_600_000.0)], True),
    ("«Alfa 5» продан", "?", [("Alfa 5 plus",)], True),
])
def test_third_rereview_probes(answer, question, rows, ok):
    r = verify_answer(answer, question, rows)
    assert r.ok is ok, r.unsupported
