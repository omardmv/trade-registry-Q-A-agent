"""Offline stub clients shaped like the openai SDK (no network)."""
import copy
import json
import time

import httpx2
import openai
from openai.types.chat import ChatCompletion

from app.agent.llm_openai import OpenAILLM


class Slow:
    """Outcome wrapper: advance the (fake) clock or sleep before producing the outcome."""

    def __init__(self, seconds, outcome, clock=None):
        self.seconds, self.outcome, self.clock = seconds, outcome, clock


def _produce(o):
    if isinstance(o, Slow):
        if o.clock is not None:
            o.clock.advance(o.seconds)
        else:
            time.sleep(o.seconds)
        o = o.outcome
    if isinstance(o, BaseException):
        raise o
    return o


# ---- OpenAI ----
class OpenAICompletions:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return _produce(self.outcomes.pop(0))


class OpenAIClient:
    def __init__(self, outcomes):
        self.chat = type("Chat", (), {})()
        self.chat.completions = OpenAICompletions(outcomes)


def openai_llm(outcomes, name: str = "openai") -> OpenAILLM:
    """An OpenAI-compatible provider called `name` whose calls produce `outcomes` in order."""
    return OpenAILLM("dummy-key", "gpt-test", 5, client=OpenAIClient(outcomes), name=name)


def o_resp(content=None, tool_calls=(), finish=None):
    calls = [{"id": cid, "type": "function",
              "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
             for cid, name, args in tool_calls]
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return ChatCompletion.model_validate({
        "id": "chatcmpl-test", "created": 0, "model": "gpt-test", "object": "chat.completion",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": finish or ("tool_calls" if calls else "stop")}]})


def o_text(text):
    return o_resp(content=text)


def o_call(name, cid="call_1", **args):
    return o_resp(tool_calls=[(cid, name, args)])


_REQ = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")


def o_status(code, message="error"):
    cls = {401: openai.AuthenticationError, 403: openai.PermissionDeniedError, 429: openai.RateLimitError}.get(
        code, openai.InternalServerError if code >= 500 else openai.APIStatusError)
    return cls(message, response=httpx2.Response(code, request=_REQ), body=None)


def o_connection():
    return openai.APIConnectionError(request=_REQ)


def o_timeout():
    return openai.APITimeoutError(request=_REQ)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s
