"""Review round 3 of the LLM fallback: timeouts, replay of empty replies, permanent errors, secrets, .env."""
import logging
import os
import time

import httpx
import pytest

from app import config
from app.agent.llm import LLMError, LLMReply, ToolSpec, classify_error, is_permanent
from app.agent.llm_fallback import FallbackLLM
from app.agent.writer import Writer
from app.config import load_dotenv
from app.examples import ExampleStore
from app.resolve import Resolver
from app.semantic import load_semantic, render_schema_prompt
from tests.llm_stubs import FakeClock, Slow, g_error, g_text, gemini, o_call, o_resp, o_status, o_text, openai_llm

TOOL = ToolSpec("lookup", "Look up a deal.", {"type": "OBJECT", "properties": {"deal_id": {"type": "INTEGER"}}})
REQ = httpx.Request("POST", "https://x")


def timeout():
    return httpx.ReadTimeout("slow", request=REQ)


def o_calls(llm):
    return llm._client.chat.completions.calls


# ---- 1. a timeout retries on the other provider if a full timeout still fits ----
def test_timeout_falls_back_to_other_provider_when_time_fits():
    clock = FakeClock()
    g, o = gemini([Slow(1, timeout(), clock)]), openai_llm([o_text("from openai")])
    chat = FallbackLLM(g, o, timeout_s=15, clock=clock).start_chat("s", [TOOL])
    assert chat.send_text("q", deadline=clock() + 60).text == "from openai"
    assert chat.provider == "openai"


def test_timeout_with_too_little_time_does_not_call_secondary():
    clock = FakeClock()
    g, o = gemini([Slow(1, timeout(), clock)]), openai_llm([o_text("never")])
    chat = FallbackLLM(g, o, timeout_s=15, clock=clock).start_chat("s", [TOOL])
    with pytest.raises(httpx.ReadTimeout):
        chat.send_text("q", deadline=clock() + 10)
    assert o_calls(o) == []


def test_complete_timeout_falls_back():
    clock = FakeClock()
    llm = FallbackLLM(gemini([timeout()]), openai_llm([o_text("ok")]), timeout_s=15, clock=clock)
    assert llm.complete("s", "p", deadline=clock() + 60) == "ok"


# ---- 2. replay of an empty assistant reply ----
EMPTY = [("user_text", "q"), ("assistant", LLMReply("", [])), ("user_text", "again")]


def test_openai_replay_skips_empty_assistant_reply():
    chat = openai_llm([]).start_chat("s", [TOOL])
    chat.load_history(EMPTY)
    assert [m["role"] for m in chat._messages] == ["system", "user", "user"]


def test_gemini_replay_never_emits_empty_parts():
    chat = gemini([]).start_chat("s", [TOOL])
    chat.load_history(EMPTY)
    assert chat._contents and all(c.parts for c in chat._contents)


# ---- 3. permanent errors ----
def test_gemini_invalid_key_and_unknown_model_are_permanent():
    assert is_permanent(g_error(400, "API key not valid. Please pass a valid API key."))
    assert is_permanent(g_error(400, "x API_KEY_INVALID x"))
    assert is_permanent(g_error(404, "models/foo is not found"))
    assert not is_permanent(g_error(400, "Request contains an invalid argument"))


@pytest.mark.parametrize("code", [400, 404])
def test_400_404_failures_are_logged_at_error_level(code, caplog):
    llm = FallbackLLM(gemini([g_error(code, "malformed")]), openai_llm([o_text("ok")]))
    with caplog.at_level(logging.INFO):
        llm.complete("s", "p")
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_invalid_key_trips_breaker():
    llm = FallbackLLM(gemini([g_error(400, "API key not valid")]), openai_llm([o_text("ok")]), clock=FakeClock())
    llm.complete("s", "p")
    assert llm.is_down(0)


# ---- 4/5. OpenAI request and truncation ----
def test_openai_passes_reasoning_effort(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_REASONING_EFFORT", "low")
    llm = openai_llm([o_text("a"), o_text("b")])
    llm.start_chat("s", [TOOL]).send_text("q")
    llm.complete("s", "p")
    assert [c["reasoning_effort"] for c in o_calls(llm)] == ["low", "low"]


def test_openai_omits_empty_reasoning_effort(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_REASONING_EFFORT", "")
    llm = openai_llm([o_text("a")])
    llm.start_chat("s", [TOOL]).send_text("q")
    assert "reasoning_effort" not in o_calls(llm)[0]


def test_openai_length_finish_raises_llm_error():
    llm = openai_llm([o_resp(content="cut off", finish="length"), o_resp(content="cut off", finish="length")])
    with pytest.raises(LLMError, match="length"):
        llm.start_chat("s", [TOOL]).send_text("q")
    with pytest.raises(LLMError, match="length"):
        llm.complete("s", "p")


# ---- 6. .env loader ----
def _load(tmp_path, monkeypatch, text, keys):
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    load_dotenv(env)
    out = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    return out


def test_dotenv_export_prefix_any_whitespace(tmp_path, monkeypatch):
    out = _load(tmp_path, monkeypatch, "export   UZEX_E1=a\nexport\tUZEX_E2=b\n  export UZEX_E3 = c\n",
                ["UZEX_E1", "UZEX_E2", "UZEX_E3"])
    assert out == {"UZEX_E1": "a", "UZEX_E2": "b", "UZEX_E3": "c"}


def test_dotenv_strips_unquoted_trailing_comment(tmp_path, monkeypatch):
    out = _load(tmp_path, monkeypatch, "UZEX_C1=abc # note\nUZEX_C2=a#b\nUZEX_C3=\t# only comment\n",
                ["UZEX_C1", "UZEX_C2", "UZEX_C3"])
    assert out == {"UZEX_C1": "abc", "UZEX_C2": "a#b", "UZEX_C3": ""}


def test_dotenv_quotes(tmp_path, monkeypatch):
    out = _load(tmp_path, monkeypatch,
                'UZEX_Q1="a # b" # c\nUZEX_Q2=\'x # y\'\nUZEX_Q3="unterminated\nUZEX_Q4=\'bad"\n',
                ["UZEX_Q1", "UZEX_Q2", "UZEX_Q3", "UZEX_Q4"])
    assert out["UZEX_Q1"] == "a # b" and out["UZEX_Q2"] == "x # y"
    assert out["UZEX_Q3"] is None and out["UZEX_Q4"] is None


# ---- 7. internal consistency errors are bugs ----
def test_tool_result_mismatch_is_runtime_error_not_llm_error():
    chat = openai_llm([o_call("resolve", cid="c1")]).start_chat("s", [TOOL])
    chat.send_text("q")
    with pytest.raises(RuntimeError) as info:
        chat.send_tool_results([("other", {}), ("x", {})])
    assert not isinstance(info.value, LLMError) and classify_error(info.value) == "bug"
    with pytest.raises(RuntimeError) as info:
        openai_llm([]).start_chat("s", [TOOL]).send_tool_results([("a", {})])
    assert not isinstance(info.value, LLMError)


# ---- 8. no key material in logs or reasons ----
SECRET = "Incorrect API key provided: sk-proj-abcdEFGH...abcd"


@pytest.mark.parametrize("code", [401, 403])
def test_auth_error_text_never_logged(code, caplog):
    for primary, secondary in [(openai_llm([o_status(code, SECRET)]), gemini([g_text("ok")])),
                               (gemini([g_error(code, SECRET)]), openai_llm([o_text("ok")]))]:
        llm = FallbackLLM(primary, secondary)
        with caplog.at_level(logging.DEBUG):
            llm.complete("s", "p")
    assert "sk-" not in caplog.text and "abcd" not in caplog.text
    assert str(code) in caplog.text


def test_writer_reason_has_no_secret(db, caplog):
    ctx = (db, Resolver(db), ExampleStore.from_yaml(config.EXAMPLES_PATH), render_schema_prompt(load_semantic()))
    for llm in (openai_llm([o_status(401, SECRET)]), gemini([g_error(403, SECRET)])):
        res = Writer(llm, *ctx).run("Сколько сделок?", "ru", time.monotonic() + 60)
        assert res.status == "failed" and "sk-" not in res.reason and "abcd" not in res.reason
        assert "40" in res.reason
    assert "sk-" not in caplog.text


# ---- 9. last_provider is gone ----
def test_no_last_provider_attribute():
    assert not hasattr(FallbackLLM(gemini([]), openai_llm([])), "last_provider")


# ---- 10. do not switch to a provider marked down (unless nothing healthier is left) ----
def test_does_not_switch_to_down_provider_when_start_is_healthy():
    clock = FakeClock()
    llm = FallbackLLM(gemini([g_error(503)]), openai_llm([o_text("never")]), clock=clock)
    llm._down_until[1] = clock() + 100
    with pytest.raises(Exception, match="503"):
        llm.complete("s", "p")
    assert o_calls(llm.providers[1]) == []


def test_switches_to_down_provider_when_both_down():
    clock = FakeClock()
    llm = FallbackLLM(gemini([g_error(503)]), openai_llm([o_text("last resort")]), clock=clock)
    llm._down_until = [clock() + 100, clock() + 100]
    assert llm.complete("s", "p") == "last resort"
