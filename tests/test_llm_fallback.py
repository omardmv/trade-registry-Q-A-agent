import time

import httpx
import pytest

from app import config
from app.agent.llm import (LLMError, LLMReply, ToolCall, ToolSpec, call_with_retry, classify_error, deadline_scope,
                           is_permanent, track_providers)
from app.agent.llm_fallback import FallbackChat, FallbackLLM, build_llm
from app.agent.llm_openai import OpenAILLM
from app.agent.writer import Writer
from app.examples import ExampleStore
from app.pipeline import Pipeline
from app.resolve import Resolver
from app.semantic import load_semantic, render_schema_prompt
from tests.llm_stubs import FakeClock, Slow, o_call, o_resp, o_status, o_text, openai_llm
from tests.test_pipeline import Q, StubWriter, ok

TOOL = ToolSpec("lookup", "Look up a deal.", {"type": "OBJECT", "properties": {"deal_id": {"type": "INTEGER"}}})


def primary(outcomes):
    return openai_llm(outcomes, name="primary")


def secondary(outcomes):
    return openai_llm(outcomes, name="secondary")


def o_calls(llm):
    return llm._client.chat.completions.calls


def o_status_provider(code, message="error"):
    """The error an OpenAI-compatible provider raises for HTTP status `code`."""
    with pytest.raises(Exception) as info:
        openai_llm([o_status(code, message)]).complete("s", "p")
    return info.value


# ---- error classification ----
@pytest.mark.parametrize("code", [400, 401, 402, 403, 404, 429, 500, 503])
def test_provider_http_errors_are_fallback(code):
    assert classify_error(o_status_provider(code)) == "fallback"


def test_classify_error():
    req = httpx.Request("POST", "https://x")
    assert classify_error(TimeoutError()) == "timeout"
    assert classify_error(httpx.ReadTimeout("t", request=req)) == "timeout"
    assert classify_error(httpx.ConnectError("refused", request=req)) == "fallback"
    assert classify_error(LLMError("blocked")) == "fallback"
    assert classify_error(ValueError("x")) == "bug"
    assert classify_error(KeyError("x")) == "bug"
    assert [is_permanent(o_status_provider(c)) for c in (400, 401, 402, 403, 404, 429, 500)] == \
        [False, True, True, True, True, False, False]
    assert not is_permanent(TimeoutError()) and not is_permanent(LLMError("x"))


# ---- chat fallback ----
def test_primary_402_on_first_send_secondary_answers():
    p, s = primary([o_status(402, "credits depleted")]), secondary([o_call("lookup", deal_id=7), o_text("BK1")])
    llm = FallbackLLM(p, s)
    chat = llm.start_chat("sys", [TOOL])
    assert chat.provider == "primary"
    with track_providers() as used:
        reply = chat.send_text("find 7")
        assert reply.tool_calls == [ToolCall("lookup", {"deal_id": 7})]
        assert chat.send_tool_results([("lookup", {"broker": "BK1"})]).text == "BK1"
    assert chat.provider == "secondary"
    assert used == ["secondary", "secondary"] and chat.providers_used == ["secondary", "secondary"]
    assert len(o_calls(p)) == 1  # stays on the secondary for the rest of the conversation
    assert o_calls(s)[0]["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "find 7"}]


def test_failure_mid_conversation_replays_transcript():
    p = primary([o_call("lookup", deal_id=1), o_status(503)])
    s = secondary([o_text("answer")])
    chat = FallbackLLM(p, s).start_chat("sys", [TOOL])
    chat.send_text("q")
    assert chat.send_tool_results([("lookup", {"row": 1})]).text == "answer"
    msgs = o_calls(s)[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]
    fn = msgs[2]["tool_calls"][0]["function"]
    assert fn["name"] == "lookup" and fn["arguments"] and '"deal_id": 1' in fn["arguments"]
    assert msgs[3] == {"role": "tool", "tool_call_id": msgs[2]["tool_calls"][0]["id"], "content": '{"row": 1}'}
    assert chat.events[-2] == ("tool_results", [("lookup", {"row": 1})])
    assert chat.providers_used == ["primary", "secondary"]


def test_blocked_reply_falls_back():
    p = primary([o_resp(content="no", finish="content_filter")])
    s = secondary([o_text("ok")])
    assert FallbackLLM(p, s).start_chat("s", [TOOL]).send_text("q").text == "ok"


def test_secondary_failure_raises_no_ping_pong():
    p = primary([o_status(402)])
    s = secondary([o_status(500), o_status(500)])
    chat = FallbackLLM(p, s).start_chat("s", [TOOL])
    with pytest.raises(Exception, match="500"):
        chat.send_text("q")
    with pytest.raises(Exception, match="500"):  # switched once; the retry stays on the secondary
        chat.send_text("q")
    assert len(o_calls(p)) == 1 and len(o_calls(s)) == 2 and chat.events == []


def test_timeout_without_time_for_secondary_stays_on_primary():
    p = primary([TimeoutError("slow"), o_text("second try")])
    s = secondary([])
    chat = FallbackLLM(p, s, timeout_s=15).start_chat("s", [TOOL])
    with pytest.raises(TimeoutError):
        chat.send_text("q", deadline=time.monotonic() + 5)
    assert o_calls(s) == []
    # the caller's retry goes to the same provider
    assert call_with_retry(lambda: chat.send_text("q"), time.monotonic() + 60, 1.0).text == "second try"
    assert chat.provider == "primary" and o_calls(s) == []


def test_bug_propagates_without_fallback():
    p = primary([KeyError("bug in our code")])
    s = secondary([o_text("never")])
    chat = FallbackLLM(p, s).start_chat("s", [TOOL])
    with pytest.raises(KeyError):
        chat.send_text("q")
    assert o_calls(s) == []


# ---- circuit breaker ----
def test_circuit_breaker_routes_new_chats_then_resets():
    clock = FakeClock()
    p = primary([o_status(402), o_text("primary back")])
    s = secondary([o_text("a"), o_text("b"), o_text("c")])
    llm = FallbackLLM(p, s, cooldown_s=300, clock=clock)
    assert llm.start_chat("s", [TOOL]).send_text("q").text == "a"
    assert llm.is_down(0)
    chat2 = llm.start_chat("s", [TOOL])
    assert chat2.provider == "secondary" and chat2.send_text("q").text == "b"
    assert llm.complete("s", "p") == "c"  # completions skip the primary too
    assert len(o_calls(p)) == 1
    clock.advance(301)
    assert not llm.is_down(0)
    chat3 = llm.start_chat("s", [TOOL])
    assert chat3.provider == "primary" and chat3.send_text("q").text == "primary back"


def test_rate_limit_does_not_trip_breaker():
    llm = FallbackLLM(primary([o_status(429)]), secondary([o_text("a")]), clock=FakeClock())
    llm.start_chat("s", [TOOL]).send_text("q")
    assert not llm.is_down(0) and llm.start_chat("s", [TOOL]).provider == "primary"


# ---- complete() ----
def test_complete_falls_back():
    llm = FallbackLLM(primary([o_status(402)]), secondary([o_text("from secondary")]))
    with track_providers() as used:
        assert llm.complete("s", "p") == "from secondary"
    assert used == ["secondary"]


def test_complete_timeout_without_time_does_not_switch():
    llm = FallbackLLM(primary([TimeoutError()]), secondary([]), timeout_s=15)
    with pytest.raises(TimeoutError):
        llm.complete("s", "p", deadline=time.monotonic() + 5)


def test_pipeline_compose_falls_back_and_reports_providers():
    p = primary([o_status(402)])
    s = secondary([o_text("Заключено 9 912 сделок, без аннулированных — 9 300.")])
    r = Pipeline(StubWriter(ok([(9912, 9300)])), FallbackLLM(p, s)).ask(Q)
    assert r.source == "llm" and r.providers_used == ["secondary"]


# ---- deadline guard ----
def test_slow_primary_failure_near_deadline_skips_secondary():
    clock = FakeClock()
    p = primary([Slow(14, o_status(503), clock)])
    s = secondary([o_text("never")])
    llm = FallbackLLM(p, s, timeout_s=15, clock=clock)
    chat = llm.start_chat("s", [TOOL])
    deadline = clock() + 20
    with pytest.raises(Exception, match="503"):
        chat.send_text("q", deadline=deadline)
    assert o_calls(s) == [] and clock() <= deadline
    # same with the deadline taken from the context (writer / composer path)
    p._client.chat.completions.outcomes = [Slow(14, o_status(402), clock)]
    with deadline_scope(clock() + 20):
        with pytest.raises(Exception, match="402"):
            llm.complete("s", "p")
    assert o_calls(s) == []


def test_fast_failure_leaves_time_for_secondary():
    clock = FakeClock()
    llm = FallbackLLM(primary([Slow(0.5, o_status(402), clock)]), secondary([o_text("ok")]), timeout_s=15,
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
    p = primary([Slow(0.3, o_status(402))])
    s = secondary([o_text("never")])
    writer = _writer(parts, FallbackLLM(p, s, timeout_s=timeout), llm_timeout_s=timeout)
    t0 = time.monotonic()
    deadline = t0 + 0.6
    res = writer.run("Сколько сделок?", "ru", deadline)
    assert res.status == "failed" and "402" in res.reason
    assert o_calls(s) == [] and time.monotonic() <= deadline


# ---- the real writer with scripted providers ----
COUNT_SPEC = {"kind": "deals_aggregate", "measures": ["deal_count"],
              "filters": {"year": 2025, "seller_inns": ["975923536"]}}


def test_writer_primary_402_on_third_turn_finishes_on_secondary(parts):
    p = primary([o_call("resolve", cid="call_a", kind="company", text="Pudexiz Plast"),
                 o_call("get_examples", cid="call_b", question="x"),
                 o_status(402, "Your prepayment credits are depleted")])
    s = secondary([o_call("compile_query", cid="call_x", spec=COUNT_SPEC, assumptions=[])])
    llm = FallbackLLM(p, s, clock=FakeClock())
    with track_providers() as used:
        res = _writer(parts, llm).run("Сколько продала Pudexiz Plast в 2025?", "ru", time.monotonic() + 60)
    assert res.status == "ok" and res.path == "compiled"
    assert res.result.columns == ["deals_all", "deals_valid"]
    assert used == ["primary", "primary", "secondary"]
    msgs = o_calls(s)[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "resolve"
    assert "975923536" in msgs[3]["content"]  # the resolve result reached the secondary
    assert msgs[4]["tool_calls"][0]["function"]["name"] == "get_examples"
    assert msgs[5]["tool_call_id"] == msgs[4]["tool_calls"][0]["id"]
    assert llm.is_down(0)


def test_writer_bug_in_chat_is_not_fallback(parts):
    p = primary([AttributeError("bug")])
    s = secondary([o_text("never")])
    res = _writer(parts, FallbackLLM(p, s)).run("Сколько сделок?", "ru", time.monotonic() + 60)
    assert res.status == "failed" and "bug" in res.reason and o_calls(s) == []


# ---- wiring ----
def _settings(keys, extra=None):
    """provider_settings stand-in: two OpenAI-compatible providers (openai, backup) with the given keys, plus extra
    named specs."""
    specs = {"openai": {"type": "openai", "model": "o", "api_key": keys.get("openai", "")},
             "backup": {"type": "openai", "model": "b", "api_key": keys.get("backup", ""),
                        "base_url": "https://backup.example/v1"} if "backup" in keys else
             {"type": "openai", "model": "b", "api_key": ""}, **(extra or {})}
    return specs.get


def test_build_llm_selects_providers():
    both = build_llm(["openai", "backup"], 5, settings=_settings({"openai": "k1", "backup": "k2"}))
    assert isinstance(both, FallbackLLM)
    assert [p.name for p in both.providers] == ["openai", "backup"]
    rev = build_llm(["backup", "openai"], 5, settings=_settings({"openai": "k1", "backup": "k2"}))
    assert [p.name for p in rev.providers] == ["backup", "openai"]
    alone = build_llm(["backup", "openai"], 5, settings=_settings({"openai": "k1"}))
    assert isinstance(alone, OpenAILLM) and alone.name == "openai"
    alone = build_llm(["openai"], 5, settings=_settings({"openai": "k1", "backup": "k2"}))
    assert isinstance(alone, OpenAILLM) and alone.name == "openai"
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_llm(["openai", "backup"], 5, settings=_settings({}))
    with pytest.raises(ValueError, match="unknown"):
        build_llm(["claude"], 5, settings=_settings({"openai": "k"}))


def test_build_llm_three_named_providers_in_order_local_needs_no_key():
    local = {"qwen_local": {"type": "openai", "model": "qwen", "api_key": "", "base_url": "http://localhost:11434/v1",
                            "temperature": "0", "text_tools": True}}
    llm = build_llm(["qwen_local", "backup", "openai"], 5,
                    settings=_settings({"openai": "k1", "backup": "k2"}, local))
    assert [p.name for p in llm.providers] == ["qwen_local", "backup", "openai"]
    q = llm.providers[0]
    assert q._temperature == "0" and q._text_tools and str(q._client.base_url).startswith("http://localhost:11434")
    alone = build_llm(["qwen_local"], 5, settings=_settings({}, local))
    assert isinstance(alone, OpenAILLM) and alone.name == "qwen_local"


def test_three_provider_chain_falls_through_to_the_third():
    clock = FakeClock()
    first, second = openai_llm([o_status(503)], name="local"), openai_llm([o_status(503)], name="backup")
    third = openai_llm([o_text("third")], name="openai")
    llm = FallbackLLM(first, second, third, timeout_s=15, clock=clock)
    with track_providers() as used:
        assert llm.complete("s", "p") == "third"
    assert used == ["openai"]
    chat = FallbackLLM(openai_llm([o_status(503)], name="local"), openai_llm([o_status(503)], name="backup"),
                       openai_llm([o_text("ok")], name="openai"), timeout_s=15, clock=FakeClock()).start_chat("s", [TOOL])
    assert chat.send_text("q").text == "ok" and chat.providers_used == ["openai"]


def test_chain_never_returns_to_a_provider_it_left():
    clock = FakeClock()
    a = openai_llm([o_status(503), o_text("back on a")], name="a")
    b = openai_llm([o_text("b1"), o_status(503)], name="b")
    c = openai_llm([o_text("c")], name="c")
    chat = FallbackLLM(a, b, c, timeout_s=15, clock=clock).start_chat("s", [])
    assert chat.send_text("q1").text == "b1"
    assert chat.send_text("q2").text == "c"  # b failed: on to c, not back to a
    assert chat.providers_used == ["b", "c"]
    assert len(o_calls(a)) == 1


def test_provider_settings_builtins_and_env(monkeypatch):
    monkeypatch.delenv("QWEN_LOCAL_MODEL", raising=False)
    q = config.provider_settings("qwen_local")
    assert q["type"] == "openai" and q["base_url"].startswith("http://localhost:11434") and q["text_tools"]
    assert q["temperature"] == "0" and q["model"] == "qwen2.5-7b-uzex" and q["api_key"] == ""
    assert config.provider_settings("openai")["model"] == config.OPENAI_MODEL
    assert config.provider_settings("nope") is None
    monkeypatch.setenv("OTHERAPI_TYPE", "anthropic")
    assert config.provider_settings("otherapi") is None  # openai is the only provider type
    monkeypatch.setenv("MYVLLM_TYPE", "openai")
    monkeypatch.setenv("MYVLLM_BASE_URL", "http://gpu:8000/v1")
    monkeypatch.setenv("MYVLLM_MODEL", "qwen2.5-32b")
    v = config.provider_settings("myvllm")
    assert v["model"] == "qwen2.5-32b" and v["base_url"] == "http://gpu:8000/v1" and not v["text_tools"]
    assert v["api_key"] == ""  # never borrows OPENAI_API_KEY


def test_fallback_chat_is_a_chat():
    chat = FallbackLLM(primary([]), secondary([])).start_chat("s", [TOOL])
    assert isinstance(chat, FallbackChat) and chat.events == []
    assert LLMReply("x").text == "x"
