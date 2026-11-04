import pytest

from app.resolve import Resolver


@pytest.fixture(scope="module")
def resolver(db):
    return Resolver(db)


def test_company_by_latin_name(resolver):
    top = resolver.resolve("company", "Pudexiz Plast")[0]
    assert top.id == "975923536"
    assert top.score >= 95


def test_company_by_cyrillic_name_typed_in_latin(resolver):
    top = resolver.resolve("company", "Harayom Kimyo")[0]
    assert top.id == "915688255"


def test_company_by_inn(resolver):
    [c] = resolver.resolve("company", "915688255")
    assert c.id == "915688255" and c.score == 100


def test_unknown_inn_returns_nothing(resolver):
    assert resolver.resolve("company", "123456789") == []


def test_broker_by_code_and_name(resolver):
    assert resolver.resolve("broker", "bk001")[0].id == "BK001"
    assert resolver.resolve("broker", "Kekuxo broker")[0].id == "BK001"
    assert resolver.resolve("broker", "BK999") == []


def test_product_russian_and_uzbek(resolver):
    ids = {c.id for c in resolver.resolve("product", "хлопок-сырец")}
    assert "Хлопок-сырец" in ids
    uz = resolver.resolve("product", "paxta")
    assert {"Хлопок-сырец", "Масло хлопковое"} <= {c.id for c in uz}
    assert any(c.kind == "product_family" and c.id == "Хлопок" for c in uz)


def test_product_absent(resolver):
    assert resolver.resolve("product", "золото") == []


def test_region_russian_to_uzbek(resolver):
    assert resolver.resolve("region", "Самаркандская область")[0].id == "Samarqand viloyati"
    assert resolver.resolve("region", "Fargona")[0].id == "Farg‘ona viloyati"


def test_contract_lookup(resolver):
    [c] = resolver.resolve("contract", "100001")
    assert c.id == "100001"
    assert c.extra["product_canonical"] == "Профиль алюминиевый"
    assert resolver.resolve("contract", "999999") == []


def test_product_substring_is_not_a_match(resolver):
    assert resolver.resolve("product", "мед") == []
    for q in ("нефть", "сырая нефть"):
        assert all(c.id != "Битум нефтяной" for c in resolver.resolve("product", q))


def test_product_whole_token_still_matches(resolver):
    assert any(c.id == "Сжиженный газ" for c in resolver.resolve("product", "газ"))


def test_short_word_one_letter_edit_is_not_a_match(resolver):
    paxta = {c.id for c in resolver.resolve("product", "paxta")}
    assert "Пиломатериалы" not in paxta
    taxta = {c.id for c in resolver.resolve("product", "taxta")}
    assert "Пиломатериалы" in taxta
    assert "Хлопок-сырец" not in taxta and "Хлопок" not in taxta


# --- translated aliases count as supported by the known-name check ---
def test_region_aliases_supported_by_translated_cells(db):
    from app.answer.verifier import verify_answer
    r = Resolver(db)
    known, groups = r.known_names(), r.alias_groups()
    answer = "г. Ташкент — 22 790; Наманганская область — 13 459"
    rows = [("Toshkent shahri", 22790), ("Namangan viloyati", 13459)]
    v = verify_answer(answer, "Сделки по регионам?", rows, known_names=known, alias_groups=groups)
    assert v.ok, v.unsupported
    v = verify_answer(answer, "Сделки по регионам?", [("Buxoro viloyati", 22790), ("Buxoro viloyati", 13459)],
                      known_names=known, alias_groups=groups)
    assert not v.ok and any("namangan" in u.lower() for u in v.unsupported)


def test_product_variant_returns_raw_registry_names(resolver):
    cands = resolver.resolve("product_variant", "сахар белый")
    assert cands and cands[0].kind == "product_variant" and cands[0].id == "Сахар белый"
    assert cands[0].extra["product_canonical"] == "Сахар" and cands[0].extra["deals"] > 0
    assert "САХАР-ПЕСОК" not in [c.id for c in cands]


def test_product_variant_tokens_match_all_spellings(resolver):
    ids = [c.id for c in resolver.resolve("product_variant", "катод медный М00К", limit=20)]
    assert ids and all("М00К" in i.upper() for i in ids)


def test_product_variant_unknown(resolver):
    assert resolver.resolve("product_variant", "квантовый компьютер") == []


def _grade_ok(name, sort):
    from app.resolve import canonical_variant_tokens
    toks = canonical_variant_tokens(name)
    return f"sort:{sort}" in toks


def test_variant_grade_tokens_are_canonical():
    from app.resolve import canonical_variant_tokens as c
    assert c("Хлопок-сырец (спот) Сорт 1 сорт Класс II класс")[-2:] == ["sort:1", "klass:2"]
    assert c("Хлопок сырец (спот) Сорт первый сорт Класс I класс")[-2:] == ["sort:1", "klass:1"]
    assert c("Хлопок-сырец (спот) I сорт 1-класс")[-2:] == ["sort:1", "klass:1"]
    assert c("Хлопок-сырец (спот) Сорт 3-сорта Класс 1-класс")[-2:] == ["sort:3", "klass:1"]
    assert c("Пшеница Класс IV класс") == ["pshenitsa", "klass:4"]
    assert c("1 сорт") == c("сорт 1") == c("первый сорт") == c("I сорт") == ["sort:1"]


def test_variant_grade_query_returns_complete_correct_set(resolver, db):
    cands = resolver.resolve("product_variant", "Хлопок-сырец 1 сорт")
    ids = {c.id for c in cands}
    all_cotton = [r[0] for r in db.query("SELECT DISTINCT product_name_full FROM deals "
                                         "WHERE product_canonical = 'Хлопок-сырец'", max_rows=10_000).rows]
    want = {n for n in all_cotton if _grade_ok(n, "1")}
    assert want and ids == want          # complete (no cap) and no other grades via "1 класс"
    assert len(ids) > 10


def test_variant_word_order_matters_when_an_ordered_match_exists(resolver):
    ids = [c.id for c in resolver.resolve("product_variant", "катод медный М00К")]
    assert ids and all(i.lower().startswith("катод медный") for i in ids)
    # no ordered spelling exists: fall back to the word set
    assert resolver.resolve("product_variant", "хлопковая пряжа")
