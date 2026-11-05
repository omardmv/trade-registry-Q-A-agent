"""An empty assistant reply (no text, no tool calls) is retried once with a short nudge, on any turn."""
import time

import pytest

from app import config
from app.agent.grammar_writer import GrammarWriter
from app.agent.llm import EmptyReplyError, LLMReply
from app.agent.writer import Writer
from app.examples import ExampleStore
from app.resolve import Resolver
from app.semantic import load_semantic, render_schema_prompt
from tests.fakes import FakeLLM, call

INN = "975923536"
PLAN = {"subject": "deals", "measures": [{"agg": "count"}],
        "filters": [{"field": "seller", "value": INN}, {"field": "year", "value": 2025}],
        "mentions": [{"text": "Pudexiz Plast", "used_as": "filter:seller"}, {"text": "2025", "used_as": "filter:year"}]}
SPEC = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2025, "seller_inns": [INN]}}
Q = "Сколько продала Pudexiz Plast в 2025?"


@pytest.fixture(scope="module")
def parts(db):
    return db, Resolver(db)


def grammar(parts, replies):
    db, resolver = parts
    llm = FakeLLM(chat_replies=replies)
    return GrammarWriter(llm, db, resolver), llm


def legacy(parts, replies):
    db, resolver = parts
    llm = FakeLLM(chat_replies=replies)
    return Writer(llm, db, resolver, ExampleStore.from_yaml(config.EXAMPLES_PATH),
                  render_schema_prompt(load_semantic())), llm


def deadline(s=60):
    return time.monotonic() + s


def test_empty_first_reply_raised_is_retried_with_nudge(parts):
    writer, llm = grammar(parts, [EmptyReplyError("OpenAI returned an empty reply"),
                                  call("resolve", kind="company", text="Pudexiz Plast"),
                                  call("run_plan", plan=PLAN, assumptions=[])])
    res = writer.run(Q, "ru", deadline())
    assert res.status == "ok", res.reason
    kind, text = llm.chat.sent[1]
    assert kind == "text" and Q in text and writer.empty_nudge in text   # same turn again, with the nudge


def test_empty_reply_after_tool_results_is_retried(parts):
    writer, llm = grammar(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                                  EmptyReplyError("empty"),
                                  call("run_plan", plan=PLAN, assumptions=[])])
    res = writer.run(Q, "ru", deadline())
    assert res.status == "ok", res.reason
    first, again = llm.chat.sent[1], llm.chat.sent[2]
    assert first[0] == again[0] == "tools"
    assert again[1][0][1]["candidates"] == first[1][0][1]["candidates"]
    assert writer.empty_nudge in again[1][0][1]["note"]


def test_empty_reply_object_is_retried_with_nudge_text(parts):
    # a provider that returns (and keeps) an empty turn: the nudge is sent as a new user message
    writer, llm = grammar(parts, [LLMReply(), call("resolve", kind="company", text="Pudexiz Plast"),
                                  call("run_plan", plan=PLAN, assumptions=[])])
    res = writer.run(Q, "ru", deadline())
    assert res.status == "ok", res.reason
    assert llm.chat.sent[1] == ("text", writer.empty_nudge)


def test_empty_reply_is_retried_on_every_turn(parts):
    writer, _ = grammar(parts, [EmptyReplyError("e"), call("resolve", kind="company", text="Pudexiz Plast"),
                                EmptyReplyError("e"), call("run_plan", plan=PLAN, assumptions=[])])
    res = writer.run(Q, "ru", deadline())
    assert res.status == "ok", res.reason


def test_two_empty_replies_in_a_row_fail(parts):
    writer, llm = grammar(parts, [EmptyReplyError("e"), LLMReply()])
    res = writer.run(Q, "ru", deadline())
    assert res.status == "failed" and "empty reply" in res.reason
    assert len(llm.chat.sent) == 2


def test_empty_reply_retry_respects_deadline(parts, monkeypatch):
    from types import SimpleNamespace

    from app.agent import writer as writer_mod
    clock = {"t": 1000.0}
    monkeypatch.setattr(writer_mod, "time", SimpleNamespace(monotonic=lambda: clock["t"]))

    class SlowEmpty(EmptyReplyError):
        pass

    writer, llm = grammar(parts, [SlowEmpty("e"), call("declare_unanswerable", reason="x")])
    real_next = llm.chat._next

    def slow_next():
        clock["t"] += 20                            # the first (empty) reply took 20 s
        return real_next()
    llm.chat._next = slow_next
    writer.llm_timeout_s = 30
    res = writer.run(Q, "ru", 1045.0)               # one send fits, a second does not
    assert res.status == "failed" and res.reason == "timeout" and len(llm.chat.sent) == 1


def test_legacy_writer_retries_empty_reply(parts):
    writer, llm = legacy(parts, [EmptyReplyError("e"), call("resolve", kind="company", text="Pudexiz Plast"),
                                 call("compile_query", spec=SPEC, assumptions=[])])
    res = writer.run(Q, "ru", deadline())
    assert res.status == "ok", res.reason
    assert writer.empty_nudge in llm.chat.sent[1][1]


def test_openai_empty_reply_raises_empty_reply_error():
    from types import SimpleNamespace

    from app.agent.llm_openai import OpenAIChat
    msg = SimpleNamespace(content="", tool_calls=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: resp)))
    chat = OpenAIChat(client, "m", "sys", [])
    with pytest.raises(EmptyReplyError):
        chat.send_text("hi")
