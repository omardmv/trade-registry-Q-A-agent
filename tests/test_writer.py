import time

import pytest

from app import config
from app.agent.writer import Writer
from app.examples import ExampleStore
from app.resolve import Resolver
from app.semantic import load_semantic, render_schema_prompt
from tests.fakes import FakeLLM, call


@pytest.fixture(scope="module")
def parts(db):
    return db, Resolver(db), ExampleStore.from_yaml(config.EXAMPLES_PATH), render_schema_prompt(load_semantic())


def make(parts, replies, **kw):
    db, resolver, examples, schema = parts
    llm = FakeLLM(chat_replies=replies)
    return Writer(llm, db, resolver, examples, schema, **kw), llm


def deadline():
    return time.monotonic() + 60


COUNT_SPEC = {"kind": "deals_aggregate", "measures": ["deal_count"],
              "filters": {"year": 2025, "seller_inns": ["975923536"]}}


def test_resolve_then_compile(parts):
    writer, llm = make(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                               call("compile_query", spec=COUNT_SPEC, assumptions=[])])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "ok" and res.path == "compiled"
    assert res.result.columns == ["deals_all", "deals_valid"]
    assert "status_scope" in res.splits
    candidates = llm.chat.sent[1][1][0][1]["candidates"]
    assert candidates[0]["id"] == "975923536"


def test_unresolved_inn_is_rejected_then_unanswerable(parts):
    writer, llm = make(parts, [call("compile_query", spec=COUNT_SPEC, assumptions=[]),
                               call("declare_unanswerable", reason="company not found")])
    res = writer.run("Сколько продала компания X?", "ru", deadline())
    assert res.status == "unanswerable"
    error = llm.chat.sent[1][1][0][1]["error"]
    assert "975923536" in error


def test_spec_error_out_of_range_is_fed_back(parts):
    bad = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2021}}
    writer, llm = make(parts, [call("compile_query", spec=bad, assumptions=[]),
                               call("declare_unanswerable", reason="2021 not in data")])
    res = writer.run("Сколько сделок было в 2021?", "ru", deadline())
    assert res.status == "unanswerable"
    assert "outside the data" in llm.chat.sent[1][1][0][1]["error"]


def test_free_sql_fallback(parts):
    sql = "SELECT AVG(delivery_term_days) AS avg_days FROM contracts WHERE contract_form = 'Форвард'"
    writer, _ = make(parts, [call("submit_sql", sql=sql, splits=[], assumptions=[])])
    res = writer.run("Средний срок поставки по форвардам?", "ru", deadline())
    assert res.status == "ok" and res.path == "sql"
    assert res.result.columns == ["avg_days"]


def test_fix_budget(parts):
    bad = {"kind": "deals_aggregate", "measures": ["profit"]}
    writer, _ = make(parts, [call("compile_query", spec=bad, assumptions=[])] * 3, max_fixes=2)
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed" and "fix" in res.reason


def test_tool_budget(parts):
    writer, _ = make(parts, [call("get_examples", question="x")] * 4, max_tool_calls=3)
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed" and "tool budget" in res.reason


def test_deadline(parts):
    writer, _ = make(parts, [call("get_examples", question="x")])
    res = writer.run("?", "ru", time.monotonic() - 1)
    assert res.status == "failed" and res.reason == "timeout"


def test_text_reply_is_nudged_once(parts):
    from app.agent.llm import LLMReply
    writer, llm = make(parts, [LLMReply(text="The answer is 5"), LLMReply(text="still text")])
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed"
    assert llm.chat.sent[1][0] == "text"


def test_llm_error_on_first_send_is_failed(parts):
    from app.agent.llm import LLMError

    class Boom:
        def start_chat(self, system, tools):
            return self

        def send_text(self, text):
            raise LLMError("blocked")

    db, resolver, examples, schema = parts
    res = Writer(Boom(), db, resolver, examples, schema).run("?", "ru", deadline())
    assert res.status == "failed" and res.reason.startswith("llm error")


def test_empty_reply_counts_as_no_tool_call(parts):
    from app.agent.llm import LLMReply
    writer, llm = make(parts, [LLMReply(), call("declare_unanswerable", reason="x")])
    res = writer.run("?", "ru", deadline())
    assert res.status == "unanswerable"
    assert llm.chat.sent[1][0] == "text"


def test_explore_sql_payload_is_json_safe(parts):
    import json
    sql = "SELECT MIN(deal_date) AS d FROM deals"
    writer, llm = make(parts, [call("explore_sql", sql=sql), call("declare_unanswerable", reason="x")])
    writer.run("?", "ru", deadline())
    payload = llm.chat.sent[1][1][0][1]
    assert "error" not in payload, payload
    json.dumps(payload)
    assert isinstance(payload["rows"][0][0], str)
