"""Fixed texts and the row-based fallback answer (always verifiable by construction)."""
from app.answer.numbers import format_cell

NO_DATA = {
    "ru": "В данных нет ответа на этот вопрос.",
    "uz": "Ma’lumotlarda bu savolga javob yo‘q.",
    "uz-Cyrl": "Маълумотларда бу саволга жавоб йўқ.",
}
COVERAGE = {
    "ru": "Данные охватывают сделки биржи с 03.01.2022 по 30.10.2026: сделки, контракты, участники, брокеры "
          "и курс доллара.",
    "uz": "Ma’lumotlar 03.01.2022 dan 30.10.2026 gacha bo‘lgan birja bitimlarini qamrab oladi: bitimlar, "
          "shartnomalar, ishtirokchilar, brokerlar va dollar kursi.",
    "uz-Cyrl": "Маълумотлар 03.01.2022 дан 30.10.2026 гача бўлган биржа битимларини қамраб олади: битимлар, "
               "шартномалар, иштирокчилар, брокерлар ва доллар курси.",
}
FAILED = {
    "ru": "Не удалось надёжно ответить на этот вопрос по данным. Попробуйте переформулировать вопрос.",
    "uz": "Bu savolga ma’lumotlar asosida ishonchli javob berib bo‘lmadi. Savolni boshqacha ifodalab ko‘ring.",
    "uz-Cyrl": "Бу саволга маълумотлар асосида ишончли жавоб бериб бўлмади. Саволни бошқача ифодалаб кўринг.",
}
TIMEOUT = {
    "ru": "Не удалось ответить за отведённое время. Попробуйте задать вопрос проще.",
    "uz": "Belgilangan vaqt ichida javob berib bo‘lmadi. Savolni soddaroq berib ko‘ring.",
    "uz-Cyrl": "Белгиланган вақт ичида жавоб бериб бўлмади. Саволни соддароқ бериб кўринг.",
}
RESULT_HEADER = {"ru": "Результат запроса:", "uz": "So‘rov natijasi:", "uz-Cyrl": "Сўров натижаси:"}
PARTIAL = {
    "ru": "Результат запроса неполный: показаны не все строки.",
    "uz": "So‘rov natijasi to‘liq emas: barcha qatorlar ko‘rsatilmagan.",
    "uz-Cyrl": "Сўров натижаси тўлиқ эмас: барча қаторлар кўрсатилмаган.",
}


def _lang(lang: str) -> str:
    return lang if lang in NO_DATA else "ru"


def no_data(lang: str) -> str:
    lang = _lang(lang)
    return f"{NO_DATA[lang]} {COVERAGE[lang]}"


def failed(lang: str) -> str:
    return FAILED[_lang(lang)]


def timeout(lang: str) -> str:
    return TIMEOUT[_lang(lang)]


def partial_note(lang: str) -> str:
    return PARTIAL[_lang(lang)]


def table_answer(lang: str, columns: list[str], rows: list[tuple], explanation: str = "") -> str:
    lines = [RESULT_HEADER[_lang(lang)]]
    for row in rows[:20]:
        lines.append("; ".join(f"{c}: {format_cell(v)}" for c, v in zip(columns, row)))
    if len(rows) > 20:
        lines.append("…")
    if explanation:
        lines.append(explanation)
    return "\n".join(lines)
