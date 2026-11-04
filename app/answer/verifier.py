"""Deterministic check that every fact in an answer comes from the query result or the question."""
import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.answer.numbers import (
    CODE_RE, DATE_RE, MONTH_YEAR_RE, QUOTED_RE, UNIT_AFTER_RE, extract_dates, extract_ids, extract_numbers, format_cell,
    strip_structural,
)
from app.text import normalize_name, normalize_text

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


def _month_years(text: str) -> set[tuple[int, int]]:
    found = {(int(m), int(y)) for m, y in MONTH_YEAR_RE.findall(DATE_RE.sub(" ", text))}
    for d in extract_dates(text):
        if "-" in d:
            y, m, _ = d.split("-")
        else:
            _, m, y = d.split(".")
        found.add((int(m), int(y)))
    return found


_MIN_NAME_CHARS = 6
_SAFE_GLUED = {"m2", "m3", "km2", "sm2", "sm3", "mm2", "mm3", "q1", "q2", "q3", "q4", "h1", "h2", "co2", "h2o"}


def _tok_match(g: str, w: str) -> bool:
    """Name token g matches answer token w: equal, or alphabetic words of 5+ chars differing only in the ending."""
    if g == w:
        return True
    if len(g) < 5 or not g.isalpha() or not w.isalpha() or abs(len(g) - len(w)) > 2:
        return False
    n = max(4, len(g) - 2)
    return w[:n] == g[:n]


def _find_name(name: list[str], tokens: list[str]) -> bool:
    k = len(name)
    return any(all(_tok_match(g, w) for g, w in zip(name, tokens[i:i + k])) for i in range(len(tokens) - k + 1))


def _expand_aliases(cells, alias_groups) -> list[str]:
    """Names of every alias group that one of the (row cell) texts equals."""
    if not alias_groups:
        return []
    index: dict[str, list[frozenset[str]]] = {}
    for g in alias_groups:
        for member in g:
            index.setdefault(member, []).append(g)
    out: list[str] = []
    for c in cells:
        for g in index.get(normalize_name(c), ()):
            out += g
    return out


def _unsupported_known_names(answer: str, supports: list[str], known_names) -> list[str]:
    a_tokens = normalize_name(answer).split()
    prefixes = {t[:3] for t in a_tokens}
    s_tokens = [normalize_name(t).split() for t in supports]
    out = []
    for name in known_names:
        toks = name.split()
        if not toks or (len(toks) < 2 and len(name) < _MIN_NAME_CHARS) or toks[0][:3] not in prefixes:
            continue
        if _find_name(toks, a_tokens) and not any(_find_name(toks, st) for st in s_tokens):
            out.append(name)
    return out


def _spaced(norm: str) -> str:
    return re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", norm)


def verify_answer(answer: str, question: str, rows: list[tuple], extra_text: str = "",
                  row_count: int | None = None, known_names=None, alias_groups=None) -> Verification:
    numbers: list[float] = [] if row_count is None else [float(row_count)]
    month_years: set[tuple[int, int]] = set()
    dates: set[str] = set(_ALWAYS_DATES)
    cells: set[str] = set()
    ids: set[str] = set()
    for row in rows:
        row_nums = {float(c) for c in row if isinstance(c, (int, float, Decimal)) and not isinstance(c, bool)}
        for y in row_nums:
            for m in row_nums:
                if y == int(y) and m == int(m) and 1 <= m <= 12:
                    month_years.add((int(m), int(y)))
        for cell in row:
            if cell is None or isinstance(cell, bool):
                continue
            if isinstance(cell, (int, float, Decimal)):
                numbers.append(float(cell))
            elif isinstance(cell, dt.date):
                dates |= {cell.strftime("%d.%m.%Y"), cell.isoformat()[:10]}
                month_years.add((cell.month, cell.year))
                numbers += [cell.day, cell.month, cell.year]
            else:
                for text in {str(cell), format_cell(cell)}:
                    cells.add(text)
                if isinstance(cell, dt.time):
                    cells.add(cell.strftime("%H:%M"))
                dates |= set(extract_dates(str(cell)))
                month_years |= _month_years(str(cell))
                ids |= set(extract_ids(str(cell)))
    for context in (question, extra_text):
        numbers += [t.value for t in extract_numbers(context)]
        dates |= set(extract_dates(context))
        month_years |= _month_years(context)
        ids |= set(extract_ids(context))
    text_hay = [f" {normalize_text(t)} " for t in [*cells, question, extra_text]]
    spaced_hay = [_spaced(h) for h in text_hay]
    name_hay = [_padded(c) for c in cells] + [_padded(question), _padded(extra_text)]

    unsupported: list[str] = []
    if known_names:
        unsupported += _unsupported_known_names(
            answer, [*cells, question, extra_text, *_expand_aliases(cells, alias_groups)], known_names)
    for d in extract_dates(answer):
        if d not in dates:
            unsupported.append(d)
    for ident in extract_ids(answer):
        if ident not in ids:
            unsupported.append(ident)
    masked = _delete_cells(answer, cells, only_with_quotes=True)
    parts: list[str] = []
    last = 0
    for m in QUOTED_RE.finditer(masked):
        quoted = m.group(1).strip()
        if not re.search(r"[^\W\d_]", quoted):  # no letters: not a name, its numbers are checked normally
            continue
        norm = normalize_name(quoted)
        if not norm or not any(f" {norm} " in hay for hay in name_hay):
            unsupported.append(quoted)
        else:
            parts.append(masked[last:m.start()])
            parts.append(" ")
            last = m.end()
    parts.append(masked[last:])
    scrubbed = _delete_cells("".join(parts), cells)

    def _month_year(m: re.Match) -> str:
        if (int(m.group(1)), int(m.group(2))) not in month_years:
            unsupported.append(m.group(0))
        return " "

    scrubbed = MONTH_YEAR_RE.sub(_month_year, scrubbed)

    def _code(m: re.Match) -> str:
        norm = normalize_text(m.group(0))
        if not (re.search(r"[a-z]", norm) and re.search(r"\d", norm)):
            return m.group(0)
        if UNIT_AFTER_RE.match(scrubbed, m.end()):
            return m.group(0)
        if any(f" {norm} " in hay for hay in text_hay):
            return " "
        glued = _spaced(norm)
        if glued != norm and norm not in _SAFE_GLUED:  # АИ95, М500, Agromir3: letters and digits glued
            if any(f" {glued} " in hay for hay in spaced_hay):
                return " "
            unsupported.append(m.group(0).rstrip(".,"))
            return " "
        return m.group(0)

    scrubbed = strip_structural(CODE_RE.sub(_code, scrubbed))
    for tok in extract_numbers(scrubbed):
        if not any(abs(tok.value - v) <= tok.tolerance + 1e-9 * abs(v) for v in numbers):
            unsupported.append(tok.text)
    return Verification(not unsupported, unsupported)
