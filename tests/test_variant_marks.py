"""Product spelling matcher: glued letters/digits are split and grade / mark tokens compared as a set."""
import re

import pytest

from app.resolve import Resolver, canonical_variant_tokens


@pytest.fixture(scope="module")
def resolver(db):
    return Resolver(db)


def _cem_i_42_5_n(db) -> set[str]:
    """Every registry spelling of the grade ЦЕМ I 42,5 Н in any form (space or no space before Н, any case,
    any product wording), found with an independent regex over the raw names."""
    rows = db.query("SELECT DISTINCT product_name_full FROM deals", max_rows=100_000).rows
    rx = re.compile(r"ЦЕМ\s+I\s+42[,.]5\s*Н(?![А-ЯA-Z])", re.IGNORECASE)
    return {r[0] for r in rows if rx.search(r[0])}


def test_glued_letters_and_digits_are_split():
    assert canonical_variant_tokens("ЦЕМ I 42,5Н") == canonical_variant_tokens("ЦЕМ I 42,5 Н")
    assert canonical_variant_tokens("М00К") == ["m", "00", "k"]
    assert canonical_variant_tokens("М00К") != canonical_variant_tokens("М0К")


@pytest.mark.parametrize("text", ["ЦЕМ I 42,5 Н", "ЦЕМ I 42,5Н", "цем i 42,5н"])
def test_every_spelling_of_the_grade_matches(resolver, db, text):
    want = _cem_i_42_5_n(db)
    assert len(want) >= 8
    assert set(resolver.variant_names(text)) == want


def test_product_worded_query_matches_its_wording_and_reports_the_rest(resolver, db):
    want = _cem_i_42_5_n(db)
    got = set(resolver.variant_names("Цемент портландский ЦЕМ I 42,5Н"))
    assert got and got < want
    assert all("портландский" in n.lower() for n in got)   # the glued spellings are included
    assert any(n.endswith("42,5Н") for n in got)
    missed = set(resolver.same_grade_spellings("Цемент портландский ЦЕМ I 42,5Н"))
    assert got | missed == want and not got & missed


def test_other_grades_are_not_matched(resolver):
    names = resolver.variant_names("ЦЕМ I 42,5 Н")
    assert not any("32,5" in n or "II" in n for n in names)
    assert resolver.same_grade_spellings("сахар белый") == []   # no grade / mark in the text
