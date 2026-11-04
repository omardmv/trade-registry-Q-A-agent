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
