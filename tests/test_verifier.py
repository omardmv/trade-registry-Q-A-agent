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
