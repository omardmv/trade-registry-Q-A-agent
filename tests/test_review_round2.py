"""Second review round: product_variant_query and status shares."""
import json
import time

import pytest

from app import config
from app.agent.validators import validate_sql
from app.agent.writer import Writer
from app.compiler.explain import explain
from app.compiler.spec import SpecError, parse_spec
from app.compiler.sql import compile_spec
from app.examples import ExampleStore
from app.resolve import Resolver
from app.semantic import load_semantic, render_schema_prompt
from tests.fakes import FakeLLM, call

COTTON_1 = "Хлопок-сырец 1 сорт"


@pytest.fixture(scope="module")
def resolver(db):
    return Resolver(db)


def agg(measures, **kw):
    return {"kind": "deals_aggregate", "measures": measures, **kw}


def make(db, resolver, replies):
    llm = FakeLLM(chat_replies=replies)
    w = Writer(llm, db, resolver, ExampleStore.from_yaml(config.EXAMPLES_PATH), render_schema_prompt(load_semantic()))
    return w, llm


# 1+2. product_variant_query -------------------------------------------------------------------------------------

def test_free_sql_filter_on_product_spellings_rejected():
    for sql in ("SELECT COUNT(*) FROM deals WHERE product_name_full IN ('Хлопок сырец (спот) Сорт 1 сорт Класс 2 класс')",
                "SELECT COUNT(*) FROM deals WHERE product_name_full = 'Сахар белый'",
                "SELECT COUNT(*) FROM deals WHERE product_name_full LIKE '%1 сорт%'",
                "SELECT COUNT(*) FROM contracts WHERE product_name = 'Сахар белый'"):
        r = validate_sql(sql, {("product_variant", "Сахар белый"),
                               ("product_variant", "Хлопок сырец (спот) Сорт 1 сорт Класс 2 класс")}, "")
        assert not r.ok and any("filters.product_variant_query" in e for e in r.errors), (sql, r.errors)
    assert validate_sql("SELECT product_name_full, COUNT(*) FROM deals GROUP BY 1", set(), "").ok


def test_variant_query_counts_every_matching_spelling(db, resolver):
    names = resolver.variant_names(COTTON_1)
    assert len(names) == 122
    want = db.query("SELECT COUNT(*) FROM deals WHERE product_name_full IN (SELECT unnest(?))", [names]).rows[0][0]
    spec = parse_spec(agg(["deal_count"], status_scope="all", filters={"product_variant_query": COTTON_1}))
    c = compile_spec(spec, variants=resolver.variant_names)
    assert db.query(c.sql).rows == [(want,)]
    text = explain(spec, c.notes, "ru")
    assert f"товар: варианты названия «{COTTON_1}» (122 написаний)" in text


def test_variant_query_without_match_is_an_error(resolver):
    spec = parse_spec(agg(["deal_count"], filters={"product_variant_query": "квантовый компьютер"}))
    with pytest.raises(SpecError, match="no product spelling matches 'квантовый компьютер'.*declare_unanswerable"):
        compile_spec(spec, variants=resolver.variant_names)


def test_product_variants_list_removed():
    with pytest.raises(SpecError, match="product_variants"):
        parse_spec(agg(["deal_count"], filters={"product_variants": ["Сахар белый"]}))


def test_resolve_variant_result_is_compact(db, resolver):
    w, _ = make(db, resolver, [])
    allowed, names = set(), []
    payload = w._tool("resolve", {"kind": "product_variant", "text": "хлопок"}, allowed, "q", 0, names, {})
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    assert size < 5000, size
    assert payload["matched_spellings"] > 15 and len(payload["sample_spellings"]) <= 15
    assert payload["deals"] > 0 and "product_variant_query" in payload["note"]
    assert ("product_variant_query", "хлопок") in allowed


def test_writer_variant_query_needs_resolve(db, resolver):
    spec = agg(["deal_count"], status_scope="all", filters={"product_variant_query": COTTON_1})
    w, llm = make(db, resolver, [call("compile_query", spec=spec, assumptions=[]),
                                 call("resolve", kind="product_variant", text=COTTON_1),
                                 call("compile_query", spec=spec, assumptions=[])])
    res = w.run("Сколько сделок по хлопку-сырцу 1 сорта?", "ru", time.monotonic() + 60)
    assert "resolve" in llm.chat.sent[1][1][0][1]["error"]
    assert res.status == "ok" and "variant_spellings_122" in res.notes


# 3. share_pct over status --------------------------------------------------------------------------------------

def test_status_share_by_amount_counts_all_statuses(db):
    spec = parse_spec(agg(["share_pct"], filters={"year": 2025}, share_of={
        "field": "status", "value": "Аннулирован(Не оплачен)", "basis": "amount_uzs"}))
    got = db.query(compile_spec(spec).sql).rows[0][0]
    want = db.query("SELECT ROUND(100.0 * SUM(amount_uzs) FILTER (WHERE is_cancelled) / SUM(amount_uzs), 2) "
                    "FROM deals WHERE year = 2025").rows[0][0]
    assert got == want and got > 0
    assert "включая аннулированные" in explain(spec, compile_spec(spec).notes, "ru")


def test_status_share_with_other_measure_counts_all_statuses(db):
    spec = parse_spec(agg(["share_pct", "amount_uzs"], status_scope="valid", filters={"year": 2025}, share_of={
        "field": "status", "value": "Аннулирован(Не оплачен)", "basis": "volume_t"}))
    got = db.query(compile_spec(spec).sql).rows[0][0]
    want = db.query("SELECT ROUND(100.0 * SUM(volume_tonnes) FILTER (WHERE is_cancelled) / SUM(volume_tonnes), 2) "
                    "FROM deals WHERE year = 2025").rows[0][0]
    assert got == want
