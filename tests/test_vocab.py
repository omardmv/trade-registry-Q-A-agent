"""Closed vocabulary (app/plan/vocab.py): matcher, the two stages, the refusal text."""
import pytest

from app.plan.vocab import Lexicon, NameWords, build_lexicon, build_vocab, fold, stem
from app.resolve import Resolver
from app.text import normalize_text


# ---- matcher ----

def lex(exact=(), stems=()):
    return Lexicon(exact, stems)


def test_stem_strips_inflection_but_keeps_four_letters():
    assert stem("bitimlarning") == "bitim"
    assert stem("sdelki") == "sdelk"
    assert stem("kurs") == "kurs"
    assert stem("dollar") == "dollar"          # "lar" would leave 3 letters


def test_prefix_stem_covers_inflections():
    lx = lex(stems={"bitim", "sdel", "prodav"})
    for t in ("bitim", "bitimlar", "bitimlarning", "sdelok", "sdelki", "prodavtsov"):
        assert lx.known(t), t
    assert not lx.known("bit") and not lx.known("oltin")


def test_short_entries_are_exact_or_take_suffixes():
    lx = lex(exact={"yil", "bir", "kun", "olti"})
    assert lx.known("yilning") and lx.known("birida") and lx.known("kunlik") and lx.known("kundagi")
    assert lx.known("olti") and not lx.known("oltin")     # a function word does not make "gold" known
    assert not lx.known("yilxyz")


def test_typo_one_edit_on_long_tokens_only():
    lx = lex(stems={"prodav", "tuzil"})
    assert lx.known("prodovtsov")                 # r033: "продовцов"
    assert not lx.known("prodo")                  # too short for a typo
    assert not lx.known("buzilgan")               # a typo never changes the first letter
    assert not lx.known("prodovtsov", typos=False)


def test_scripts_fold_to_one_spelling():
    lx = build_lexicon(extra_terms=["Цемент", "евро"])        # евро only to check the fold
    for word in ("цемента", "sement", "sementning", "tsement", "yevro", "евро"):
        assert lx.known(normalize_text(word)), word
    assert fold("yevro") == fold("evro") and fold("tsement") == "sement"


def test_numbers_ids_and_short_tokens_are_known():
    lx = lex()
    assert lx.known("2025") and lx.known("bk005") and lx.known("983531029") and lx.known("ai")


def test_static_lexicon_has_labels_glossary_months_and_function_words():
    lx = build_lexicon()
    for t in ("skolko", "sdelok", "bitimlar", "qancha", "nechta", "kursi", "dollara", "marta", "oktyabrda",
              "shartnomalar", "eksportnix", "annulirovannix", "bekor", "qilinganlarsiz"):
        assert lx.known(t), t
    for t in ("evro", "yevro", "oltin", "zolota", "pribil", "sotrudnikov", "aksiyalar", "dividend"):
        assert not lx.known(t), t


def test_name_words_take_case_endings():
    names = NameWords(["Linom Energo MChJ", "\"ZO‘JET PLUS\" MCHJ"])
    assert names.has("linom") and names.has("energoning") and names.has("zojet")
    assert not names.has("evro")


@pytest.fixture(scope="module")
def resolver(db):
    return Resolver(db)


def test_vocab_is_built_from_the_database(resolver):
    v = build_vocab({"unit": {"тонна"}}, resolver)
    assert v.unknown("Сколько сделок у Pudexiz Plast?") == []
    assert [u.word for u in v.unknown("Сколько тонн золота продали в 2025 году?")] == ["золота"]


def test_typos_need_a_long_stem():
    assert not Lexicon(stems={"porod"}).known("pogoda")          # 1 edit in 5 letters is another word
    assert Lexicon(stems={"prodav"}).known("prodovtsa")


def test_name_word_endings_must_be_suffixes():
    names = NameWords(["Komi Trade MChJ", "Linom Energo MChJ"])
    assert names.has("energoning") and names.has("komiga")
    assert not names.has("komissiya")                             # "komi" + "ssiya" is another word


def test_uzbek_passive_suffixes():
    lx = Lexicon(exact={"sot", "qil"})
    assert lx.known("sotildi") and lx.known("qilingan") and lx.known("qilindi")
