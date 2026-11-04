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
