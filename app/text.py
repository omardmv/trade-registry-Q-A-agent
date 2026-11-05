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


_LAT_TO_CYR_2 = {"o‘": "ў", "o'": "ў", "oʻ": "ў", "o’": "ў", "g‘": "ғ", "g'": "ғ", "gʻ": "ғ", "g’": "ғ",
                 "sh": "ш", "ch": "ч", "ya": "я", "yo": "ё", "yu": "ю", "ye": "е"}
_LAT_TO_CYR_1 = dict(zip("abdefghijklmnopqrstuvxyz", "абдэфгҳижклмнопқрстувхйз"))


def to_cyrillic(text: str) -> str:
    """Uzbek Latin -> Uzbek Cyrillic, for fixed template words (not for data values)."""
    out, i = [], 0
    while i < len(text):
        pair = text[i:i + 2]
        low = pair.lower()
        if low in _LAT_TO_CYR_2:
            cyr = _LAT_TO_CYR_2[low]
            out.append(cyr.upper() if pair[0].isupper() else cyr)
            i += 2
            continue
        ch = text[i]
        lo = ch.lower()
        if lo == "e":   # э at the start of a word, е inside
            cyr = "э" if i == 0 or not text[i - 1].isalpha() else "е"
        elif lo in _LAT_TO_CYR_1:
            cyr = _LAT_TO_CYR_1[lo]
        elif ch in "‘’ʻ'":
            cyr = "ъ" if i > 0 and text[i - 1].isalpha() and i + 1 < len(text) and text[i + 1].isalpha() else ch
        else:
            cyr = ch
        out.append(cyr.upper() if ch.isupper() else cyr)
        i += 1
    return "".join(out)


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
