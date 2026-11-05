"""Closed vocabulary: a question may use only words the data has; any other content word means "no data about it".

A normalize_text token (Latin, lower case; Russian / Uzbek Cyrillic transliterated) is known when it is
  1. a function / question / filler word (app/vocab.yaml `function`, exact tokens);
  2. a domain term: stems of the field labels (app/fields.yaml), the glossary (app/semantic.yaml), catalog values
     (products, families, categories, regions, statuses, contract types / forms, activities, units, ...), month
     names and the curated words of app/vocab.yaml `domain`;
  3. a number, date, ID or a token of at most 2 letters (marks, abbreviations; other checks validate them);
  4. a word of an entity name (company, broker, product, variant, region): stage 1 (before the planner) checks
     the names of the whole database, stage 2 (after the plan) only the names of the entities the plan uses.

Matching: a token is known when it equals an entry, starts with a stem of >= 4 letters, or (tokens of >= 6
letters) is one edit away from a stem of >= 5 letters at the start ("prodovtsov" -> "prodav"). Spelling folds:
ts -> s (Cyrillic ц: цемент / sement) and an initial ye -> e (Cyrillic е: евро / yevro).
"""
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml
from rapidfuzz.distance import Levenshtein

from app.text import normalize_name, normalize_text

log = logging.getLogger(__name__)

VOCAB_PATH = Path(__file__).resolve().parent.parent / "vocab.yaml"
FIELDS_PATH = Path(__file__).resolve().parent.parent / "fields.yaml"
SEMANTIC_PATH = Path(__file__).resolve().parent.parent / "semantic.yaml"

MIN_STEM = 4
TYPO_MIN_TOKEN = 6
TYPO_MIN_STEM = 5
MAX_NAME_ENDING = 5        # a name word may carry a case ending ("Energoga", "Энергонинг")

MONTHS = ("январь января январе январю январском yanvar фев февраль февраля феврале fevral март марта марте mart "
          "апрель апреля апреле aprel май мая мае may iyun июнь июня июне iyul июль июля июле август августа "
          "августе avgust сентябрь сентября сентябре sentyabr sentabr октябрь октября октябре oktyabr oktabr "
          "ноябрь ноября ноябре noyabr декабрь декабря декабре dekabr")

# endings stripped to stem a dictionary word (normalize_text form); longest first, a stem keeps >= 4 letters
_ENDINGS = sorted({
    # Russian (transliterated: ы -> i, й -> y, ц -> ts, ь dropped)
    "iyami", "yami", "ami", "iyax", "yax", "ax", "ogo", "ego", "omu", "emu", "imi", "iyx", "ix", "oy", "ey", "iy",
    "aya", "yaya", "oe", "ee", "ie", "uyu", "yuyu", "ov", "ev", "om", "em", "am", "yam", "iyu", "iya", "ii", "ost",
    "ets", "tsa", "tsu", "tsom", "tsi", "tsov", "a", "ya", "i", "e", "o", "u", "yu", "y",
    # Uzbek (plural, case and possessive suffixes)
    "larning", "larni", "larda", "lardan", "larga", "lari", "lar", "ning", "dagi", "dan", "da", "ga", "ni", "si",
    "sini", "ini", "ida", "idan", "iga", "imiz", "ingiz", "ish", "ligi", "lik",
}, key=len, reverse=True)


def fold(token: str) -> str:
    """Spelling folds shared by entries and tokens: ц (ts) = s, an initial е (ye) = e."""
    t = token.replace("ts", "s")
    if t.startswith("ye"):
        t = "e" + t[2:]
    return t


def stem(word: str) -> str:
    """Strip inflection endings while at least MIN_STEM letters remain (bitimlarning -> bitim, sdelki -> sdelk)."""
    w = word
    while True:
        for e in _ENDINGS:
            if w.endswith(e) and len(w) - len(e) >= MIN_STEM:
                w = w[:-len(e)]
                break
        else:
            return w


def _tokens(text: str) -> list[str]:
    return normalize_text(text).split()


def _forms(token: str) -> tuple[str, ...]:
    """The token as written and with the spelling folds (both are looked up: folding can shorten a stem)."""
    f = fold(token)
    return (token,) if f == token else (token, f)


# endings a short entry (3 letters: yil, kun, bir, год) may carry: Uzbek suffix chains and Russian case endings
_SUFFIXES = frozenset({
    "lar", "ning", "ni", "li", "da", "dan", "ga", "ka", "qa", "gi", "dagi", "lik", "lig", "i", "si", "ini", "sini", "ida",
    "idan", "iga", "imiz", "ingiz", "mi", "cha", "gacha", "ta", "dir", "dek", "day",
    "adi", "adigan", "digan", "gan", "kan", "qan", "ib", "sa", "moqda", "di", "mas", "may", "masdan", "ganda",
    "gunch", "guncha", "sh", "ish", "ilgan", "ingan", "lgan", "ngan",
    "a", "u", "e", "y", "o", "ya", "yu", "om", "ov", "ev", "ax", "am", "ami", "oy", "ey", "em", "yax", "yam", "yami",
})


def _suffix_chain(rest: str) -> bool:
    """rest is a sequence of known suffixes (at most 3)."""
    def ok(s: str, depth: int) -> bool:
        if not s:
            return True
        if depth == 3:
            return False
        return any(s.startswith(x) and ok(s[len(x):], depth + 1) for x in _SUFFIXES if len(x) <= len(s))
    return ok(rest, 0)


def _is_trivial(token: str) -> bool:
    return any(ch.isdigit() for ch in token) or len(token) <= 2


@dataclass(frozen=True)
class Unknown:
    token: str      # normalize_text token
    word: str       # the word as the question writes it


def question_words(question: str) -> list[tuple[str, str]]:
    """(folded token, original word) for every normalize_text token of the question."""
    out = []
    for word in question.split():
        clean = word.strip("«»\"“”'.,;:?!()[]—–-…")
        for t in normalize_text(word).split():
            out.append((t, clean or word))
    return out


class Lexicon:
    """Exact entries and prefix stems; known(token) for a folded token."""

    def __init__(self, exact: Iterable[str] = (), stems: Iterable[str] = ()):
        self.exact = frozenset(exact)
        self.stems = frozenset(s for s in stems if len(s) >= MIN_STEM)
        self._by_len: dict[int, list[str]] = {}
        for s in self.stems:
            if len(s) >= TYPO_MIN_STEM:
                self._by_len.setdefault(len(s), []).append(s)

    def known(self, token: str, typos: bool = True) -> bool:
        forms = _forms(token)
        if _is_trivial(token) or any(t in self.exact for t in forms):
            return True
        for t in forms:
            if any(t[:k] in self.stems for k in range(len(t), MIN_STEM - 1, -1)):
                return True
            # an entry with suffixes: yil-ning, bir-i-da, kun-lik, qaysi-si-da, ko'rsat-ilgan
            if any(t[:k] in self.exact and _suffix_chain(t[k:]) for k in range(3, len(t))):
                return True
        return typos and len(token) >= TYPO_MIN_TOKEN and any(self._near_stem(t) for t in forms)

    def _near_stem(self, t: str) -> bool:
        """One edit (substitution, insertion or deletion) inside the stem part of the token."""
        for n, stems in self._by_len.items():
            if n > len(t) + 1:
                continue
            heads = {t[:n - 1], t[:n], t[:n + 1]}
            for s in stems:
                if s[0] == t[0] and any(Levenshtein.distance(h, s, score_cutoff=1) <= 1 for h in heads):
                    return True
        return False


class NameWords:
    """Words of entity names; a question token matches a name word in the same or an inflected form."""

    def __init__(self, names: Iterable[str]):
        words = set()
        for n in names:
            if n:
                words.update(f for t in normalize_name(str(n)).split() for f in _forms(t))
        self.words = frozenset(w for w in words if not _is_trivial(w))
        self._long = frozenset(w for w in self.words if len(w) >= MIN_STEM)

    def has(self, token: str) -> bool:
        for t in _forms(token):
            if t in self.words:
                return True
            if any(t[:k] in self._long for k in range(max(MIN_STEM, len(t) - MAX_NAME_ENDING), len(t))):
                return True
        return False

    def __bool__(self):
        return bool(self.words)


def _add(entries: Iterable[str], exact: set, stems: set, stemmed: bool):
    for entry in entries:
        entry = str(entry).strip()
        if not entry:
            continue
        if entry.endswith("*"):
            for t in _tokens(entry[:-1]):
                (stems if len(t) >= MIN_STEM else exact).update(_forms(t))
            continue
        for raw in _tokens(entry):
            exact.update(_forms(raw))
            if stemmed and not _is_trivial(raw):
                s = stem(raw)
                if len(s) >= MIN_STEM:
                    stems.update(f for f in _forms(s) if len(f) >= MIN_STEM)


def _strings(node) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _strings(v)


def _field_terms(raw: dict) -> list[str]:
    """Labels and enum values of every subject, field and measure of fields.yaml."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("label", "count_label", "values"):
                    out.extend(_strings(v))
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(raw)
    return out


def _glossary_terms(raw: dict) -> list[str]:
    """Glossary lines of semantic.yaml, without the schema side ("→ amount_uzs")."""
    return [line.split("→")[0].replace("/", " ") for line in raw.get("glossary") or ()]


@lru_cache(maxsize=1)
def _static_sources() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    vocab = yaml.safe_load(VOCAB_PATH.read_text(encoding="utf-8"))
    function = tuple(w for lang in (vocab.get("function") or {}).values() for w in lang)
    domain = tuple(w for lang in (vocab.get("domain") or {}).values() for w in lang)
    fields = _field_terms(yaml.safe_load(FIELDS_PATH.read_text(encoding="utf-8")))
    glossary = _glossary_terms(yaml.safe_load(SEMANTIC_PATH.read_text(encoding="utf-8")))
    return function, domain + tuple(fields) + tuple(glossary) + tuple(MONTHS.split()), tuple(fields)


def build_lexicon(catalog: dict[str, set[str]] | None = None, extra_terms: Iterable[str] = ()) -> Lexicon:
    """Lexicon of the static sources plus the catalog values and extra domain terms (product / region names)."""
    function, domain, _ = _static_sources()
    exact: set[str] = set()
    stems: set[str] = set()
    _add(function, exact, stems, stemmed=False)
    _add(domain, exact, stems, stemmed=True)
    _add((v for values in (catalog or {}).values() for v in values), exact, stems, stemmed=True)
    _add(extra_terms, exact, stems, stemmed=True)
    return Lexicon(exact, stems)


class Vocab:
    """Lexicon + the entity names of the database (stage 1)."""

    def __init__(self, lexicon: Lexicon, names: NameWords | None = None):
        self.lexicon = lexicon
        self.names = names or NameWords(())

    def unknown(self, question: str, names: NameWords | None = None) -> list[Unknown]:
        """Tokens of the question outside the lexicon and outside the given names (default: all DB names)."""
        names = self.names if names is None else names
        out, seen = [], set()
        for token, word in question_words(question):
            if token in seen or self.lexicon.known(token) or names.has(token):
                continue
            seen.add(token)
            out.append(Unknown(token, word))
        return out

    def unknown_in_plan(self, question: str, plan_names: Iterable[str]) -> list[Unknown]:
        """Stage 2: lexicon misses that are not words of the entities the plan uses."""
        return self.unknown(question, NameWords(plan_names))


_SPLIT_PRODUCT = re.compile(r"[;,]")


def build_vocab(catalog: dict[str, set[str]] | None, resolver) -> Vocab:
    """Lexicon from the static sources, the catalog and the resolver's products / regions; stage-1 names from all
    companies, brokers, products, product variants and regions of the database."""
    terms = []
    for product_name, canonical, family, synonyms in resolver.product_rows():
        terms += [product_name, canonical, family, *_SPLIT_PRODUCT.split(synonyms or "")]
    for region, region_ru, synonyms in resolver.region_rows():
        terms += [region, region_ru, *_SPLIT_PRODUCT.split(synonyms or "")]
    lexicon = build_lexicon(catalog, terms)
    return Vocab(lexicon, NameWords(resolver.all_entity_names()))


def log_refusal(stage: str, question: str, unknown: list[Unknown]):
    log.warning("closed vocabulary (%s): no data about %s in %r", stage,
                ", ".join(f"«{u.word}» ({u.token})" for u in unknown), question)
