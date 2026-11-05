"""Stage 2 judge: does the calculation the plan describes answer what the question asks?

Balanced safety: runs only when the question has words outside the closed vocabulary (app/plan/vocab.py) that the
plan does not use as names. Max safety: runs on every plan; its unknown words are the wording words (verbs, adverbs,
...; app/plan/wordkind.py), since an unknown word that can be a noun refuses before the judge. One LLM call (the writer's provider chain, LLM_TIMEOUT_S, inside the request deadline) gets the
question, the deterministic explanation of the plan (app/plan/explain.py: the calculation in words, built before
the SQL runs) and the unknown words, and answers {"covered": true|false, "missing": "..."}.

covered=false -> no data (naming the missing concept in the question's own words); covered=true -> answer; an error,
a timeout or an unreadable reply -> refuse ("could not answer reliably"), never answer. Judges over-flag style
words as missing facts, so the prompt says what does not count and shows both sides in Russian and Uzbek.
"""
import json
import logging
import re
import time
from dataclasses import dataclass

from app.agent.llm import call_with_retry, safe_text

log = logging.getLogger(__name__)

SYSTEM = """You check whether a database calculation answers a user's question.
The database is the registry of a commodity exchange: deals, contracts, participants (companies), brokers and the
US dollar rate. The question is in Russian or Uzbek. You get the question, the calculation (a fixed sentence that
says exactly what the database computes) and the question's words that are not in the database's vocabulary.

Decide whether the calculation answers what the question asks.
- covered = false only when the question asks for a fact or concept the calculation does not compute: another
  currency, another commodity or entity, employees, profit, costs, prices outside the exchange, a forecast, a
  property the registry does not store (address, phone, insurance, ...), a condition the calculation leaves out.
- covered = true when the unknown words are only wording: verbs ("held", "made", "served", "takes", "passed"),
  politeness, filler, synonyms or paraphrases of what the calculation computes, a phrasing of the period, or a
  description of the same measure in other words.
- Judge the meaning of the whole question, not single words. Do not flag a word because it is rare.

Reply with one JSON object and nothing else:
{"covered": true, "missing": ""}
or
{"covered": false, "missing": "<the words of the question that name what the calculation does not cover>"}
"missing" is copied from the question, as written there (a few words)."""

EXAMPLES = [
    ("Сколько сделок по цементу оформили в 2024 году?",
     "Расчёт: сделки; за 2024 год; группа товаров: Цемент; число сделок; все сделки и отдельно без аннулированных.",
     ["оформили"], {"covered": True, "missing": ""}),
    ("Сколько сделок по цементу оплатили в рассрочку в 2024 году?",
     "Расчёт: сделки; за 2024 год; группа товаров: Цемент; число сделок; все сделки и отдельно без аннулированных.",
     ["рассрочку"], {"covered": False, "missing": "в рассрочку"}),
    ("2023 yilda paxta bo'yicha bitimlar summasi qancha yig'ilgan?",
     "Hisob: bitimlar; 2023-yil; tovar guruhi: Paxta; jami: summa (so‘m); barcha bitimlar va alohida bekor "
     "qilinganlarsiz.", ["yig'ilgan"], {"covered": True, "missing": ""}),
    ("Toshkentdagi omborlarning ijara narxi 2023 yilda qancha bo'lgan?",
     "Hisob: bitimlar; 2023-yil; ombor hududi: Ташкент; o‘rtacha tortilgan narx (summa ÷ hajm) so‘m/kg; bekor "
     "qilingan bitimlar hisobga olinmagan.", ["ijara"], {"covered": False, "missing": "ijara narxi"}),
    ("Марҳамат, 2024 йилда шакар бўйича битимлар сонини айтиб юборинг",
     "Ҳисоб: битимлар; 2024-йил; товар гуруҳи: Шакар; битимлар сони; барча битимлар ва алоҳида бекор "
     "қилинганларсиз.", ["Марҳамат", "юборинг"], {"covered": True, "missing": ""}),
    ("2025 йилда цементнинг ўртача нархи фунт стерлингда қанча?",
     "Ҳисоб: битимлар; 2025-йил; товар гуруҳи: Цемент; ўртача тортилган нарх (сумма ÷ ҳажм) сўм/кг; бекор "
     "қилинган битимлар ҳисобга олинмаган.", ["фунт", "стерлингда"], {"covered": False, "missing": "фунт стерлингда"}),
]


def _case(question: str, calculation: str, words) -> str:
    return (f"Question: {question}\nCalculation: {calculation}\n"
            f"Words not in the vocabulary: {', '.join(words) if words else '(none)'}")


def build_prompt(question: str, calculation: str, words) -> str:
    shots = "\n\n".join(f"{_case(q, c, w)}\nAnswer: {json.dumps(a, ensure_ascii=False)}" for q, c, w, a in EXAMPLES)
    return f"Examples:\n\n{shots}\n\nNow this one.\n\n{_case(question, calculation, words)}\nAnswer:"


@dataclass(frozen=True)
class Verdict:
    covered: bool | None          # None: no verdict (error, timeout, unreadable reply) -> refuse
    missing: str = ""
    error: str = ""


_OBJ = re.compile(r"\{.*?\}", re.S)


def parse_verdict(text: str) -> Verdict:
    """The first JSON object of the reply; covered must be a JSON boolean."""
    for m in _OBJ.finditer(text or ""):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("covered"), bool):
            missing = obj.get("missing")
            return Verdict(obj["covered"], str(missing).strip() if isinstance(missing, str) else "")
    return Verdict(None, error=f"unreadable verdict: {(text or '')[:80]!r}")


def judge(llm, question: str, calculation: str, words, deadline: float, timeout_s: float) -> Verdict:
    if deadline - time.monotonic() < timeout_s:
        return Verdict(None, error="timeout")
    try:
        text = call_with_retry(lambda: llm.complete(SYSTEM, build_prompt(question, calculation, words)),
                               deadline, timeout_s)
    except Exception as e:  # no traceback: provider messages may contain key material
        log.error("judge failed: %s: %s", type(e).__name__, safe_text(e))
        return Verdict(None, error=f"llm error: {type(e).__name__}")
    return parse_verdict(text)


def term_for(verdict: Verdict, question: str, words) -> str:
    """The words to name in the no-data reply: the judge's `missing` when it is a short piece of the question
    (the user's own words, never model text), else the first unknown word."""
    m = verdict.missing.strip(" «»\"'.,;:")
    if m and len(m) <= 40 and len(m.split()) <= 4 and m.lower() in question.lower():
        return m
    return words[0] if words else ""
