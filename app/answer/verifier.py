"""Deterministic check that every fact in an answer comes from the query result or the question."""
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from app.answer.numbers import extract_dates, extract_ids, extract_numbers, extract_quoted
from app.text import normalize_name

_ALWAYS_NUMBERS = list(range(0, 11)) + [2022, 2023, 2024, 2025, 2026]
_ALWAYS_DATES = {"03.01.2022", "30.10.2026", "01.01.2022", "31.10.2026"}


@dataclass
class Verification:
    ok: bool
    unsupported: list[str] = field(default_factory=list)


def verify_answer(answer: str, question: str, rows: list[tuple], extra_text: str = "") -> Verification:
    numbers: list[float] = list(_ALWAYS_NUMBERS)
    dates: set[str] = set(_ALWAYS_DATES)
    strings: list[str] = []
    for row in rows:
        for cell in row:
            if cell is None or isinstance(cell, bool):
                continue
            if isinstance(cell, (int, float, Decimal)):
                numbers.append(float(cell))
            elif isinstance(cell, dt.date):
                dates |= {cell.strftime("%d.%m.%Y"), cell.isoformat()[:10]}
                numbers += [cell.day, cell.month, cell.year]
            else:
                text = str(cell)
                strings.append(text)
                numbers += [t.value for t in extract_numbers(text)]
                dates |= set(extract_dates(text))
    numbers += [t.value for t in extract_numbers(question)]
    dates |= set(extract_dates(question))
    haystack = " ".join(strings) + " " + question
    names_haystack = normalize_name(haystack + " " + extra_text)

    unsupported: list[str] = []
    for d in extract_dates(answer):
        if d not in dates:
            unsupported.append(d)
    for ident in extract_ids(answer):
        if ident not in haystack.upper():
            unsupported.append(ident)
    for tok in extract_numbers(answer):
        if not any(abs(tok.value - v) <= tok.tolerance + 1e-9 * abs(v) for v in numbers):
            unsupported.append(tok.text)
    for quoted in extract_quoted(answer):
        norm = normalize_name(quoted)
        if norm and norm not in names_haystack:
            unsupported.append(quoted)
    return Verification(not unsupported, unsupported)
