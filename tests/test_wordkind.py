"""Word kind of unknown words (max safety): nouns block, wording goes to the judge (app/plan/wordkind.py)."""
import pytest

from app.catalog import load_catalog
from app.plan.vocab import build_vocab
from app.plan.wordkind import is_noun, russian_lemma, uz_rule, word_kind
from app.resolve import Resolver
from app.text import normalize_text


def kind(word, lang, initial=False, prev=""):
    return word_kind(normalize_text(word), word, lang, initial, prev)


@pytest.mark.parametrize("word", ["довезли", "делится", "заняла", "реально", "реже", "досрочно", "основана",
                                  "загруженных", "учитывайте", "него", "маленький", "помесячно", "брать"])
def test_russian_wording(word):
    assert not is_noun(kind(word, "ru")), kind(word, "ru")


@pytest.mark.parametrize("word", ["евро", "директор", "склад", "сертификат", "прибыль", "юань", "комиссия", "скидку",
                                  "меди", "счёт", "рынка", "календарь",
                                  "выше",          # a NOUN parse («выша») scores 0.14 >= 0.1: conservative
                                  "дизтоплива",    # not in the pymorphy3 dictionary: a guessed parse blocks
                                  ])
def test_russian_nouns_block(word):
    assert is_noun(kind(word, "ru"))


def test_capitals_latin_and_uzbek_letters_block_in_russian():
    assert is_noun(kind("Лондонской", "ru"))                 # capital inside a sentence: a proper name
    assert not is_noun(kind("Раздели", "ru", initial=True))  # capital at the start of a sentence: a verb
    assert is_noun(kind("ВВП", "ru", initial=True))          # all capitals: an abbreviation
    assert is_noun(kind("Brent", "ru", initial=True)) and is_noun(kind("brent", "ru"))
    assert is_noun(kind("ҳақи", "ru"))


def test_russian_fixed_phrases():
    assert not is_noun(kind("счёт", "ru", prev="za")) and is_noun(kind("счёт", "ru", prev="na"))
    assert not is_noun(kind("качестве", "ru", prev="v"))


def test_russian_lemma():
    assert russian_lemma("меди") == "медь" and russian_lemma("тройку") == "тройка"
    assert russian_lemma("дизтоплива") is None and russian_lemma("Brent") is None


@pytest.mark.parametrize("token,rule", [
    ("keladi", "present -adi"), ("oladi", "present -adi"), ("egallaydi", "present -adi"),
    ("egallagan", "participle -gan"), ("foydalangan", "participle -gan"), ("bolganlarsiz", "participle -gan"),
    ("qaytarilganlarni", "participle -gan"), ("bilasizmi", "present + question"), ("otdi", "past -di"),
    ("oshdi", "past -di"), ("kutilmoqda", "progressive"), ("olib", "converb -ib"), ("boglansa", "conditional -sa"),
    ("olmay", "negative / imperative"), ("haqiqatda", "adverb / particle / postposition"),
    ("togri", "adverb / particle / postposition"), ("yarmida", "relational noun (yarm-, payt-)"),
    ("yarmiga", "relational noun (yarm-, payt-)"), ("paytgacha", "relational noun (yarm-, payt-)"),
])
def test_uzbek_wording_rules(token, rule):
    assert uz_rule(token) == rule


@pytest.mark.parametrize("token", [
    "prognoz", "foydasi", "foyda", "direktori", "xodim", "yevro", "yuanning", "chegirma", "sertifikati", "kumush",
    "ertaga", "sof", "ega", "band", "faol", "oyda", "xizmat", "nuqson", "sabab", "fondi", "bandi", "tarkib",
    "tarkibi", "tartib", "kassa", "narsa", "maqsadi", "qaydi", "organ", "organlar", "yuk", "talab", "import",
])
def test_uzbek_nouns_and_unclear_words_block(token):
    assert uz_rule(token) is None


def test_uzbek_capitals_block():
    assert is_noun(kind("O‘zbekistonda", "uz")) and is_noun(kind("Zapravka", "uz"))
    assert is_noun(kind("Ertaga", "uz", initial=True))
    assert not is_noun(kind("кутилмоқда", "uz-Cyrl"))


@pytest.fixture(scope="module")
def vocab(db):
    return build_vocab(load_catalog(db), Resolver(db))


def test_gate_splits_nouns_and_wording(vocab):
    g = vocab.gate("Сколько контрактов в 2025 году расторгли досрочно?")
    assert g.blocking == [] and [u.word for u in g.wording] == ["расторгли", "досрочно"]
    g = vocab.gate("Сколько сделок в евро расторгли в 2025 году?")
    assert [u.word for u in g.blocking] == ["евро"] and [u.word for u in g.wording] == ["расторгли"]
    g = vocab.gate("2026 йил охирида доллар курси қанча бўлиши кутилмоқда?")
    assert g.blocking == [] and g.kinds["кутилмоқда"] == "progressive"


def test_gate_capital_inside_the_sentence_blocks(vocab):
    g = vocab.gate("Сколько тонн меди продали на Лондонской бирже?")
    assert {u.word for u in g.blocking} >= {"Лондонской"}


@pytest.mark.max_safety
def test_product_named_by_an_adjective_is_known_by_its_noun(vocab):
    """«катодной меди» (fresh2 g006): the product «Катод медный» is named by an adjective; the noun «медь» in any
    case is known through the shared root. Only product names give such roots; a person (повар, строитель) and a
    noun with no product adjective (мёд, евро, золото) still block."""
    from app.plan.wordkind import russian_adjective_root, russian_noun_root
    assert russian_adjective_root("медный") == "мед" and russian_adjective_root("стальной") == "стал"
    assert russian_noun_root("меди") == russian_noun_root("медью") == "мед"
    assert russian_noun_root("повара") is None and russian_noun_root("строители") is None
    assert "мед" in vocab.product_roots
    for q in ("На сколько долларов экспортировали катодной меди в 2024 году?", "Сколько тонн меди продали в 2025?",
              "Объём сделок с медью в 2024 году"):
        assert vocab.gate(q).blocking == [], q
    for q, word in (("Сколько мёда продали в 2024?", "мёда"), ("Сколько золота продали в 2024?", "золота"),
                    ("Сколько поваров купили соль в 2024?", "поваров")):
        assert word in [u.word for u in vocab.gate(q).blocking], q
