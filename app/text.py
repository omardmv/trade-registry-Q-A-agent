"""Text normalization shared by ingest, value resolution and verification."""
import re
from typing import Literal

_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ь": "", "ы": "i", "э": "e", "ю": "yu",
    "я": "ya", "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h",
}
_APOSTROPHES = "‘’ʻʼ'`´"
_LEGAL_FORMS = {
    "mchj", "ooo", "aj", "ao", "ok", "xk", "qk", "chp", "yatt",
    "masuliyati", "cheklangan", "jamiyati",
}
_UZ_CYRL_LETTERS = set("ўқғҳ")
_UZ_CYRL_WORDS = re.compile(r"\b(нечта|қанча|қайси|йил\w*|битим\w*|ким|бўл\w*|канча|кайси)\b", re.I)


def to_latin(text: str) -> str:
    """Lower-case and transliterate Cyrillic (Russian + Uzbek) to Uzbek Latin."""
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text.lower())


def normalize_text(text: str) -> str:
    """Latin, lower case, no apostrophes, punctuation collapsed to single spaces."""
    t = to_latin(text)
    for a in _APOSTROPHES:
        t = t.replace(a, "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def normalize_name(text: str) -> str:
    """normalize_text without legal-form tokens (MChJ, МЧЖ, AJ, ...)."""
    return " ".join(w for w in normalize_text(text).split() if w not in _LEGAL_FORMS)


def detect_lang(text: str) -> Literal["ru", "uz", "uz-Cyrl"]:
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cyr > lat:
        low = text.lower()
        if any(ch in _UZ_CYRL_LETTERS for ch in low) or _UZ_CYRL_WORDS.search(low):
            return "uz-Cyrl"
        return "ru"
    return "uz"
