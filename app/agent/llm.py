"""LLM abstraction so the agent can be tested with scripted fakes; Gemini is the real backend."""
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel


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


class LLMError(RuntimeError):
    """The model returned no usable content (blocked, empty, or malformed response)."""


def _plain(value):
    """Convert SDK map/sequence containers into plain dict/list; leave everything else as is."""
    if isinstance(value, (str, bytes, BaseModel)):
        return value
    if isinstance(value, dict) or hasattr(value, "items"):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)) or isinstance(value, Sequence):
        return [_plain(v) for v in value]
    return value


def _block_reason(resp) -> str:
    bits = []
    pf = getattr(resp, "prompt_feedback", None)
    if pf is not None and getattr(pf, "block_reason", None):
        bits.append(f"prompt_feedback.block_reason={getattr(pf.block_reason, 'name', pf.block_reason)}")
    cands = getattr(resp, "candidates", None) or []
    if cands and getattr(cands[0], "finish_reason", None):
        fr = cands[0].finish_reason
        bits.append(f"finish_reason={getattr(fr, 'name', fr)}")
    return ", ".join(bits) or "no details"


def _content_of(resp):
    cands = getattr(resp, "candidates", None)
    if not cands or cands[0].content is None:
        raise LLMError(f"Gemini returned no content ({_block_reason(resp)})")
    return cands[0].content


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

    def _call(self, user_content) -> LLMReply:
        candidate = [*self._contents, user_content]
        resp = self._client.models.generate_content(model=self._model, contents=candidate, config=self._config)
        content = _content_of(resp)
        self._contents = [*candidate, content]  # commit only after a successful response
        parts = content.parts or []
        text = "".join(p.text for p in parts if getattr(p, "text", None))
        calls = [ToolCall(fc.name, _plain(fc.args or {})) for fc in (resp.function_calls or [])]
        return LLMReply(text, calls)

    def send_text(self, text: str) -> LLMReply:
        t = self._types
        return self._call(t.Content(role="user", parts=[t.Part.from_text(text=text)]))

    def send_tool_results(self, results: list[tuple[str, dict]]) -> LLMReply:
        t = self._types
        parts = [t.Part.from_function_response(name=name, response=payload) for name, payload in results]
        return self._call(t.Content(role="user", parts=parts))


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
        content = _content_of(resp)
        text = "".join(p.text for p in (content.parts or []) if getattr(p, "text", None) and not getattr(p, "thought", None))
        if not text.strip():
            raise LLMError(f"Gemini returned empty text ({_block_reason(resp)})")
        return text.strip()
