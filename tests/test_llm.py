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
    llm = GeminiLLM("dummy-key", "gemini-2.5-flash", 5)
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
