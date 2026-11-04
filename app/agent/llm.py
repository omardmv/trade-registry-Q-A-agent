"""LLM abstraction so the agent can be tested with scripted fakes; Gemini is the real backend."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Chat(Protocol):
    def send_text(self, text: str) -> LLMReply: ...
    def send_tool_results(self, results: list[tuple[str, dict]]) -> LLMReply: ...


class LLM(Protocol):
    def start_chat(self, system: str, tools: list[ToolSpec]) -> Chat: ...
    def complete(self, system: str, prompt: str) -> str: ...


def _plain(value):
    """Convert proto/map containers returned by the SDK into plain Python types."""
    if isinstance(value, dict) or hasattr(value, "items"):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)) or (hasattr(value, "__iter__") and not isinstance(value, (str, bytes))):
        return [_plain(v) for v in value]
    return value


class GeminiChat:
    def __init__(self, client, model: str, system: str, tools: list[ToolSpec]):
        from google.genai import types
        self._types = types
        self._client = client
        self._model = model
        self._contents: list = []
        self._config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters=t.parameters)
                for t in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _call(self) -> LLMReply:
        resp = self._client.models.generate_content(model=self._model, contents=self._contents, config=self._config)
        content = resp.candidates[0].content
        self._contents.append(content)
        parts = content.parts or []
        text = "".join(p.text for p in parts if getattr(p, "text", None))
        calls = [ToolCall(fc.name, _plain(fc.args or {})) for fc in (resp.function_calls or [])]
        return LLMReply(text, calls)

    def send_text(self, text: str) -> LLMReply:
        t = self._types
        self._contents.append(t.Content(role="user", parts=[t.Part.from_text(text=text)]))
        return self._call()

    def send_tool_results(self, results: list[tuple[str, dict]]) -> LLMReply:
        t = self._types
        parts = [t.Part.from_function_response(name=name, response=payload) for name, payload in results]
        self._contents.append(t.Content(role="user", parts=parts))
        return self._call()


class GeminiLLM:
    def __init__(self, api_key: str, model: str, timeout_s: float):
        from google import genai
        from google.genai import types
        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self._model = model

    def start_chat(self, system: str, tools: list[ToolSpec]) -> GeminiChat:
        return GeminiChat(self._client, self._model, system, tools)

    def complete(self, system: str, prompt: str) -> str:
        resp = self._client.models.generate_content(
            model=self._model, contents=prompt,
            config=self._types.GenerateContentConfig(system_instruction=system, temperature=0))
        return (resp.text or "").strip()
