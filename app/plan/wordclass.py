"""Closed-choice word classifier for the unknown words of a question (max safety; app/config.py WORD_CLASSIFIER).

Max safety refuses a question whose words the data does not have. The noun gate (app/plan/wordkind.py) refused any
unknown word that can be a noun, which also refused ordinary words the vocabulary does not list (commands «ayting»,
adverbs «chindan», meaning-words «yetakchi» = top, «qism» = share). This module replaces that refusal:

1. One LLM call per question with unknown words (the pipeline's provider chain, temperature from the provider, inside
   the request deadline) labels every unknown word with exactly one label from a CLOSED list:
   - "wording": grammar, a verb / command, an adverb, a particle, filler, politeness: no fact of its own;
   - a concept the data HAS (concepts()): the subjects, fields and named measures of app/fields.yaml and a few shape
     concepts (top, share, count, total, average, minimum, maximum, trend, period);
   - "other": anything else (an object, property, currency, fee, person, place, event, ... not in the list).
   The prompt maps a word to a concept only when it MEANS that concept, never because it is of the same kind (euro is
   a currency like the dollar -> other; a director is a person like a broker -> other); unsure -> other.
2. Decision (app/pipeline.py stage 1, app/agent/grammar_writer.py stage 2):
   - any word labelled "other" -> refuse with the no-data reply naming the word (no planner call);
   - no verdict (error, timeout, unreadable reply, a word without a valid label) -> refuse, never answer;
   - a concept word: after the plan is built, plan_uses() must find the concept in the plan, else refuse;
   - a wording word needs nothing more. The judge (app/plan/judge.py) still confirms every answer.
3. Every classification is logged (logger "app.plan.wordclass"; also one JSON line per question to the file named by
   UZEX_WORDCLASS_LOG when set) for review; nothing is added to the vocabulary automatically.

The stage-1 result reaches stage 2 through a context variable (CURRENT), set by the pipeline around the writer run.
"""
import json
import logging
import os
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.agent.llm import call_with_retry, safe_text
from app.plan.dictionary import Dictionary, load_dictionary

log = logging.getLogger(__name__)

WORDING = "wording"
OTHER = "other"

# shape concepts: what a word can ask for besides a subject / field / measure (description for the prompt)
SHAPES = {
    "top": "top / ranking / leader / the biggest few (лидер, топ, yetakchi)",
    "share": "share / percentage / part of a whole (доля, процент, ulush, qism)",
    "count": "number of items (количество, soni)",
    "total": "total / sum of values (итого, jami)",
    "average": "average / mean / median (средний, o‘rtacha)",
    "minimum": "minimum / the lowest / the least (минимальный, eng kam)",
    "maximum": "maximum / the highest / the most (максимальный, eng ko‘p)",
    "trend": "trend / change / growth / dynamics over time (динамика, рост, o‘sish)",
    "period": "a period / time span / date (период, davr)",
}

_ENTITY_GROUPS = {"company": "company", "broker": "broker", "region": "region", "product": "product",
                  "product_family": "product", "product_variant": "product"}
_GRAINS = ("year", "quarter", "month", "day", "hour")


def _short(label: dict) -> str:
    """The Russian label without its parenthetical notes (the id is English; the model knows Uzbek)."""
    return re.sub(r"\s*\([^)]*\)", "", label.get("ru") or label.get("uz") or "").strip()


def _concepts(d: Dictionary) -> tuple[tuple[str, str], ...]:
    out: dict[str, str] = {}
    for s in d.subjects.values():
        out.setdefault(s.name, f"{_short(s.label)} (subject)")
    for s in d.subjects.values():
        for f in s.fields.values():
            if f.name not in out:
                out[f.name] = _short(f.label)
        for m in s.measures.values():
            if m.name not in out:
                out[m.name] = _short(m.label)
    for k, v in SHAPES.items():
        out.setdefault(k, v)
    return tuple(out.items())


def concepts(d: Dictionary | None = None) -> dict[str, str]:
    """Concept id -> short description: subjects, fields and named measures of fields.yaml, then the shapes."""
    return dict(_concepts(d or load_dictionary()))


def labels(d: Dictionary | None = None) -> frozenset[str]:
    return frozenset(concepts(d)) | {WORDING, OTHER}


SYSTEM = """You label words of a question asked to the database of a commodity exchange: deals, contracts, companies \
(buyers and sellers), brokers and the US dollar rate in so'm. The question is in Russian or Uzbek (Latin or \
Cyrillic). You get the question and the words of it that the database vocabulary does not know. Give each word \
exactly one label:
- "wording": the word carries no fact: grammar, a verb or a command (tell, show, held, made, took), an adverb, a \
particle, filler, politeness, emphasis.
- a concept id from the list below, only when the word MEANS that concept in this question (a synonym, a \
translation, an inflected form). Never because it is the same kind of thing: euro or rouble is a currency like the \
dollar -> "other"; a director or employee is a person like a broker -> "other"; a commission, fee, tax, profit or \
cost is money like an amount -> "other"; a warehouse rent is a price like the deal price -> "other".
- "other": anything else: an object, product property, currency, fee, person, place, event, document, condition \
or action the list does not have; a verb or adjective that names such a fact (insured, terminated early, \
smuggled) is "other" too. The given words are NOT in the database: a specific name among them (a product or \
material such as gold or oil, a company, a person, a country, a city, a currency, an organisation) is a thing the \
data does not have -> "other"; only a generic word that means the concept itself maps to it (goods, \
merchandise -> product). When unsure -> "other".
Reply with one JSON object and nothing else, each given word exactly as written -> its label: {"word": "label"}

Concepts (id: meaning):
"""

EXAMPLES = [
    ("Скажите, сколько реально заключили сделок по цементу в 2024?", ["Скажите", "реально"],
     {"Скажите": "wording", "реально": "wording"}),
    ("Кто лидер по объёму продаж сахара в 2023 году?", ["лидер"], {"лидер": "top"}),
    ("Какой средний курс евро в 2024 году?", ["евро"], {"евро": "other"}),
    ("Сколько тонн платины продали в 2025?", ["платины"], {"платины": "other"}),
    ("Сколько директоров у брокеров из Ташкента?", ["директоров"], {"директоров": "other"}),
    ("Какую комиссию брокеры взяли за сделки 2024 года?", ["комиссию", "взяли"],
     {"комиссию": "other", "взяли": "wording"}),
    ("Ayting-chi, 2024 yilda paxta bo'yicha yetakchi sotuvchi kim?", ["Ayting-chi", "yetakchi"],
     {"Ayting-chi": "wording", "yetakchi": "top"}),
    ("Bitimlarda eksport shartnomalarining qismi qancha?", ["qismi"], {"qismi": "share"}),
    ("2023 yilda Toshkent omborlarining ijara to'lovi qancha bo'lgan?", ["ijara"], {"ijara": "other"}),
    ("Eng ko'p bitimga xizmat ko'rsatgan broker kim?", ["xizmat"], {"xizmat": "broker"}),
    ("Чиндан ҳам 2025 йилда шакар бўйича битимлар сони қанча?", ["Чиндан"], {"Чиндан": "wording"}),
    ("Цемент сотувчиларининг соф фойдаси қанча?", ["соф", "фойдаси"], {"соф": "other", "фойдаси": "other"}),
    ("Йиллар кесимида нархнинг ўсиши қандай?", ["ўсиши"], {"ўсиши": "trend"}),
]


def system_prompt(d: Dictionary | None = None) -> str:
    return SYSTEM + "\n".join(f"{k}: {v}" for k, v in concepts(d).items())


def _case(question: str, words) -> str:
    return f"Question: {question}\nWords: {json.dumps(list(words), ensure_ascii=False)}"


def build_prompt(question: str, words) -> str:
    shots = "\n\n".join(f"{_case(q, w)}\nLabels: {json.dumps(a, ensure_ascii=False)}" for q, w, a in EXAMPLES)
    return f"Examples:\n\n{shots}\n\nNow this one.\n\n{_case(question, words)}\nLabels:"


@dataclass(frozen=True)
class WordClasses:
    """Labels by the word as written; error set (labels empty) when there is no usable verdict."""
    labels: dict = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def other(self) -> list[str]:
        return [w for w, lb in self.labels.items() if lb == OTHER]

    def concept_words(self) -> dict[str, str]:
        return {w: lb for w, lb in self.labels.items() if lb not in (WORDING, OTHER)}

    def label_of(self, word: str) -> str | None:
        if word in self.labels:
            return self.labels[word]
        low = word.lower()
        return next((lb for w, lb in self.labels.items() if w.lower() == low), None)


_OBJ = re.compile(r"\{.*\}", re.S)


def parse_labels(text: str, words, allowed: frozenset[str]) -> WordClasses:
    """The JSON object of the reply: every given word must get a string label; a label outside the closed list is
    "other". A missing word, a non-string label or an unreadable reply is no verdict."""
    m = _OBJ.search(text) if isinstance(text, str) else None
    obj = None
    if m:
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            obj = None
    if not isinstance(obj, dict):
        return WordClasses(error=f"unreadable labels: {str(text or '')[:80]!r}")
    by_lower = {str(k).strip().lower(): v for k, v in obj.items()}
    out = {}
    for w in words:
        v = obj.get(w, by_lower.get(w.lower()))
        if not isinstance(v, str) or not v.strip():
            return WordClasses(error=f"no label for {w!r}")
        v = v.strip()
        lb = v if v in allowed else v.lower() if v.lower() in allowed else OTHER
        out[w] = lb
    return WordClasses(out)


def classify(llm, question: str, words, deadline: float, timeout_s: float,
             d: Dictionary | None = None) -> WordClasses:
    """One LLM call; never raises: an error or a timeout is a WordClasses with error set (refuse)."""
    words = list(dict.fromkeys(words))
    if not words:
        return WordClasses({})
    if deadline - time.monotonic() < timeout_s:
        return WordClasses(error="timeout")
    try:
        text = call_with_retry(lambda: llm.complete(system_prompt(d), build_prompt(question, words)),
                               deadline, timeout_s)
    except Exception as e:  # no traceback: provider messages may contain key material
        log.error("word classifier failed: %s: %s", type(e).__name__, safe_text(e))
        return WordClasses(error=f"llm error: {type(e).__name__}")
    out = parse_labels(text, words, labels(d))
    record(question, words, out)
    return out


def record(question: str, words, out: WordClasses):
    """Log the classification for review (never added to the vocabulary automatically)."""
    if out.error:
        log.warning("word classifier: no verdict (%s) for %s | %s", out.error, list(words), question)
    else:
        log.info("word classifier: %s | %s", ", ".join(f"«{w}» -> {lb}" for w, lb in out.labels.items()), question)
    path = os.environ.get("UZEX_WORDCLASS_LOG", "").strip()
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": time.time(), "question": question, "words": list(words),
                                     "labels": out.labels, "error": out.error}, ensure_ascii=False) + "\n")
        except OSError as e:
            log.error("word classifier log not written: %s", type(e).__name__)


# ---------------------------------------------------------------- the plan uses the concept

def _named_shape(m) -> set[str]:
    sql, ru = m.sql.upper(), (m.label.get("ru") or "").lower()
    out = set()
    if sql.startswith("COUNT"):
        out |= {"count", "total"}
    if sql.startswith("SUM"):
        out.add("total")
    if "AVG(" in sql or "сред" in ru:
        out.add("average")
    if "100.0" in sql or "доля" in ru:
        out.add("share")
    return out


def plan_uses(concept: str, plan, d: Dictionary | None = None) -> bool:
    """Does the plan use the concept: the subject, a field (filter, group_by, measure field or condition), a named
    measure, or a shape (limit, aggregation, grouping by time, comparison, period filter)?"""
    d = d or load_dictionary()
    s = d.subject(plan.subject)
    used = {f.field for f in plan.filters} | set(plan.group_by)
    used |= {m.of for m in plan.measures if m.of} | {w.field for m in plan.measures for w in m.where}
    aggs = {m.agg for m in plan.measures}
    named = [s.measures[a] for a in aggs if a in s.measures]
    shapes = set().union(*(_named_shape(m) for m in named)) if named else set()
    used_groups = {_ENTITY_GROUPS[s.fields[f].ground] for f in used
                   if f in s.fields and s.fields[f].ground in _ENTITY_GROUPS}
    if concept in d.subjects:
        if plan.subject == concept:
            return True
        return {"companies": "company", "brokers": "broker"}.get(concept) in used_groups or (
            concept == "contracts" and "contract" in used)
    if concept in SHAPES:
        time_used = any(f in s.fields and s.fields[f].type in ("grain", "date") for f in used) or bool(plan.compare)
        return {
            "top": plan.limit is not None,
            "share": "share" in aggs or "share" in shapes or any(f.endswith("_pct") for f in used),
            "count": bool(aggs & {"count", "distinct"}) or "count" in shapes or any(f.startswith("deals_as")
                                                                                  for f in used),
            "total": bool(aggs & {"sum", "count"}) or "total" in shapes,
            "average": bool(aggs & {"avg", "median"}) or "average" in shapes,
            "minimum": "min" in aggs or (plan.limit is not None and plan.order == "asc"),
            "maximum": "max" in aggs or (plan.limit is not None and plan.order == "desc"),
            "trend": bool(plan.compare) or any(g in _GRAINS for g in plan.group_by),
            "period": time_used,
        }[concept]
    if concept in aggs or concept in used:
        return True
    f = s.fields.get(concept)
    if f is None:
        # a field of another subject: the same name or the same entity in this plan's subject
        f = next((x.fields[concept] for x in d.subjects.values() if concept in x.fields), None)
        if f is None:
            return False
    group = _ENTITY_GROUPS.get(f.ground)
    generic = bool(f.either) or f.type == "record" or concept in ("product", "region")
    return group is not None and generic and group in used_groups


def unused_concepts(classes: "WordClasses | None", words, plan, d: Dictionary | None = None) -> list[tuple[str, str]]:
    """(word, concept) for the given words labelled with a concept the plan does not use."""
    if classes is None:
        return []
    out = []
    for w in words:
        lb = classes.label_of(w)
        if lb is not None and lb not in (WORDING, OTHER) and not plan_uses(lb, plan, d):
            out.append((w, lb))
    return out


# stage 1 -> stage 2: the classification of the current request (None: classifier off or not run)
CURRENT: ContextVar["WordClasses | None"] = ContextVar("word_classes", default=None)
