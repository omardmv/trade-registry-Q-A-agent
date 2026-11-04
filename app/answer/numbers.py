"""Extract and format numbers, IDs, dates and quoted names for answer verification."""
import datetime as dt
import math
import re
from dataclasses import dataclass
from decimal import Decimal

_SEP = "[   ]"  # space, no-break space (U+00A0), narrow no-break space (U+202F)
_SCALES = [
    (r"трлн\w*|trln|trillion\w*", 1e12),
    (r"млрд\w*|миллиард\w*|mlrd|milliard\w*", 1e9),
    (r"млн\w*|миллион\w*|mln|million\w*", 1e6),
    (r"тыс\w*|ming", 1e3),
]
_SCALE_RE = "|".join(p for p, _ in _SCALES)
NUMBER_RE = re.compile(
    rf"(?:(?<![\w.,])(?P<sign>[-\u2212]))?(?<![\w.,])(?P<int>\d{{1,3}}(?:{_SEP}\d{{3}})+|\d+)"
    rf"(?:[.,](?P<dec>\d+))?(?:{_SEP}?(?P<scale>{_SCALE_RE})\.?)?", re.I)
_PERCENT_AFTER = re.compile(r"\s*(?:%|процент\w*|foiz|фоиз\w*)", re.I)
# an ordinal before a month name / "число" / "kun" is a date day, not structure: it must be supported
_DATE_WORD = (r"(?!\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|числ"
              r"|yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sent[yа]?abr|okt[yа]?abr|noyabr|dekabr|kun)"
              r"\w*)")
# structural numbers (list markers, ranks, ordinals) are not facts
_STRUCTURAL_RES = [
    re.compile(r"^[ \t]*\d{1,2}[.)]\s", re.M),
    re.compile(r"(?:топ|top)-?\d+", re.I),
    re.compile(r"\b\d{1,2}-(?:е|й|я|го|му|м|ое|ый)\b" + _DATE_WORD, re.I),
    re.compile(r"\b\d{1,2}-(?:chi|o‘rin|o'rin)" + _DATE_WORD, re.I),
    re.compile(r"\b\d{1,2}\s+(?:место|o‘rin|o'rin)", re.I),
]
# product / grade codes: АИ-92, AGROMIR-2, КС-65, 21х32мм, "ПЦ 400"
CODE_RE = re.compile(
    r"(?<!\w)(?:[^\W\d_]+[-\u2010]?\d[\w,.хx]*|\d+[хx]\d+\w*|[^\W\d_]{1,4}[ \u00a0\u202f]\d+)(?![\w])", re.I)
MONTH_YEAR_RE = re.compile(r"(?<![\d.])(0[1-9]|1[0-2])\.(20\d\d)\b(?!\.\d)")
DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b|\b\d{4}-\d{2}-\d{2}\b")
ID_RE = re.compile(r"(?<!\d)\d{9}(?!\d)|\bBK\d{3}\b", re.I)
QUOTED_RE = re.compile(r"[«“\"]([^»”\"]{2,80})[»”\"]")


@dataclass
class NumberToken:
    text: str
    value: float
    tolerance: float


def _scale(word: str | None) -> float:
    if not word:
        return 1.0
    for pattern, factor in _SCALES:
        if re.fullmatch(pattern, word, re.I):
            return factor
    return 1.0


def strip_structural(text: str) -> str:
    for rx in _STRUCTURAL_RES:
        text = rx.sub(" ", text)
    return text


def strip_dates_and_ids(text: str) -> str:
    return ID_RE.sub(" ", DATE_RE.sub(" ", text))


def extract_numbers(text: str) -> list[NumberToken]:
    tokens = []
    clean = strip_dates_and_ids(text)
    for m in NUMBER_RE.finditer(clean):
        integer = re.sub(_SEP, "", m.group("int"))
        decimals = m.group("dec") or ""
        scale = _scale(m.group("scale"))
        value = float(f"{integer}.{decimals}" if decimals else integer) * scale
        if m.group("sign"):
            value = -value
        tol = 0.5 * 10 ** (-len(decimals)) * scale
        if decimals or m.group("scale") or _PERCENT_AFTER.match(clean, m.end()):
            tol = min(tol, 0.05 * abs(value))
        tokens.append(NumberToken(m.group(0).strip(), value, tol))
    return tokens


def extract_ids(text: str) -> list[str]:
    return [m.group(0).upper() for m in ID_RE.finditer(DATE_RE.sub(" ", text))]


def extract_dates(text: str) -> list[str]:
    return DATE_RE.findall(text)


def extract_quoted(text: str) -> list[str]:
    return [m.group(1).strip() for m in QUOTED_RE.finditer(text)]


def format_number(v) -> str:
    v = float(v)
    if not math.isfinite(v):
        return str(v)
    if v.is_integer():
        return f"{int(v):,}".replace(",", " ")
    decimals = 2
    if abs(round(v, 2) - v) > 0.01 * abs(v):  # tiny values: keep >= 3 significant digits
        decimals = min(20, max(2, 3 - math.floor(math.log10(abs(v)))))
    return f"{v:,.{decimals}f}".replace(",", " ").replace(".", ",")


def format_cell(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (int, float, Decimal)):
        return format_number(v)
    if isinstance(v, dt.datetime):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, dt.date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, dt.time):
        return v.strftime("%H:%M:%S")
    return str(v)
