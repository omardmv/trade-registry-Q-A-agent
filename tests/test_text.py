from app.text import detect_lang, normalize_name, normalize_text, to_latin


def test_to_latin_uzbek_letters():
    assert to_latin("ҳарайўм кимё") == "harayom kimyo"
    assert to_latin("қопур") == "qopur"


def test_normalize_name_cyrillic_and_latin_agree():
    assert normalize_name('"ҲАРАЙЎМ КИМЁ" МЧЖ') == "harayom kimyo"
    assert normalize_name('"Pudexiz Plast" MChJ') == "pudexiz plast"
    assert normalize_name("PUBAM KIMYO MAS’ULIYATI CHEKLANGAN JAMIYATI") == "pubam kimyo"
    assert normalize_name('"Go‘qe Prokat" MChJ') == normalize_name("Go'qe prokat") == "goqe prokat"
    assert normalize_name('"ҚОПУР БРОКЕР" МЧЖ') == "qopur broker"
    assert normalize_name("RO‘QI ENERGO OK") == "roqi energo"


def test_normalize_text_keeps_all_tokens():
    assert normalize_text("Farg‘ona viloyati") == "fargona viloyati"
    assert normalize_text("Хлопок-сырец (спот)") == "xlopok sirets spot"


def test_detect_lang():
    assert detect_lang("Сколько сделок было заключено в октябре 2025 года?") == "ru"
    assert detect_lang("2025-yilda eng ko'p savdo qilgan korxona qaysi?") == "uz"
    assert detect_lang("2025 йилда энг кўп савдо қилган корхона қайси?") == "uz-Cyrl"
    assert detect_lang("Нечта битим тузилган?") == "uz-Cyrl"
