from app.agent.llm import GeminiChat, GeminiLLM, ToolSpec, _plain

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "measures": {"type": "ARRAY", "items": {"type": "STRING", "enum": ["volume", "amount"]}},
        "filter": {"type": "OBJECT", "properties": {"buyer": {"type": "STRING"}, "n": {"type": "INTEGER"}}},
        "flag": {"type": "BOOLEAN"},
    },
    "required": ["measures"],
}


class MapLike:
    def __init__(self, d):
        self._d = d

    def items(self):
        return self._d.items()


def test_plain_converts_nested():
    v = _plain(MapLike({"a": (1, 2), "b": MapLike({"c": ["x", MapLike({"d": 1})]}), "s": "str"}))
    assert v == {"a": [1, 2], "b": {"c": ["x", {"d": 1}]}, "s": "str"}
    assert type(v) is dict and type(v["a"]) is list
    assert _plain("abc") == "abc"
    assert _plain(5) == 5


def test_gemini_config_builds_offline():
    llm = GeminiLLM("dummy-key", "gemini-3.5-flash", 5)
    chat = llm.start_chat("sys", [ToolSpec("t", "desc", SCHEMA)])
    assert isinstance(chat, GeminiChat)
    decl = chat._config.tools[0].function_declarations[0]
    assert decl.name == "t"
    assert decl.parameters.properties["filter"].properties["n"].type == "INTEGER"
    assert decl.parameters.properties["measures"].items.enum == ["volume", "amount"]
    assert chat._config.automatic_function_calling.disable is True


# ---- error handling / history consistency ----
import pytest
from google.genai import types

from app.agent.llm import LLMError


class StubModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(list(contents) if isinstance(contents, list) else contents)
        o = self.outcomes.pop(0)
        if isinstance(o, BaseException):
            raise o
        return o


class StubClient:
    def __init__(self, outcomes):
        self.models = StubModels(outcomes)


def _chat(outcomes):
    return GeminiChat(StubClient(outcomes), "m", "sys", [ToolSpec("t", "d", SCHEMA)])


def _resp(parts, finish=None):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=parts), finish_reason=finish)])


def test_function_call_reply_appends_model_content_unchanged():
    parts = [types.Part(function_call=types.FunctionCall(name="t", args={"measures": ["volume"]}),
                        thought_signature=b"sig")]
    r = _resp(parts)
    chat = _chat([r])
    reply = chat.send_text("hi")
    assert [(c.name, c.args) for c in reply.tool_calls] == [("t", {"measures": ["volume"]})]
    assert len(chat._contents) == 2
    assert chat._contents[1] is r.candidates[0].content
    assert chat._contents[1].parts[0].thought_signature == b"sig"


def test_empty_candidates_raises_and_history_unchanged():
    chat = _chat([types.GenerateContentResponse(candidates=[])])
    with pytest.raises(LLMError):
        chat.send_text("hi")
    assert chat._contents == []


def test_content_none_safety_raises_and_history_unchanged():
    r = types.GenerateContentResponse(candidates=[types.Candidate(
        content=None, finish_reason=types.FinishReason.SAFETY)])
    chat = _chat([r])
    with pytest.raises(LLMError, match="SAFETY"):
        chat.send_text("hi")
    assert chat._contents == []


def test_timeout_propagates_history_unchanged_then_recovers():
    chat = _chat([TimeoutError("boom"), _resp([types.Part.from_text(text="ok")])])
    with pytest.raises(TimeoutError):
        chat.send_text("hi")
    assert chat._contents == []
    assert chat.send_text("again").text == "ok"
    assert len(chat._contents) == 2


def test_tool_results_failure_leaves_history_unchanged():
    chat = _chat([_resp([types.Part.from_text(text="a")]), TimeoutError()])
    chat.send_text("q")
    with pytest.raises(TimeoutError):
        chat.send_tool_results([("t", {"x": 1})])
    assert len(chat._contents) == 2


def test_complete_blocked_raises():
    llm = GeminiLLM("k", "m", 5)
    llm._client = StubClient([types.GenerateContentResponse(candidates=[types.Candidate(
        content=None, finish_reason=types.FinishReason.SAFETY)])])
    with pytest.raises(LLMError):
        llm.complete("s", "p")


def test_plain_leaves_pydantic_and_generators_alone():
    p = types.Part.from_text(text="x")
    assert _plain(p) is p
    g = (i for i in range(3))
    assert _plain(g) is g


def test_function_response_accepts_floats_none_nested():
    part = types.Part.from_function_response(name="t", response={"a": 1.5, "b": None, "c": [[1, 2.5], [None]]})
    assert part.function_response.response["c"] == [[1, 2.5], [None]]


# ---- thinking budget and transient retry ----
import time

from google.genai import errors

from app.agent.llm import call_with_retry, is_transient


def test_thinking_budget_in_both_configs(monkeypatch):
    from app import config
    assert config.GEMINI_THINKING_BUDGET == 1024
    llm = GeminiLLM("dummy-key", "gemini-3.5-flash", 5)
    chat = llm.start_chat("sys", [ToolSpec("t", "desc", SCHEMA)])
    assert chat._config.thinking_config.thinking_budget == 1024
    seen = {}

    class M:
        def generate_content(self, model, contents, config):
            seen["cfg"] = config
            raise LLMError("stop")

    llm._client = type("C", (), {"models": M()})()
    with pytest.raises(LLMError):
        llm.complete("s", "p")
    assert seen["cfg"].thinking_config.thinking_budget == 1024


def test_is_transient():
    assert is_transient(errors.ServerError(503, {}))
    assert is_transient(errors.ClientError(429, {}))
    assert is_transient(errors.ServerError(500, {}))
    assert is_transient(TimeoutError())
    assert not is_transient(errors.ClientError(400, {}))
    assert not is_transient(LLMError("blocked"))
    assert not is_transient(ValueError("x"))


def test_call_with_retry_once_when_time_fits():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise errors.ServerError(503, {})
        return "ok"

    assert call_with_retry(fn, time.monotonic() + 10, 1.0) == "ok" and len(calls) == 2


def test_call_with_retry_not_when_no_time_or_not_transient():
    calls = []

    def fn():
        calls.append(1)
        raise errors.ServerError(503, {})

    with pytest.raises(errors.ServerError):
        call_with_retry(fn, time.monotonic() + 0.1, 5.0)
    assert len(calls) == 1
    calls.clear()

    def bad():
        calls.append(1)
        raise ValueError("x")

    with pytest.raises(ValueError):
        call_with_retry(bad, time.monotonic() + 10, 1.0)
    assert len(calls) == 1
    calls.clear()

    def twice():
        calls.append(1)
        raise TimeoutError()

    with pytest.raises(TimeoutError):  # at most one retry
        call_with_retry(twice, time.monotonic() + 10, 1.0)
    assert len(calls) == 2


def test_429_waits_one_second_only_if_it_fits(monkeypatch):
    import app.agent.llm as m
    slept = []
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))

    def make():
        calls = []

        def fn():
            calls.append(1)
            if len(calls) == 1:
                raise errors.ClientError(429, {})
            return "ok"
        return fn, calls

    fn, calls = make()
    assert call_with_retry(fn, time.monotonic() + 10, 2.0) == "ok" and slept == [1.0] and len(calls) == 2
    slept.clear()
    fn, calls = make()
    with pytest.raises(errors.ClientError):  # 2.0 + 1 s does not fit in 2.5 s
        call_with_retry(fn, time.monotonic() + 2.5, 2.0)
    assert slept == [] and len(calls) == 1
    slept.clear()

    def srv():
        calls.append(1)
        if len(calls) == 1:
            raise errors.ServerError(503, {})
        return "ok"

    calls.clear()
    assert call_with_retry(srv, time.monotonic() + 10, 2.0) == "ok" and slept == []
