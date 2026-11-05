"""Every planner's tools must be accepted by OpenAI-compatible servers (JSON Schema, native tools or tools listed
in the prompt). Offline: schemas only."""
import json

import pytest

from app.agent.llm_openai import _tool_payload
from app.agent.writer import TOOLS as LEGACY_TOOLS
from app.plan.prompt import TOOLS as GRAMMAR_TOOLS

PLANNERS = {"legacy": LEGACY_TOOLS, "grammar": GRAMMAR_TOOLS}


def _walk(schema, path="$"):
    yield path, schema
    for name, sub in (schema.get("properties") or {}).items():
        yield from _walk(sub, f"{path}.{name}")
    if "items" in schema:
        yield from _walk(schema["items"], f"{path}[]")


@pytest.mark.parametrize("planner", PLANNERS)
def test_openai_tool_schemas_are_plain_json_schema(planner):
    for tool in _tool_payload(PLANNERS[planner]):
        params = tool["function"]["parameters"]
        json.dumps(params)
        for path, node in _walk(params):
            assert node.get("type") in ("object", "string", "array", "integer", "boolean", "number"), path
            if node["type"] == "array":
                assert "items" in node, f"{path}: arrays need items (OpenAI rejects them otherwise)"
            for req in node.get("required") or []:
                assert req in (node.get("properties") or {}), f"{path}: required {req} is not a property"
