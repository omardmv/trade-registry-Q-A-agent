"""Word kind of a question word outside the closed vocabulary (max safety, app/plan/vocab.py).

In max safety an unknown word refuses only when it can be a noun: a noun names a thing, and a thing outside the
vocabulary is a thing the data does not have («евро», «директор», «склад», «pribyl»). Any other word (a verb, a
participle, an adverb, a particle, ...) is wording: it does not refuse by itself; the judge (app/plan/judge.py),
which confirms every answer in max safety, gets it as an unknown word and decides.

Blocking is the default (conservative): a word is wording only when it is clearly not a noun.

Always blocking: a word written with a capital letter inside a sentence (a proper name), an all-capitals word (an
abbreviation), a token with digits, a Latin word in a Russian question, a Cyrillic word with Uzbek letters in a
Russian question.

Russian (pymorphy3, offline dictionaries): the word blocks when pymorphy3 does not know it (a guessed parse), or
when the top parse or any parse with score >= 0.1 is a noun or a name / surname / patronymic / place /
organisation / abbreviation. Two fixed prepositional phrases are wording: «за счёт», «в качестве».
russian_lemma() gives the lemma of the top parse: a word whose lemma is in the lexicon is a known word
(«меди» -> «медь», «тройку» -> «тройка»).

Uzbek (Latin; Cyrillic is transliterated by normalize_text first; no tagger exists): wording only when the token
matches one of these rules (UZ_RULES; tests in tests/test_wordkind.py), else it blocks:
  - participle -gan / -kan / -qan (stem >= 2 letters), optionally -lik, -lar, a possessive and a case / -siz /
    -mi / -dir ending: egallagan, foydalangan, bo'lganlarsiz, qaytarilganlarni;
  - present / future -adi / -ydi (stem >= 2), with -lar / -mi: keladi, oladi, egallaydi, kelmaydi, oladimi;
  - present 1st / 2nd person only with the question particle: -asizmi, -aymanmi, ...: bilasizmi, olasizmi;
  - past -di after l, r, t, s, z, sh, ch, k, q, g, b, m, y (not n / d: fondi, bandi), stem >= 2: o'tdi, oshdi,
    bo'ldi, kamaydi;
  - progressive -yapti / -yapdi / -yotgan / -moqda (with -lar / -mi): kutilmoqda;
  - converb -ib after a consonant (stem >= 2): olib, kelib;
  - conditional -sa / -salar / -sangiz after a consonant (length >= 5): bog'lansa;
  - negative / imperative forms -masin, -masdan, -may, -mang: olmay;
  - fixed adverbs, particles and postpositions (UZ_WORDING): haqiqatda, aslida, taxminan, to'g'ri, asosida,
    sababli, tomonidan, ...; and the relational nouns yarm- (half: yarmida, yarmiga) and payt- (time:
    paytgacha) with case suffixes.
A token matching a rule but listed in UZ_NOUNS (nouns that happen to end like a verb form: tarkib, tartib, kassa,
maqsadi, qaydi, organ, ...) blocks.
"""
import re
from functools import lru_cache

from app.text import normalize_text

# ---------------------------------------------------------------- Russian

_RU_NOUN_TAGS = {"NOUN", "Name", "Surn", "Patr", "Geox", "Orgn", "Abbr", "Trad"}
_RU_MIN_SCORE = 0.1
_RU_PHRASES = {("за", "счёт"), ("за", "счет"), ("в", "качестве")}
_UZ_LETTERS = set("ўқғҳЎҚҒҲ")


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


def _ru_blocks(word: str) -> bool:
    w = word.lower()
    m = _morph()
    if not m.word_is_known(w):
        return True                  # a guessed parse: treat as a noun
    parses = m.parse(w)
    if not parses:
        return True
    for i, p in enumerate(parses):
        if i == 0 or p.score >= _RU_MIN_SCORE:
            if any(g in p.tag for g in _RU_NOUN_TAGS):
                return True
    return False


def russian_lemma(word: str) -> str | None:
    """Lemma of the top pymorphy3 parse of a dictionary word (None for unknown words)."""
    w = word.lower()
    if not re.fullmatch(r"[а-яё]+", w):
        return None
    m = _morph()
    if not m.word_is_known(w):
        return None
    parses = m.parse(w)
    return parses[0].normal_form if parses else None


# ---------------------------------------------------------------- Uzbek

UZ_WORDING = frozenset("""
haqiqatda haqiqatan haqiqatdan aslida taxminan deyarli faqat faqatgina hozir hozirgacha yana juda ancha koproq
kamroq odatda umuman shunchaki rostdan ayniqsa xususan masalan togri mumkin kerak iltimos marhamat asosida asosan
tomonidan orqali haqida sababli natijasida mobaynida sifatida tasi beri qadar
""".split())
# relational nouns used as postpositions, with case / possessive suffixes: yarmida (in the half), paytgacha (until)
_UZ_RELATIONAL = re.compile(r"(yarm|payt)(i|si)?(da|ga|dan|gacha|ni|ning|dagi)?")
UZ_NOUNS = frozenset("""
tarkib tartib golib talib sohib nasib garib ajib xatib raqib adib tabib kassa massa narsa bursa maqsadi qaydi
adadi hasadi jasadi organ slogan jildi kurdi hindi
""".split())

_POSS = r"(?:i|ini|iga|ida|idan|si|lari)?"
_CASE = r"(?:ning|ni|ga|da|dan|siz|dir|mi|cha|day|dek|dagi)?"
UZ_RULES = [
    ("participle -gan", re.compile(rf"[a-z]{{2,}}(?:g|k|q)an(?:lik|lig)?(?:lar)?{_POSS}{_CASE}")),
    ("present -adi", re.compile(r"[a-z]{2,}(?:a|y)di(?:lar)?(?:mi)?")),
    ("present + question", re.compile(r"[a-z]{2,}(?:a|y)(?:man|san|miz|siz)(?:lar)?mi")),
    ("past -di", re.compile(r"[a-z]{1,}(?:l|r|t|s|z|sh|ch|k|q|g|b|m|y)di(?:lar|m|ng|k|ngiz)?(?:mi)?")),
    ("progressive", re.compile(r"[a-z]{2,}(?:yapti|yapdi|yotgan|moqda)(?:lar)?(?:mi)?")),
    ("converb -ib", re.compile(r"[a-z]*[bcdfghjklmnpqrstvxz]ib")),
    ("conditional -sa", re.compile(r"[a-z]{2,}[bcdfghjklmnpqrtvxyz]sa(?:lar|ngiz|m|k)?")),
    ("negative / imperative", re.compile(r"[a-z]{2,}(?:masin|masdan|may|mang)")),
]


def uz_rule(token: str) -> str | None:
    """The rule a (Latin, normalize_text) Uzbek token matches as a clear non-noun form, or None."""
    if token in UZ_NOUNS or any(token.startswith(n) and len(n) >= 5 for n in UZ_NOUNS):
        return None
    if token in UZ_WORDING:
        return "adverb / particle / postposition"
    if _UZ_RELATIONAL.fullmatch(token):
        return "relational noun (yarm-, payt-)"
    if len(token) < 4:
        return None
    for name, rx in UZ_RULES:
        if rx.fullmatch(token):
            return name
    return None


# ---------------------------------------------------------------- both

def occurrences(question: str):
    """(token, piece of the written word, sentence-initial, previous token) for every normalize_text token."""
    out = []
    initial = True
    prev = ""
    for word in question.split():
        pieces = [p for p in re.split(r"[^\w‘’ʻʼ'`]+", word) if p]
        lead = True
        for piece in pieces:
            for t in normalize_text(piece).split():
                out.append((t, piece, initial and lead, prev))
                prev = t
                lead = False
        if pieces:
            initial = False
        if re.search(r"[.?!:]$", word.rstrip("»\"”)")):
            initial = True
    return out


def word_kind(token: str, piece: str, lang: str, initial: bool = False, prev: str = "") -> str:
    """'noun' (blocks in max safety) or the kind of a wording word ('verb', 'adverb', ...; does not block)."""
    letters = [ch for ch in piece if ch.isalpha()]
    if any(ch.isdigit() for ch in token) or not letters:
        return "noun"
    if len(letters) >= 2 and all(ch.isupper() for ch in letters):
        return "noun (abbreviation)"
    if letters[0].isupper() and not initial:
        return "noun (proper name)"
    cyr = any("Ѐ" <= ch <= "ӿ" for ch in piece)
    if lang == "ru":
        if not cyr or any(ch in _UZ_LETTERS for ch in piece):
            return "noun"
        if (prev, piece.lower()) in {(normalize_text(a), b) for a, b in _RU_PHRASES}:
            return "preposition"
        if _ru_blocks(piece):
            return "noun"
        m = _morph().parse(piece.lower())[0]
        return str(m.tag.POS or "wording")
    rule = uz_rule(token)
    return rule if rule else "noun"


def is_noun(kind: str) -> bool:
    return kind.startswith("noun")
