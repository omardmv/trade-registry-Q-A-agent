"""LLM abstraction so the agent can be tested with scripted fakes; OpenAI-compatible servers are the real backend
(app.agent.llm_openai), optionally chained with fallback (app.agent.llm_fallback)."""
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from collections.abc import Callable, Iterator
from typing import Literal, Protocol, TypeVar

T = TypeVar("T")


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


class EmptyReplyError(LLMError):
    """The model replied with neither text nor tool calls (nothing was committed to the chat history)."""


class ProviderError(RuntimeError):
    """The provider refused or failed the call (HTTP status, connection error). transient=True: worth a retry."""

    def __init__(self, message: str, *, transient: bool, status: int | None = None):
        super().__init__(message)
        self.transient, self.status = transient, status


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


_USAGE: dict[str, list[int]] = {}  # provider -> [requests, input tokens, cached input tokens, output tokens]
_USAGE_LOCK = threading.Lock()


def record_usage(provider: str, inp: int | None, cached: int | None, out: int | None) -> None:
    """Process-wide token counter (the evaluation reports it and stops at a budget)."""
    with _USAGE_LOCK:
        u = _USAGE.setdefault(provider, [0, 0, 0, 0])
        u[0] += 1
        u[1] += inp or 0
        u[2] += cached or 0
        u[3] += out or 0


def usage_totals() -> dict[str, list[int]]:
    with _USAGE_LOCK:
        return {k: list(v) for k, v in _USAGE.items()}


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


def error_status(exc: BaseException) -> int | None:
    """HTTP status of a provider error (ProviderError, openai APIStatusError)."""
    if isinstance(exc, ProviderError):
        return exc.status
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


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
    if isinstance(exc, (ProviderError, LLMError)) or _is_openai_error(exc):
        return "fallback"
    if type(exc).__name__ in _CONNECTION_NAMES:
        return "fallback"
    return "bug"


def is_permanent(exc: BaseException) -> bool:
    """Credits depleted / bad key / forbidden / unknown model: the provider will keep failing for a while
    (circuit breaker)."""
    return classify_error(exc) == "fallback" and error_status(exc) in _PERMANENT_STATUS


def safe_text(exc: BaseException) -> str:
    """Text of an error that is safe to log or store. Auth errors (401/403) may echo key material in their
    message: only the status code and the error type are kept. (ProviderError messages are built safe.)"""
    status = error_status(exc)
    if status in _AUTH_STATUS and not isinstance(exc, ProviderError):
        return f"HTTP {status} {type(exc).__name__}"
    return str(exc)


def is_transient(exc: BaseException) -> bool:
    """A transient ProviderError (HTTP 408/409/429/5xx, connection error) or a timeout."""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, ProviderError):
        return exc.transient
    return type(exc).__name__ in {"ReadTimeout", "ConnectTimeout", "TimeoutException"}


def call_with_retry(fn: Callable[[], T], deadline: float, timeout_s: float) -> T:
    """Call fn; on a transient error retry once, but only if a full timeout_s still fits before deadline
    (HTTP 429: after a 1 s pause, which must fit too)."""
    try:
        return fn()
    except Exception as e:
        if not is_transient(e):
            raise
        rate_limited = error_status(e) == 429
        if deadline - time.monotonic() < timeout_s + (1.0 if rate_limited else 0.0):
            raise
        if rate_limited:
            time.sleep(1.0)
    return fn()
