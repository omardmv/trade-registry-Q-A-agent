"""Primary/secondary LLM with automatic fallback, including mid-conversation, and a circuit breaker.

A FallbackChat keeps a provider-neutral transcript. When the active provider fails for a provider reason
(classify_error == "fallback"), the other provider's chat is rebuilt from the transcript and the same step is
sent there; the conversation then stays on it. A timeout switches too (the other provider
may be healthy), but not when the timeout leaves no room for another full timeout_s. A fallback attempt is only started if a full timeout_s still fits before the current
deadline (deadline_scope), so the per-answer time bound holds.
"""
import logging
import threading
import time
from collections.abc import Callable

from app.agent.llm import LLM, LLMReply, ToolSpec, classify_error, current_deadline, error_status, is_permanent, safe_text

log = logging.getLogger(__name__)


def _name(llm) -> str:
    return getattr(llm, "name", type(llm).__name__)


class FallbackLLM:
    def __init__(self, primary: LLM, secondary: LLM, cooldown_s: float = 300.0,
                 timeout_s: float | None = None, clock: Callable[[], float] = time.monotonic):
        from app import config
        self.providers = [primary, secondary]
        self.cooldown_s = cooldown_s
        self.timeout_s = config.LLM_TIMEOUT_S if timeout_s is None else timeout_s
        self.clock = clock
        self._down_until = [0.0, 0.0]
        self._lock = threading.Lock()

    # ---- circuit breaker (process-wide: one FallbackLLM per process) ----
    def _first(self) -> int:
        """Index of the provider a new chat/completion starts on: the primary unless it is marked down."""
        now = self.clock()
        with self._lock:
            for i in range(len(self.providers)):
                if self._down_until[i] <= now:
                    return i
        return 0

    def _failed(self, i: int, exc: BaseException) -> None:
        if is_permanent(exc):
            with self._lock:
                self._down_until[i] = self.clock() + self.cooldown_s
            log.warning("LLM provider %s marked down for %.0f s: %s", _name(self.providers[i]), self.cooldown_s,
                        safe_text(exc))

    def is_down(self, i: int) -> bool:
        with self._lock:
            return self._down_until[i] > self.clock()

    def _may_fall_back(self, deadline: float | None) -> bool:
        if deadline is None:
            deadline = current_deadline()
        return deadline is None or deadline - self.clock() >= self.timeout_s

    def _run(self, start: int, attempt: Callable[[int], object], deadline: float | None, switched: bool = False,
             on_switch: Callable[[int], None] | None = None):
        """attempt(i) on provider start; on a provider failure, once, on the other provider if time allows
        (on_switch(other) is called first). -> (index that answered, result)."""
        try:
            return start, attempt(start)
        except Exception as e:
            kind = classify_error(e)
            if kind == "bug":
                raise
            if kind == "fallback":
                self._failed(start, e)
            other = 1 - start
            if switched or not self._may_fall_back(deadline):
                raise
            # a provider that is marked down is only a last resort, when the one that just failed is down too
            if self.is_down(other) and not self.is_down(start):
                raise
            # a failure with HTTP 400/404 may be our own malformed request: make it visible
            level = logging.ERROR if error_status(e) in (400, 404) else logging.WARNING
            log.log(level, "LLM provider %s %s (%s); falling back to %s", _name(self.providers[start]),
                    "timed out" if kind == "timeout" else "failed", safe_text(e), _name(self.providers[other]))
            if on_switch is not None:
                on_switch(other)
            try:
                return other, attempt(other)
            except Exception as e2:
                if classify_error(e2) == "fallback":
                    self._failed(other, e2)
                raise

    # ---- LLM interface ----
    def start_chat(self, system: str, tools: list[ToolSpec]) -> "FallbackChat":
        return FallbackChat(self, system, tools)

    def complete(self, system: str, prompt: str, deadline: float | None = None) -> str:
        return self._run(self._first(), lambda k: self.providers[k].complete(system, prompt), deadline)[1]


class FallbackChat:
    def __init__(self, owner: FallbackLLM, system: str, tools: list[ToolSpec]):
        self._owner, self._system, self._tools = owner, system, tools
        self.events: list[tuple[str, object]] = []  # committed transcript: user_text / assistant / tool_results
        self._active = owner._first()
        self._chats: dict[int, object] = {self._active: owner.providers[self._active].start_chat(system, tools)}
        self._switched = False
        self.providers_used: list[str] = []

    @property
    def provider(self) -> str:
        return _name(self._owner.providers[self._active])

    def _chat(self, i: int):
        if i not in self._chats:
            chat = self._owner.providers[i].start_chat(self._system, self._tools)
            chat.load_history(list(self.events))
            self._chats[i] = chat
        return self._chats[i]

    def _switch(self, other: int) -> None:
        """The conversation stays on the other provider from now on, even if this attempt fails too."""
        self._active, self._switched = other, True

    def _send(self, kind: str, payload, deadline: float | None) -> LLMReply:
        method = "send_text" if kind == "user_text" else "send_tool_results"
        _, reply = self._owner._run(self._active, lambda k: getattr(self._chat(k), method)(payload),
                                    deadline, switched=self._switched, on_switch=self._switch)
        self.events += [(kind, payload), ("assistant", reply)]
        self.providers_used.append(self.provider)
        return reply

    def send_text(self, text: str, deadline: float | None = None) -> LLMReply:
        return self._send("user_text", text, deadline)

    def send_tool_results(self, results: list[tuple[str, dict]], deadline: float | None = None) -> LLMReply:
        return self._send("tool_results", list(results), deadline)


def build_llm(order: list[str], keys: dict[str, str], models: dict[str, str], timeout_s: float,
              cooldown_s: float = 300.0) -> LLM:
    """Providers in `order` that have a key: two -> FallbackLLM(first, second), one -> that provider alone."""
    makers = {"gemini": lambda: _gemini(keys["gemini"], models["gemini"], timeout_s),
              "openai": lambda: _openai(keys["openai"], models["openai"], timeout_s)}
    unknown = [p for p in order if p not in makers]
    if unknown:
        raise ValueError(f"LLM_PROVIDERS: unknown provider(s) {unknown}; use gemini and/or openai")
    available = [p for p in dict.fromkeys(order) if keys.get(p)]
    if not available:
        raise RuntimeError("No LLM provider configured: set GEMINI_API_KEY and/or OPENAI_API_KEY "
                           f"(LLM_PROVIDERS={','.join(order)})")
    llms = [makers[p]() for p in available[:2]]
    return llms[0] if len(llms) == 1 else FallbackLLM(llms[0], llms[1], cooldown_s, timeout_s)


def _gemini(key, model, timeout_s):
    from app.agent.llm import GeminiLLM
    return GeminiLLM(key, model, timeout_s)


def _openai(key, model, timeout_s):
    from app.agent.llm_openai import OpenAILLM
    return OpenAILLM(key, model, timeout_s)
