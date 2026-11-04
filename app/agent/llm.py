"""LLM abstraction so the agent can be tested with scripted fakes; Gemini is the real backend."""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from collections.abc import Callable, Iterator, Sequence
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from app import config

T = TypeVar("T")
_TRANSIENT_STATUS = {429, 500, 503}


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


class ProviderError(RuntimeError):
    """The provider refused or failed the call (HTTP status, connection error). transient=True: worth a retry."""

    def __init__(self, message: str, *, transient: bool, status: int | None = None):
        super().__init__(message)
        self.transient, self.status = transient, status

    @property
    def code(self) -> int | None:  # same attribute name as google.genai errors (call_with_retry reads it)
        return self.status


# ---- per-request context: the deadline for fallback attempts, and which providers answered ----
_DEADLINE: ContextVar[float | None] = ContextVar("llm_deadline", default=None)
_PROVIDERS: ContextVar[list[str] | None] = ContextVar("llm_providers", default=None)


@contextmanager
def deadline_scope(deadline: float | None) -> Iterator[None]:
    """LLM calls made inside may only start a fallback attempt that fits before this monotonic deadline."""
    token = _DEADLINE.set(deadline)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def current_deadline() -> float | None:
    return _DEADLINE.get()


@contextmanager
def track_providers() -> Iterator[list[str]]:
    """Collect the name of the provider of every successful LLM reply made inside (in order)."""
    used: list[str] = []
    token = _PROVIDERS.set(used)
    try:
        yield used
    finally:
        _PROVIDERS.reset(token)


def note_provider(name: str) -> None:
    used = _PROVIDERS.get()
    if used is not None:
        used.append(name)


_TIMEOUT_NAMES = {"ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException",
                  "APITimeoutError"}
_CONNECTION_NAMES = {"ConnectError", "ReadError", "WriteError", "RemoteProtocolError", "NetworkError",
                     "ProxyError", "APIConnectionError"}
_PERMANENT_STATUS = {401, 402, 403, 404}  # 404: unknown model
_AUTH_STATUS = {401, 403}
_BAD_KEY_MARKERS = ("api key not valid", "api_key_invalid")


def error_status(exc: BaseException) -> int | None:
    """HTTP status of a provider error (ProviderError, google.genai APIError, openai APIStatusError)."""
    if isinstance(exc, ProviderError):
        return exc.status
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _is_genai_error(exc: BaseException) -> bool:
    try:
        from google.genai import errors
    except ImportError:
        return False
    return isinstance(exc, errors.APIError)


def _is_openai_error(exc: BaseException) -> bool:
    try:
        import openai
    except ImportError:
        return False
    return isinstance(exc, openai.APIError)


def classify_error(exc: BaseException) -> Literal["fallback", "timeout", "bug"]:
    """fallback: the provider failed (HTTP status, connection error, blocked/empty reply) -> try another provider.
    timeout: the call ran out of time -> no switch within that call. bug: anything else -> propagate."""
    if isinstance(exc, TimeoutError) or type(exc).__name__ in _TIMEOUT_NAMES:
        return "timeout"
    if isinstance(exc, (ProviderError, LLMError)) or _is_genai_error(exc) or _is_openai_error(exc):
        return "fallback"
    if type(exc).__name__ in _CONNECTION_NAMES:
        return "fallback"
    return "bug"


def is_permanent(exc: BaseException) -> bool:
    """Credits depleted / bad key / forbidden / unknown model: the provider will keep failing for a while
    (circuit breaker). Gemini reports an invalid key as HTTP 400 "API key not valid" (API_KEY_INVALID)."""
    if classify_error(exc) != "fallback":
        return False
    status = error_status(exc)
    if status in _PERMANENT_STATUS:
        return True
    return status == 400 and any(m in str(exc).lower() for m in _BAD_KEY_MARKERS)


def safe_text(exc: BaseException) -> str:
    """Text of an error that is safe to log or store. Auth errors (401/403) may echo key material in their
    message: only the status code and the error type are kept. (ProviderError messages are built safe.)"""
    status = error_status(exc)
    if status in _AUTH_STATUS and not isinstance(exc, ProviderError):
        return f"HTTP {status} {type(exc).__name__}"
    return str(exc)


def is_transient(exc: BaseException) -> bool:
    """HTTP 429/500/503 from the Gemini API, a transient ProviderError, or a timeout."""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, ProviderError):
        return exc.transient
    try:
        from google.genai import errors
        if isinstance(exc, errors.APIError) and getattr(exc, "code", None) in _TRANSIENT_STATUS:
            return True
    except ImportError:
        pass
    return type(exc).__name__ in {"ReadTimeout", "ConnectTimeout", "TimeoutException"}


def call_with_retry(fn: Callable[[], T], deadline: float, timeout_s: float) -> T:
    """Call fn; on a transient error retry once, but only if a full timeout_s still fits before deadline
    (HTTP 429: after a 1 s pause, which must fit too)."""
    try:
        return fn()
    except Exception as e:
        if not is_transient(e):
            raise
        rate_limited = getattr(e, "code", None) == 429
        if deadline - time.monotonic() < timeout_s + (1.0 if rate_limited else 0.0):
            raise
        if rate_limited:
            time.sleep(1.0)
    return fn()


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
    name = "gemini"

    def __init__(self, client, model: str, system: str, tools: list[ToolSpec],
                 thinking_budget: int = config.GEMINI_THINKING_BUDGET):
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
            thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
        )

    def _call(self, user_content) -> LLMReply:
        candidate = [*self._contents, user_content]
        resp = self._client.models.generate_content(model=self._model, contents=candidate, config=self._config)
        content = _content_of(resp)
        self._contents = [*candidate, content]  # commit only after a successful response
        parts = content.parts or []
        text = "".join(p.text for p in parts if getattr(p, "text", None))
        calls = [ToolCall(fc.name, _plain(fc.args or {})) for fc in (resp.function_calls or [])]
        note_provider(self.name)
        return LLMReply(text, calls)

    def load_history(self, events: list[tuple[str, object]]) -> None:
        """Rebuild the native history from a provider-neutral transcript (see app.agent.llm_fallback).
        Replayed function calls carry no real thought signature, so they get Gemini's documented bypass value."""
        t = self._types
        contents = []
        for kind, payload in events:
            if kind == "user_text":
                contents.append(t.Content(role="user", parts=[t.Part.from_text(text=payload)]))
            elif kind == "tool_results":
                contents.append(t.Content(role="user", parts=[
                    t.Part.from_function_response(name=name, response=data) for name, data in payload]))
            elif kind == "assistant":
                parts = [t.Part.from_text(text=payload.text)] if payload.text else []
                parts += [t.Part(function_call=t.FunctionCall(name=c.name, args=c.args),
                                 thought_signature=b"skip_thought_signature_validator")
                          for c in payload.tool_calls]
                if parts:  # Gemini rejects Content with empty parts; an empty reply is simply not replayed
                    contents.append(t.Content(role="model", parts=parts))
            else:
                raise ValueError(f"unknown transcript event {kind!r}")
        self._contents = contents

    def send_text(self, text: str) -> LLMReply:
        t = self._types
        return self._call(t.Content(role="user", parts=[t.Part.from_text(text=text)]))

    def send_tool_results(self, results: list[tuple[str, dict]]) -> LLMReply:
        t = self._types
        parts = [t.Part.from_function_response(name=name, response=payload) for name, payload in results]
        return self._call(t.Content(role="user", parts=parts))


class GeminiLLM:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout_s: float,
                 thinking_budget: int = config.GEMINI_THINKING_BUDGET):
        from google import genai
        from google.genai import types
        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self._model = model
        self._thinking_budget = thinking_budget

    def start_chat(self, system: str, tools: list[ToolSpec]) -> GeminiChat:
        return GeminiChat(self._client, self._model, system, tools, self._thinking_budget)

    def complete(self, system: str, prompt: str) -> str:
        resp = self._client.models.generate_content(
            model=self._model, contents=prompt,
            config=self._types.GenerateContentConfig(
                system_instruction=system, temperature=0,
                thinking_config=self._types.ThinkingConfig(thinking_budget=self._thinking_budget)))
        content = _content_of(resp)
        text = "".join(p.text for p in (content.parts or []) if getattr(p, "text", None) and not getattr(p, "thought", None))
        if not text.strip():
            raise LLMError(f"Gemini returned empty text ({_block_reason(resp)})")
        note_provider(self.name)
        return text.strip()
