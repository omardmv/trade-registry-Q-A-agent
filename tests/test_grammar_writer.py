"""Writer in grammar mode (PLANNER=grammar) with a scripted FakeLLM; legacy mode unchanged."""
import time

import pytest

from app import config
from app.agent import writer as legacy
from app.agent.grammar_writer import GrammarWriter
from app.agent.writer import Writer
from app.plan.dictionary import load_dictionary
from app.plan.model import Plan
from app.plan.prompt import TOOLS, build_system, load_examples, prompt_size
from app.resolve import Resolver
from tests.fakes import FakeLLM, call

INN = "975923536"   # a seller of the test data (see tests/test_writer.py)


@pytest.fixture(scope="module")
def parts(db):
    return db, Resolver(db)


def make(parts, replies, **kw):
    db, resolver = parts
    llm = FakeLLM(chat_replies=replies)
    return GrammarWriter(llm, db, resolver, **kw), llm


def deadline():
    return time.monotonic() + 60


def F(field, value, op="="):
    return {"field": field, "op": op, "value": value}


SELLER_PLAN = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("seller", INN), F("year", 2025)]}


def tool_payload(llm, n):
    """payload of the n-th tool result batch sent back to the model (first result)."""
    batches = [p for kind, p in llm.chat.sent if kind == "tools"]
    return batches[n][0][1]


def test_resolve_then_run_plan(parts):
    writer, llm = make(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                               call("run_plan", plan=SELLER_PLAN, assumptions=[])])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "ok" and res.path == "compiled" and isinstance(res.spec, Plan)
    assert res.result.columns == ["deals_all", "deals_valid"] and "status_scope" in res.splits
    assert res.measure_columns == ["deals_all", "deals_valid"]
    assert tool_payload(llm, 0)["candidates"][0]["id"] == INN
    assert llm.system.startswith("You plan queries") and "run_plan" in [t.name for t in TOOLS]


def test_ungrounded_inn_is_rejected(parts):
    writer, llm = make(parts, [call("run_plan", plan=SELLER_PLAN, assumptions=[]),
                               call("declare_unanswerable", reason="company not found")])
    res = writer.run("Сколько продала компания X в 2025?", "ru", deadline())
    assert res.status == "unanswerable"
    assert INN in tool_payload(llm, 0)["error"] and "resolve" in tool_payload(llm, 0)["error"]


def test_ungrounded_product_is_rejected(parts):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("product", "Сахар"), F("year", 2025)]}
    writer, llm = make(parts, [call("run_plan", plan=plan, assumptions=[]),
                               call("resolve", kind="product", text="сахар"),
                               call("run_plan", plan=plan, assumptions=[])])
    res = writer.run("Сколько сделок с сахаром в 2025?", "ru", deadline())
    assert "Сахар" in tool_payload(llm, 0)["error"] and res.status == "ok"


def test_deal_id_named_in_question_is_grounded(parts):
    db, _ = parts
    deal = str(db.query("SELECT MIN(deal_id) FROM deals").rows[0][0])
    plan = {"subject": "deals", "filters": [F("deal", deal)], "group_by": ["deal"]}
    writer, _ = make(parts, [call("run_plan", plan=plan, assumptions=[])])
    res = writer.run(f"Покажи сделку {deal}", "ru", deadline())
    assert res.status == "ok" and str(res.result.rows[0][0]) == deal


def test_missing_deal_id_says_it_does_not_exist(parts):
    plan = {"subject": "deals", "filters": [F("deal", "999999")], "group_by": ["deal"]}
    writer, llm = make(parts, [call("run_plan", plan=plan, assumptions=[]),
                               call("declare_unanswerable", reason="no such deal")])
    res = writer.run("Покажи сделку 999999", "ru", deadline())
    assert res.status == "unanswerable" and "does not exist" in tool_payload(llm, 0)["error"]


def test_validation_error_is_fed_back_then_fixed(parts):
    bad = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2021)]}
    writer, llm = make(parts, [call("run_plan", plan=bad, assumptions=[]),
                               call("declare_unanswerable", reason="2021 not in data")])
    res = writer.run("Сколько сделок было в 2021?", "ru", deadline())
    assert res.status == "unanswerable" and "outside the data" in tool_payload(llm, 0)["error"]


def test_period_check_feeds_back(parts):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2025)]}
    fixed = {**plan, "filters": [F("year", 2025), F("month", 10)]}
    writer, llm = make(parts, [call("run_plan", plan=plan, assumptions=[]), call("run_plan", plan=fixed, assumptions=[])])
    res = writer.run("Сколько сделок было в октябре 2025?", "ru", deadline())
    assert "month/quarter" in tool_payload(llm, 0)["error"] and res.status == "ok"


def test_product_variant_flow(parts):
    plan = {"subject": "deals", "measures": [{"agg": "count"}],
            "filters": [F("product_variant", "АИ-92"), F("year", 2025)]}
    writer, llm = make(parts, [call("resolve", kind="product_variant", text="АИ-92"),
                               call("run_plan", plan=plan, assumptions=[])])
    res = writer.run("Сколько сделок по АИ-92 в 2025?", "ru", deadline())
    note = tool_payload(llm, 0)["note"]
    assert "product_variant" in note and "product_variant_query" not in note
    assert res.status == "ok" and any(n.startswith("variant_spellings_") for n in res.notes)


def test_fix_budget(parts):
    bad = {"subject": "deals", "measures": [{"agg": "profit"}]}
    writer, _ = make(parts, [call("run_plan", plan=bad, assumptions=[])] * 3, max_fixes=2)
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed" and "fix" in res.reason


def test_free_sql_fallback_still_available(parts):
    sql = "SELECT AVG(delivery_term_days) AS avg_days FROM contracts WHERE contract_form = 'Форвард'"
    writer, _ = make(parts, [call("submit_sql", sql=sql, splits=[], assumptions=[])])
    res = writer.run("Средний срок поставки по форвардам?", "ru", deadline())
    assert res.status == "ok" and res.path == "sql"


def test_text_reply_is_nudged_to_run_plan(parts):
    from app.agent.llm import LLMReply
    writer, llm = make(parts, [LLMReply(text="ответ"), call("declare_unanswerable", reason="x")])
    writer.run("?", "ru", deadline())
    assert "run_plan" in llm.chat.sent[1][1]


def test_compiled_query_error_is_fed_back(parts):
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("product_variant", "zzqx-none")]}
    writer, llm = make(parts, [call("run_plan", plan=plan, assumptions=[]), call("declare_unanswerable", reason="x")])
    writer.run("zzqx-none?", "ru", deadline())
    assert "zzqx-none" in tool_payload(llm, 0)["error"]


def test_prompt_and_schema_size_under_budget():
    size = prompt_size(build_system(load_dictionary(), load_examples()))
    assert size["total"] < 12_000, size


def test_examples_are_valid_plans():
    from app.plan.model import parse_plan
    from app.plan.validate import validate_plan
    exs = load_examples()
    assert len(exs) >= 20
    subjects = set()
    for ex in exs:
        p = validate_plan(parse_plan(ex["plan"]), load_dictionary())
        subjects.add(p.subject)
    assert subjects == {"deals", "contracts", "companies", "brokers", "usd_rates"}


def test_examples_do_not_copy_eval_questions():
    import yaml
    intents = yaml.safe_load((config.ROOT / "eval" / "real" / "intents.yaml").read_text(encoding="utf-8"))
    questions = {q.strip().lower() for i in intents for q in (i.get("questions") or {}).values() if q}
    assert not [e["q"] for e in load_examples() if e["q"].strip().lower() in questions]


def test_legacy_is_default_and_unchanged(parts):
    assert config.PLANNER in ("legacy", "grammar")
    assert Writer.tools is legacy.TOOLS and Writer.compile_tool == "compile_query"
    assert [t.name for t in Writer.tools] == ["resolve", "get_examples", "compile_query", "submit_sql", "explore_sql",
                                              "declare_unanswerable"]


def test_build_pipeline_picks_writer(monkeypatch, db_path):
    from app import pipeline
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "PLANNER", "grammar")
    monkeypatch.setattr("app.agent.llm_fallback.build_llm", lambda *a, **k: FakeLLM())
    assert isinstance(pipeline.build_pipeline().writer, GrammarWriter)
    monkeypatch.setattr(config, "PLANNER", "legacy")
    w = pipeline.build_pipeline().writer
    assert type(w) is Writer


def test_pipeline_explains_plan_and_detects_no_data(parts):
    from app.pipeline import Pipeline
    plan = {"subject": "deals", "measures": [{"agg": "count"}], "filters": [F("year", 2024)]}
    writer, _ = make(parts, [call("run_plan", plan=plan, assumptions=[])])
    res = Pipeline(writer, FakeLLM(completions=["нет", "нет"])).ask("Сколько сделок в 2024 году?")
    assert res.answered and res.explanation.startswith("Расчёт: сделки; за 2024 год; число сделок")
    empty = {"subject": "deals", "measures": [{"agg": "sum", "of": "tonnes"}],
             "filters": [F("unit", "штука"), F("year", 2024)]}
    writer, _ = make(parts, [call("run_plan", plan=empty, assumptions=[])])
    res = Pipeline(writer, FakeLLM()).ask("Сколько тонн штучных товаров в 2024 году?")
    assert not res.answered and res.source == "no_data"
