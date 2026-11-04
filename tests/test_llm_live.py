import pytest

from app import config
from app.agent.llm import GeminiLLM, ToolSpec

pytestmark = pytest.mark.live


@pytest.fixture
def llm():
    if not config.GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not set")
    return GeminiLLM(config.GEMINI_API_KEY, config.GEMINI_MODEL, 30)


def test_complete(llm):
    assert "4" in llm.complete("Answer with a number only.", "2+2=?")


def test_tool_call_round_trip(llm):
    tool = ToolSpec("lookup", "Look up a deal by id.",
                    {"type": "OBJECT", "properties": {"deal_id": {"type": "INTEGER"}}, "required": ["deal_id"]})
    chat = llm.start_chat("Always call the lookup tool.", [tool])
    reply = chat.send_text("Find deal 387830")
    assert reply.tool_calls and reply.tool_calls[0].name == "lookup"
    assert int(reply.tool_calls[0].args["deal_id"]) == 387830
    final = chat.send_tool_results([("lookup", {"buyer_broker": "BK106"})])
    assert "BK106" in final.text
