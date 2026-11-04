import json

import pytest

from app.agent.llm import LLMError, LLMReply, ProviderError, ToolCall, ToolSpec, classify_error, is_transient
from app.agent.llm_openai import to_json_schema
from app.agent.writer import TOOLS
from tests.llm_stubs import o_call, o_connection, o_resp, o_status, o_text, o_timeout, openai_llm

TOOL = ToolSpec("lookup", "Look up a deal.", {"type": "OBJECT", "properties": {"deal_id": {"type": "INTEGER"}},
                                              "required": ["deal_id"]})


def _walk(schema):
    yield schema
    for sub in (schema.get("properties") or {}).values():
        yield from _walk(sub)
    if "items" in schema:
        yield from _walk(schema["items"])


def test_schema_conversion_nested():
    src = {"type": "OBJECT", "description": "d", "properties": {
        "a": {"type": "ARRAY", "items": {"type": "STRING", "enum": ["x", "y"]}},
        "b": {"type": "OBJECT", "properties": {"n": {"type": "INTEGER"}, "f": {"type": "NUMBER"},
                                               "ok": {"type": "BOOLEAN"}}}},
           "required": ["a"]}
    out = to_json_schema(src)
    assert out == {"type": "object", "description": "d", "properties": {
        "a": {"type": "array", "items": {"type": "string", "enum": ["x", "y"]}},
        "b": {"type": "object", "properties": {"n": {"type": "integer"}, "f": {"type": "number"},
                                               "ok": {"type": "boolean"}}}},
                   "required": ["a"]}
    assert src["type"] == "OBJECT"  # input untouched


def test_schema_conversion_of_writer_tools():
    for tool in TOOLS:
        out = to_json_schema(tool.parameters)
        nodes = list(_walk(out))
        assert all(n["type"] in {"object", "string", "array", "integer", "boolean", "number"} for n in nodes)
        assert set(out.get("required", [])) <= set(out["properties"])
        json.dumps(out)  # serializable
    spec = to_json_schema(next(t for t in TOOLS if t.name == "compile_query").parameters)["properties"]["spec"]
    assert spec["properties"]["kind"]["enum"][0] == "deals_aggregate"
    assert spec["properties"]["filters"]["properties"]["statuses"]["items"]["type"] == "string"


def test_tools_payload_and_round_trip_ids():
    llm = openai_llm([o_resp(tool_calls=[("call_a", "lookup", {"deal_id": 1}), ("call_b", "lookup", {"deal_id": 2})]),
                      o_text("done")])
    chat = llm.start_chat("sys", [TOOL])
    reply = chat.send_text("find 1 and 2")
    assert [(c.name, c.args) for c in reply.tool_calls] == [("lookup", {"deal_id": 1}), ("lookup", {"deal_id": 2})]
    first = llm._client.chat.completions.calls[0]
    assert first["tool_choice"] == "auto"
    assert first["tools"][0] == {"type": "function", "function": {
        "name": "lookup", "description": "Look up a deal.",
        "parameters": {"type": "object", "properties": {"deal_id": {"type": "integer"}}, "required": ["deal_id"]}}}
    assert first["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "find 1 and 2"}]
    assert chat.send_tool_results([("lookup", {"r": 1}), ("lookup", {"r": "ü"})]).text == "done"
    msgs = llm._client.chat.completions.calls[1]["messages"]
    assert msgs[2]["role"] == "assistant" and [c["id"] for c in msgs[2]["tool_calls"]] == ["call_a", "call_b"]
    assert msgs[3:] == [{"role": "tool", "tool_call_id": "call_a", "content": '{"r": 1}'},
                        {"role": "tool", "tool_call_id": "call_b", "content": '{"r": "ü"}'}]


def test_tool_results_mapped_by_name_when_counts_differ():
    llm = openai_llm([o_resp(tool_calls=[("c1", "resolve", {}), ("c2", "get_examples", {})]), o_text("ok")])
    chat = llm.start_chat("s", [TOOL])
    chat.send_text("q")
    chat.send_tool_results([("get_examples", {"e": 1})])
    tool_msgs = [m for m in llm._client.chat.completions.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs == [{"role": "tool", "tool_call_id": "c2", "content": '{"e": 1}'},
                         {"role": "tool", "tool_call_id": "c1", "content": '{"error": "not executed"}'}]


def test_tool_results_unmatched_name_raises():
    llm = openai_llm([o_call("resolve", cid="c1")])
    chat = llm.start_chat("s", [TOOL])
    chat.send_text("q")
    with pytest.raises(RuntimeError, match="matches no pending"):
        chat.send_tool_results([("other", {}), ("x", {})])
    assert len(chat._messages) == 3  # nothing committed


def test_invalid_json_arguments_raise_and_history_unchanged():
    llm = openai_llm([o_resp(tool_calls=[("c1", "lookup", "{not json")])])
    chat = llm.start_chat("s", [TOOL])
    with pytest.raises(LLMError, match="invalid JSON"):
        chat.send_text("q")
    assert len(chat._messages) == 1
    assert classify_error(LLMError("x")) == "fallback"


def test_non_object_arguments_raise():
    chat = openai_llm([o_resp(tool_calls=[("c1", "lookup", "[1]")])]).start_chat("s", [TOOL])
    with pytest.raises(LLMError, match="non-object"):
        chat.send_text("q")


def test_content_filter_and_empty_raise():
    chat = openai_llm([o_resp(content="partial", finish="content_filter"), o_resp(content="")]).start_chat("s", [TOOL])
    with pytest.raises(LLMError, match="content_filter"):
        chat.send_text("q")
    with pytest.raises(LLMError, match="empty"):
        chat.send_text("q")
    assert len(chat._messages) == 1


def test_complete():
    llm = openai_llm([o_text("  answer  "), o_text(""), o_resp(content="x", finish="content_filter")])
    assert llm.complete("sys", "p") == "answer"
    assert llm._client.chat.completions.calls[0]["messages"] == [{"role": "system", "content": "sys"},
                                                                  {"role": "user", "content": "p"}]
    assert "tools" not in llm._client.chat.completions.calls[0]
    with pytest.raises(LLMError):
        llm.complete("s", "p")
    with pytest.raises(LLMError):
        llm.complete("s", "p")


@pytest.mark.parametrize("exc,transient,status", [
    (o_status(402), False, 402), (o_status(401), False, 401), (o_status(403), False, 403),
    (o_status(429), True, 429), (o_status(500), True, 500), (o_status(503), True, 503),
    (o_connection(), True, None)])
def test_exception_mapping(exc, transient, status):
    llm = openai_llm([exc])
    with pytest.raises(ProviderError) as info:
        llm.complete("s", "p")
    assert info.value.transient is transient and info.value.status == status
    assert classify_error(info.value) == "fallback"
    assert is_transient(info.value) is transient


def test_timeout_maps_to_timeout_error():
    chat = openai_llm([o_timeout()]).start_chat("s", [TOOL])
    with pytest.raises(TimeoutError) as info:
        chat.send_text("q")
    assert classify_error(info.value) == "timeout" and is_transient(info.value)


def test_load_history_builds_native_messages():
    llm = openai_llm([o_text("final")])
    chat = llm.start_chat("sys", [TOOL])
    chat.load_history([
        ("user_text", "q"),
        ("assistant", LLMReply("", [ToolCall("resolve", {"text": "Ц"}), ToolCall("get_examples", {})])),
        ("tool_results", [("resolve", {"c": 1}), ("get_examples", {"e": []})]),
        ("assistant", LLMReply("thinking", [ToolCall("explore_sql", {"sql": "SELECT 1"})])),
    ])
    assert chat.send_tool_results([("explore_sql", {"rows": []})]).text == "final"
    msgs = llm._client.chat.completions.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool", "assistant", "tool"]
    ids = [c["id"] for c in msgs[2]["tool_calls"]]
    assert len(set(ids)) == 2 and [m["tool_call_id"] for m in msgs[3:5]] == ids
    assert json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"text": "Ц"}
    assert msgs[5]["content"] == "thinking" and msgs[6]["tool_call_id"] == msgs[5]["tool_calls"][0]["id"]
    assert msgs[6]["tool_call_id"] not in ids
