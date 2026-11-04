"""Deterministic check that every fact in an answer comes from the query result or the question."""
import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.answer.numbers import (
    extract_dates, extract_ids, extract_numbers, extract_quoted, format_cell, strip_structural,
)
from app.text import normalize_name

_ALWAYS_DATES = {"03.01.2022", "30.10.2026", "01.01.2022", "31.10.2026"}
_QUOTE_CHARS = "«»“”\""
_PLACEHOLDER = "\x00"


@dataclass
class Verification:
    ok: bool
    unsupported: list[str] = field(default_factory=list)


def _padded(text: str) -> str:
    return f" {normalize_name(text)} "


def _delete_cells(text: str, cells: set[str], only_with_quotes: bool = False) -> str:
    chosen = sorted((c for c in cells if c and (not only_with_quotes or any(q in c for q in _QUOTE_CHARS))),
                    key=len, reverse=True)
    if not chosen:
        return text
    # never cut a cell out of the middle of a longer number ("12" inside "12 345,68")
    rx = re.compile(r"(?<!\w)(?<!\d[ \u00a0\u202f.,])(?:" + "|".join(re.escape(c) for c in chosen)
                    + r")(?!\w)(?![ \u00a0\u202f.,]\d)")
    return rx.sub(_PLACEHOLDER if only_with_quotes else " ", text)


def verify_answer(answer: str, question: str, rows: list[tuple], extra_text: str = "") -> Verification:
    numbers: list[float] = []
    dates: set[str] = set(_ALWAYS_DATES)
    cells: set[str] = set()
    ids: set[str] = set()
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
                for text in {str(cell), format_cell(cell)}:
                    cells.add(text)
                dates |= set(extract_dates(str(cell)))
                ids |= set(extract_ids(str(cell)))
    for context in (question, extra_text):
        numbers += [t.value for t in extract_numbers(context)]
        dates |= set(extract_dates(context))
        ids |= set(extract_ids(context))
    name_hay = [_padded(c) for c in cells] + [_padded(question), _padded(extra_text)]

    unsupported: list[str] = []
    for d in extract_dates(answer):
        if d not in dates:
            unsupported.append(d)
    for ident in extract_ids(answer):
        if ident not in ids:
            unsupported.append(ident)
    scrubbed = strip_structural(_delete_cells(answer, cells))
    for tok in extract_numbers(scrubbed):
        if not any(abs(tok.value - v) <= tok.tolerance + 1e-9 * abs(v) for v in numbers):
            unsupported.append(tok.text)
    for quoted in extract_quoted(_delete_cells(answer, cells, only_with_quotes=True)):
        norm = normalize_name(quoted)
        if not norm or not any(f" {norm} " in hay for hay in name_hay):
            unsupported.append(quoted)
    return Verification(not unsupported, unsupported)
