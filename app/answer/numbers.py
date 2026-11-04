"""Extract and format numbers, IDs, dates and quoted names for answer verification."""
import datetime as dt
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
    rf"(?<![\w.,])(\d{{1,3}}(?:{_SEP}\d{{3}})+|\d+)(?:[.,](\d+))?(?:{_SEP}?({_SCALE_RE})\.?)?", re.I)
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


def strip_dates_and_ids(text: str) -> str:
    return ID_RE.sub(" ", DATE_RE.sub(" ", text))


def extract_numbers(text: str) -> list[NumberToken]:
    tokens = []
    for m in NUMBER_RE.finditer(strip_dates_and_ids(text)):
        integer = re.sub(_SEP, "", m.group(1))
        decimals = m.group(2) or ""
        scale = _scale(m.group(3))
        value = float(f"{integer}.{decimals}" if decimals else integer) * scale
        tokens.append(NumberToken(m.group(0).strip(), value, 0.5 * 10 ** (-len(decimals)) * scale))
    return tokens


def extract_ids(text: str) -> list[str]:
    return [m.group(0).upper() for m in ID_RE.finditer(DATE_RE.sub(" ", text))]


def extract_dates(text: str) -> list[str]:
    return DATE_RE.findall(text)


def extract_quoted(text: str) -> list[str]:
    return [m.group(1).strip() for m in QUOTED_RE.finditer(text)]


def format_number(v) -> str:
    v = float(v)
    if v.is_integer():
        return f"{int(v):,}".replace(",", " ")
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


def format_cell(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (int, float, Decimal)):
        return format_number(v)
    if isinstance(v, dt.datetime):
        return v.strftime("%d.%m.%Y %H:%M:%S")
    if isinstance(v, dt.date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, dt.time):
        return v.strftime("%H:%M:%S")
    return str(v)
