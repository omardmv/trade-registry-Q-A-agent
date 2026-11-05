"""A chain of LLM providers with automatic fallback, including mid-conversation, and a circuit breaker.

A FallbackChat keeps a provider-neutral transcript. When the active provider fails for a provider reason
(classify_error == "fallback"), the next provider's chat is rebuilt from the transcript and the same step is
sent there; the conversation then stays on it (it never returns to a provider it left). A timeout switches too (the other provider
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
    def __init__(self, *providers: LLM, cooldown_s: float = 300.0,
                 timeout_s: float | None = None, clock: Callable[[], float] = time.monotonic):
        from app import config
        if len(providers) < 2:
            raise ValueError("FallbackLLM needs at least two providers")
        self.providers = list(providers)
        self.cooldown_s = cooldown_s
        self.timeout_s = config.LLM_TIMEOUT_S if timeout_s is None else timeout_s
        self.clock = clock
        self._down_until = [0.0] * len(self.providers)
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

    def _next(self, failed: int, tried: set[int]) -> int | None:
        """The provider to try after `failed`: the next one in chain order not tried yet and not marked down; a
        provider marked down is only a last resort, when the one that just failed is down too."""
        n = len(self.providers)
        rest = [(failed + k) % n for k in range(1, n) if (failed + k) % n not in tried]
        up = [j for j in rest if not self.is_down(j)]
        if up:
            return up[0]
        return rest[0] if rest and self.is_down(failed) else None

    def _run(self, start: int, attempt: Callable[[int], object], deadline: float | None,
             exclude: set[int] = frozenset(), on_switch: Callable[[int], None] | None = None):
        """attempt(i) on provider start; on a provider failure, on the next providers of the chain (never one in
        `exclude`) while a full timeout still fits (on_switch(next) is called first). -> (index, result)."""
        tried, i = set(exclude), start
        while True:
            tried.add(i)
            try:
                return i, attempt(i)
            except Exception as e:
                kind = classify_error(e)
                if kind == "bug":
                    raise
                if kind == "fallback":
                    self._failed(i, e)
                nxt = self._next(i, tried)
                if nxt is None or not self._may_fall_back(deadline):
                    raise
                # a failure with HTTP 400/404 may be our own malformed request: make it visible
                level = logging.ERROR if error_status(e) in (400, 404) else logging.WARNING
                log.log(level, "LLM provider %s %s (%s); falling back to %s", _name(self.providers[i]),
                        "timed out" if kind == "timeout" else "failed", safe_text(e), _name(self.providers[nxt]))
                if on_switch is not None:
                    on_switch(nxt)
                i = nxt

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
        self._left: set[int] = set()  # providers this conversation moved away from
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
        self._left.add(self._active)
        self._active = other

    def _send(self, kind: str, payload, deadline: float | None) -> LLMReply:
        method = "send_text" if kind == "user_text" else "send_tool_results"
        _, reply = self._owner._run(self._active, lambda k: getattr(self._chat(k), method)(payload),
                                    deadline, exclude=self._left, on_switch=self._switch)
        self.events += [(kind, payload), ("assistant", reply)]
        self.providers_used.append(self.provider)
        return reply

    def send_text(self, text: str, deadline: float | None = None) -> LLMReply:
        return self._send("user_text", text, deadline)

    def send_tool_results(self, results: list[tuple[str, dict]], deadline: float | None = None) -> LLMReply:
        return self._send("tool_results", list(results), deadline)


def build_llm(order: list[str], timeout_s: float, cooldown_s: float = 300.0,
              settings: Callable[[str], dict | None] | None = None) -> LLM:
    """The named providers of `order` (see config.provider_settings) that are usable (an API key, or a base_url for
    a local server), as a fallback chain in that order; one usable provider -> that provider alone."""
    from app import config
    settings = settings or config.provider_settings
    specs = {name: settings(name) for name in dict.fromkeys(order)}
    unknown = [n for n, s in specs.items() if s is None]
    if unknown:
        raise ValueError(f"LLM_PROVIDERS: unknown provider(s) {unknown}; built in: gemini, openai, qwen_local "
                         "(or set <NAME>_TYPE=gemini|openai for another one)")
    usable = [(n, s) for n, s in specs.items() if s.get("api_key") or (s["type"] == "openai" and s.get("base_url"))]
    if not usable:
        raise RuntimeError("No LLM provider configured: set an API key (GEMINI_API_KEY, OPENAI_API_KEY) or a local "
                           f"base_url (LLM_PROVIDERS={','.join(order)})")
    llms = [make_provider(n, s, timeout_s) for n, s in usable]
    return llms[0] if len(llms) == 1 else FallbackLLM(*llms, cooldown_s=cooldown_s, timeout_s=timeout_s)


def make_provider(name: str, spec: dict, timeout_s: float) -> LLM:
    if spec["type"] == "gemini":
        from app.agent.llm import GeminiLLM
        llm = GeminiLLM(spec["api_key"], spec["model"], timeout_s)
        llm.name = name
        return llm
    from app.agent.llm_openai import OpenAILLM
    return OpenAILLM(spec.get("api_key", ""), spec["model"], timeout_s, base_url=spec.get("base_url", ""),
                     temperature=spec.get("temperature", ""), text_tools=bool(spec.get("text_tools")), name=name)
