"""Tool calls through the prompt (OPENAI_TEXT_TOOLS) for local servers whose tool parser drops calls."""
import pytest

from app import config
from app.agent.llm import EmptyReplyError, LLMReply, ToolCall, ToolSpec
from app.agent.llm_openai import TextToolChat, _repair_json
from tests.llm_stubs import o_text, openai_llm

TOOL = ToolSpec("run_plan", "Run a plan.", {"type": "OBJECT", "properties": {"subject": {"type": "STRING"}}})


@pytest.fixture
def text_tools(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_TEXT_TOOLS", True)


def calls(llm):
    return llm._client.chat.completions.calls


def test_tool_call_parsed_from_text_and_tools_listed_in_the_prompt(text_tools):
    llm = openai_llm([o_text('<tool_call>\n{"name": "run_plan", "arguments": {"subject": "deals"}}\n</tool_call>'),
                      o_text("done")])
    chat = llm.start_chat("SYS", [TOOL])
    assert isinstance(chat, TextToolChat)
    reply = chat.send_text("q")
    assert reply.tool_calls == [ToolCall("run_plan", {"subject": "deals"})] and reply.text == ""
    sent = calls(llm)[0]
    assert "tools" not in sent and '"run_plan"' in sent["messages"][0]["content"]
    chat.send_tool_results([("run_plan", {"rows": 1})])
    last = calls(llm)[1]["messages"][-1]
    assert last["role"] == "user" and "<tool_response>" in last["content"] and '"rows": 1' in last["content"]


def test_missing_closing_brace_is_repaired(text_tools):
    raw = '<tool_call>\n{"name": "run_plan", "arguments": {"subject": "deals", "f": [1, 2]}\n</tool_call>'
    reply = openai_llm([o_text(raw)]).start_chat("S", [TOOL]).send_text("q")
    assert reply.tool_calls == [ToolCall("run_plan", {"subject": "deals", "f": [1, 2]})]
    assert _repair_json('{"a": "x}"') == {"a": "x}"}


def test_empty_text_reply_raises_empty_reply(text_tools):
    with pytest.raises(EmptyReplyError):
        openai_llm([o_text("  ")]).start_chat("S", [TOOL]).send_text("q")


def test_off_by_default_uses_api_tools(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_TEXT_TOOLS", False)
    llm = openai_llm([o_text("x")])
    chat = llm.start_chat("S", [TOOL])
    assert not isinstance(chat, TextToolChat)
    chat.send_text("q")
    assert "tools" in calls(llm)[0]


def test_history_replay(text_tools):
    llm = openai_llm([o_text("ok")])
    chat = llm.start_chat("S", [TOOL])
    chat.load_history([("user_text", "q"), ("assistant", LLMReply("", [ToolCall("run_plan", {"subject": "deals"})])),
                       ("tool_results", [("run_plan", {"error": "x"})])])
    chat.send_text("again")
    msgs = calls(llm)[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user", "user"]
    assert "<tool_call>" in msgs[2]["content"] and "<tool_response>" in msgs[3]["content"]


def test_bare_json_call_without_tags(text_tools):
    raw = '```json\n{"name": "run_plan", "arguments": {"subject": "deals"}}\n```'
    reply = openai_llm([o_text(raw)]).start_chat("S", [TOOL]).send_text("q")
    assert reply.tool_calls == [ToolCall("run_plan", {"subject": "deals"})] and reply.text == ""
