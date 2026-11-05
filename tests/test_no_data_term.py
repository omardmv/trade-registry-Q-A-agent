"""The no-data text names the word the data has nothing about (closed vocabulary)."""
from app.answer import templates
from app.text import to_cyrillic


def test_no_data_names_the_term_in_three_languages():
    ru = templates.no_data("ru", "евро")
    assert ru.startswith("В данных нет сведений о «евро».") and templates.COVERAGE["ru"] in ru
    assert templates.no_data("ru", "акциях").startswith("В данных нет сведений об «акциях».")
    uz = templates.no_data("uz", "yevro")
    assert uz.startswith("Ma’lumotlarda «yevro» haqida ma’lumot yo‘q.") and templates.COVERAGE["uz"] in uz
    cy = templates.no_data("uz-Cyrl", "евро")
    assert cy.startswith("Маълумотларда «евро» ҳақида маълумот йўқ.") and templates.COVERAGE["uz-Cyrl"] in cy
    assert templates.no_data("ru") == f"{templates.NO_DATA['ru']} {templates.COVERAGE['ru']}"


def test_to_cyrillic_yo_before_o_apostrophe():
    assert to_cyrillic("yo‘q") == "йўқ" and to_cyrillic("Yo‘l") == "Йўл" and to_cyrillic("yoz") == "ёз"
