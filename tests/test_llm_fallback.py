import time

import httpx
import pytest

from app import config
from app.agent.llm import (GeminiLLM, LLMError, LLMReply, ToolCall, ToolSpec, call_with_retry, classify_error,
                           deadline_scope, is_permanent, track_providers)
from app.agent.llm_fallback import FallbackChat, FallbackLLM, build_llm
from app.agent.llm_openai import OpenAILLM
from app.agent.writer import Writer
from app.examples import ExampleStore
from app.pipeline import Pipeline
from app.resolve import Resolver
from app.semantic import load_semantic, render_schema_prompt
from tests.llm_stubs import (FakeClock, Slow, g_call, g_error, g_text, gemini, o_call, o_resp, o_status, o_text,
                             openai_llm)
from tests.test_pipeline import Q, StubWriter, ok

TOOL = ToolSpec("lookup", "Look up a deal.", {"type": "OBJECT", "properties": {"deal_id": {"type": "INTEGER"}}})


def g_calls(llm):
    return llm._client.models.calls


def o_calls(llm):
    return llm._client.chat.completions.calls


# ---- error classification ----
@pytest.mark.parametrize("code", [400, 401, 402, 403, 404, 429, 500, 503])
def test_gemini_api_errors_are_fallback(code):
    assert classify_error(g_error(code)) == "fallback"


def test_classify_error():
    req = httpx.Request("POST", "https://x")
    assert classify_error(TimeoutError()) == "timeout"
    assert classify_error(httpx.ReadTimeout("t", request=req)) == "timeout"
    assert classify_error(httpx.ConnectError("refused", request=req)) == "fallback"
    assert classify_error(LLMError("blocked")) == "fallback"
    assert classify_error(ValueError("x")) == "bug"
    assert classify_error(KeyError("x")) == "bug"
    assert [is_permanent(g_error(c)) for c in (401, 402, 403, 429, 500)] == [True, True, True, False, False]
    assert is_permanent(o_status_provider(402)) and not is_permanent(o_status_provider(429))
    assert not is_permanent(TimeoutError()) and not is_permanent(LLMError("x"))


def o_status_provider(code):
    with pytest.raises(Exception) as info:
        openai_llm([o_status(code)]).complete("s", "p")
    return info.value


# ---- chat fallback ----
def test_gemini_402_on_first_send_openai_answers():
    g, o = gemini([g_error(402, "credits depleted")]), openai_llm([o_call("lookup", deal_id=7), o_text("BK1")])
    llm = FallbackLLM(g, o)
    chat = llm.start_chat("sys", [TOOL])
    assert chat.provider == "gemini"
    with track_providers() as used:
        reply = chat.send_text("find 7")
        assert reply.tool_calls == [ToolCall("lookup", {"deal_id": 7})]
        assert chat.send_tool_results([("lookup", {"broker": "BK1"})]).text == "BK1"
    assert chat.provider == "openai" and llm.last_provider == "openai"
    assert used == ["openai", "openai"] and chat.providers_used == ["openai", "openai"]
    assert len(g_calls(g)) == 1  # stays on OpenAI for the rest of the conversation
    assert o_calls(o)[0]["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "find 7"}]


def test_gemini_failure_mid_conversation_replays_transcript():
    g = gemini([g_call("lookup", deal_id=1), g_error(503)])
    o = openai_llm([o_text("answer")])
    chat = FallbackLLM(g, o).start_chat("sys", [TOOL])
    chat.send_text("q")
    assert chat.send_tool_results([("lookup", {"row": 1})]).text == "answer"
    msgs = o_calls(o)[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "lookup"
    assert msgs[3] == {"role": "tool", "tool_call_id": msgs[2]["tool_calls"][0]["id"], "content": '{"row": 1}'}
    assert chat.events[-2] == ("tool_results", [("lookup", {"row": 1})])


def test_openai_primary_replays_into_gemini():
    o = openai_llm([o_call("lookup", deal_id=1), o_status(402)])
    g = gemini([g_text("done")])
    chat = FallbackLLM(o, g).start_chat("sys", [TOOL])
    chat.send_text("q")
    assert chat.send_tool_results([("lookup", {"row": 1})]).text == "done"
    contents = g_calls(g)[0]
    assert [c.role for c in contents] == ["user", "model", "user"]
    fc = contents[1].parts[0].function_call
    assert fc.name == "lookup" and fc.args == {"deal_id": 1}
    assert contents[1].parts[0].thought_signature
    assert contents[2].parts[0].function_response.response == {"row": 1}


def test_blocked_reply_falls_back():
    g = gemini([g_call("lookup", deal_id=1).model_copy(update={"candidates": []})])
    o = openai_llm([o_text("ok")])
    assert FallbackLLM(g, o).start_chat("s", [TOOL]).send_text("q").text == "ok"


def test_secondary_failure_raises_no_ping_pong():
    g = gemini([g_error(402)])
    o = openai_llm([o_status(500), o_status(500)])
    chat = FallbackLLM(g, o).start_chat("s", [TOOL])
    with pytest.raises(Exception, match="500"):
        chat.send_text("q")
    with pytest.raises(Exception, match="500"):  # switched once; the retry stays on OpenAI
        chat.send_text("q")
    assert len(g_calls(g)) == 1 and len(o_calls(o)) == 2 and chat.events == []


def test_timeout_does_not_switch():
    g = gemini([TimeoutError("slow"), g_text("second try")])
    o = openai_llm([])
    chat = FallbackLLM(g, o).start_chat("s", [TOOL])
    with pytest.raises(TimeoutError):
        chat.send_text("q")
    assert o_calls(o) == []
    # the caller's retry goes to the same provider
    assert call_with_retry(lambda: chat.send_text("q"), time.monotonic() + 60, 1.0).text == "second try"
    assert chat.provider == "gemini" and o_calls(o) == []


def test_bug_propagates_without_fallback():
    g = gemini([KeyError("bug in our code")])
    o = openai_llm([o_text("never")])
    chat = FallbackLLM(g, o).start_chat("s", [TOOL])
    with pytest.raises(KeyError):
        chat.send_text("q")
    assert o_calls(o) == []


# ---- circuit breaker ----
def test_circuit_breaker_routes_new_chats_then_resets():
    clock = FakeClock()
    g = gemini([g_error(402), g_text("gemini back")])
    o = openai_llm([o_text("a"), o_text("b"), o_text("c")])
    llm = FallbackLLM(g, o, cooldown_s=300, clock=clock)
    assert llm.start_chat("s", [TOOL]).send_text("q").text == "a"
    assert llm.is_down(0)
    chat2 = llm.start_chat("s", [TOOL])
    assert chat2.provider == "openai" and chat2.send_text("q").text == "b"
    assert llm.complete("s", "p") == "c"  # completions skip the primary too
    assert len(g_calls(g)) == 1
    clock.advance(301)
    assert not llm.is_down(0)
    chat3 = llm.start_chat("s", [TOOL])
    assert chat3.provider == "gemini" and chat3.send_text("q").text == "gemini back"


def test_rate_limit_does_not_trip_breaker():
    g = gemini([g_error(429)])
    llm = FallbackLLM(g, openai_llm([o_text("a")]), clock=FakeClock())
    llm.start_chat("s", [TOOL]).send_text("q")
    assert not llm.is_down(0) and llm.start_chat("s", [TOOL]).provider == "gemini"


# ---- complete() ----
def test_complete_falls_back():
    g = gemini([g_error(402)])
    o = openai_llm([o_text("from openai")])
    llm = FallbackLLM(g, o)
    with track_providers() as used:
        assert llm.complete("s", "p") == "from openai"
    assert used == ["openai"] and llm.last_provider == "openai"


def test_complete_timeout_does_not_switch():
    llm = FallbackLLM(gemini([TimeoutError()]), openai_llm([]))
    with pytest.raises(TimeoutError):
        llm.complete("s", "p")


def test_pipeline_compose_falls_back_and_reports_providers():
    g = gemini([g_error(402)])
    o = openai_llm([o_text("Заключено 9 912 сделок, без аннулированных — 9 300.")])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), FallbackLLM(g, o)).ask(Q)
    assert r.source == "llm" and r.providers_used == ["openai"]


# ---- deadline guard ----
def test_slow_primary_failure_near_deadline_skips_secondary():
    clock = FakeClock()
    g = gemini([Slow(14, g_error(503), clock)])
    o = openai_llm([o_text("never")])
    llm = FallbackLLM(g, o, timeout_s=15, clock=clock)
    chat = llm.start_chat("s", [TOOL])
    deadline = clock() + 20
    with pytest.raises(Exception, match="503"):
        chat.send_text("q", deadline=deadline)
    assert o_calls(o) == [] and clock() <= deadline
    # same with the deadline taken from the context (writer / composer path)
    g._client.models.outcomes = [Slow(14, g_error(402), clock)]
    with deadline_scope(clock() + 20):
        with pytest.raises(Exception, match="402"):
            llm.complete("s", "p")
    assert o_calls(o) == []


def test_fast_failure_leaves_time_for_secondary():
    clock = FakeClock()
    llm = FallbackLLM(gemini([Slow(0.5, g_error(402), clock)]), openai_llm([o_text("ok")]), timeout_s=15,
                      clock=clock)
    with deadline_scope(clock() + 20):
        assert llm.complete("s", "p") == "ok"


@pytest.fixture(scope="module")
def parts(db):
    return db, Resolver(db), ExampleStore.from_yaml(config.EXAMPLES_PATH), render_schema_prompt(load_semantic())


def _writer(parts, llm, **kw):
    db, resolver, examples, schema = parts
    return Writer(llm, db, resolver, examples, schema, **kw)


def test_writer_worst_case_stays_within_deadline(parts):
    """Real clock: the primary fails slowly, leaving less than one LLM timeout -> no secondary call."""
    timeout = 0.4
    g = gemini([Slow(0.3, g_error(402))])
    o = openai_llm([o_text("never")])
    writer = _writer(parts, FallbackLLM(g, o, timeout_s=timeout), llm_timeout_s=timeout)
    t0 = time.monotonic()
    deadline = t0 + 0.6
    res = writer.run("Сколько сделок?", "ru", deadline)
    assert res.status == "failed" and "402" in res.reason
    assert o_calls(o) == [] and time.monotonic() <= deadline


# ---- the real writer with scripted providers ----
COUNT_SPEC = {"kind": "deals_aggregate", "measures": ["deal_count"],
              "filters": {"year": 2025, "seller_inns": ["975923536"]}}


def test_writer_gemini_402_on_third_turn_finishes_on_openai(parts):
    g = gemini([g_call("resolve", kind="company", text="Pudexiz Plast"), g_call("get_examples", question="x"),
                g_error(402, "Your prepayment credits are depleted")])
    o = openai_llm([o_call("compile_query", cid="call_x", spec=COUNT_SPEC, assumptions=[])])
    llm = FallbackLLM(g, o, clock=FakeClock())
    with track_providers() as used:
        res = _writer(parts, llm).run("Сколько продала Pudexiz Plast в 2025?", "ru", time.monotonic() + 60)
    assert res.status == "ok" and res.path == "compiled"
    assert res.result.columns == ["deals_all", "deals_valid"]
    assert used == ["gemini", "gemini", "openai"]
    msgs = o_calls(o)[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "resolve"
    assert "975923536" in msgs[3]["content"]  # the resolve result reached OpenAI
    assert msgs[4]["tool_calls"][0]["function"]["name"] == "get_examples"
    assert msgs[5]["tool_call_id"] == msgs[4]["tool_calls"][0]["id"]
    assert llm.is_down(0)


def test_writer_bug_in_chat_is_not_fallback(parts):
    g = gemini([AttributeError("bug")])
    o = openai_llm([o_text("never")])
    res = _writer(parts, FallbackLLM(g, o)).run("Сколько сделок?", "ru", time.monotonic() + 60)
    assert res.status == "failed" and "bug" in res.reason and o_calls(o) == []


# ---- wiring ----
def test_build_llm_selects_providers():
    models = {"gemini": "g", "openai": "o"}
    both = build_llm(["gemini", "openai"], {"gemini": "k1", "openai": "k2"}, models, 5)
    assert isinstance(both, FallbackLLM)
    assert [p.name for p in both.providers] == ["gemini", "openai"]
    rev = build_llm(["openai", "gemini"], {"gemini": "k1", "openai": "k2"}, models, 5)
    assert [p.name for p in rev.providers] == ["openai", "gemini"]
    assert isinstance(build_llm(["gemini", "openai"], {"gemini": "", "openai": "k2"}, models, 5), OpenAILLM)
    assert isinstance(build_llm(["gemini", "openai"], {"gemini": "k1", "openai": ""}, models, 5), GeminiLLM)
    assert isinstance(build_llm(["gemini"], {"gemini": "k1", "openai": "k2"}, models, 5), GeminiLLM)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_llm(["gemini", "openai"], {"gemini": "", "openai": ""}, models, 5)
    with pytest.raises(ValueError, match="unknown"):
        build_llm(["claude"], {"gemini": "k"}, models, 5)


def test_fallback_chat_is_a_chat():
    chat = FallbackLLM(gemini([]), openai_llm([])).start_chat("s", [TOOL])
    assert isinstance(chat, FallbackChat) and chat.events == []
    assert LLMReply("x").text == "x"
