"""LLM answer writer. It only rephrases result rows; the verifier checks its output."""
from app.answer.numbers import format_cell

LANG_NAMES = {"ru": "Russian", "uz": "Uzbek (Latin script)", "uz-Cyrl": "Uzbek (Cyrillic script)"}

SYSTEM = """You write the final answer of a commodity-exchange Q&A service.
Rules:
- Answer in {language}.
- Use ONLY values present in the result rows or the question. Never add numbers, names, INNs, broker codes,
  contract numbers or dates that are not there. Do not calculate new numbers (no sums, differences or
  percentages); you may only shorten large numbers like "1,23 млрд" / "1,23 mlrd".
- Copy INNs, broker codes and contract numbers exactly. Put company and broker names in «» exactly as in the
  rows, followed by the INN or code in parentheses.
- Columns ending in _all / _valid: _all = all deals, _valid = without cancelled (annulled) deals. Present both.
  A "ranking" column (by_volume_t, by_total_uzs ...) or "role" column (buyer / seller) means separate
  variants: present each. If the variants give the same top result, say it once.
- Column suffixes: _uzs = so'm, _usd = US dollars, _t = tonnes, _pct = percent.
- Do not compute percentages, shares or differences; if the rows contain them, copy them.
- Write company names in «» exactly as in the rows.
- Format numbers with spaces between thousands and a comma for decimals: 1 234 567,89.
- 1–4 sentences, or a short list for rankings. No preamble, no SQL, no explanation of the method.
"""


def render_rows(columns: list[str], rows: list[tuple], limit: int = 200) -> str:
    lines = [" | ".join(columns)]
    lines += [" | ".join(format_cell(v) for v in row) for row in rows[:limit]]
    return "\n".join(lines)


def compose(llm, *, question: str, lang: str, sql: str, columns: list[str], rows: list[tuple], row_count: int,
            truncated: bool, splits: list[str], assumptions: list[str], feedback: list[str] | None = None) -> str:
    prompt = (f"Question: {question}\n\nExecuted SQL:\n{sql}\n\n"
              f"Result ({row_count} rows{', truncated' if truncated else ''}):\n{render_rows(columns, rows)}\n\n"
              f"Split axes: {', '.join(splits) or 'none'}\n"
              f"Assumptions: {'; '.join(assumptions) or 'none'}\n")
    if feedback:
        prompt += ("\nYour previous answer contained values that are NOT in the result: " + ", ".join(feedback)
                   + ". Rewrite it using only values from the result.\n")
    return llm.complete(SYSTEM.format(language=LANG_NAMES.get(lang, "Russian")), prompt).strip()
