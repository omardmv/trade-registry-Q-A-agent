"""OpenAI Chat Completions backend with the same Chat/LLM interface as GeminiChat/GeminiLLM."""
import json
import re

from app import config
from app.agent.llm import (EmptyReplyError, LLMError, LLMReply, ProviderError, ToolCall, ToolSpec, note_provider,
                           record_usage)

_TYPES = {"OBJECT": "object", "STRING": "string", "ARRAY": "array", "INTEGER": "integer",
          "BOOLEAN": "boolean", "NUMBER": "number"}


def to_json_schema(schema: dict) -> dict:
    """Gemini-style OpenAPI schema (uppercase types) -> JSON Schema (lowercase types), recursively."""
    out = {}
    for key, value in schema.items():
        if key == "type":
            out["type"] = _TYPES.get(str(value).upper(), str(value).lower())
        elif key == "properties":
            out["properties"] = {name: to_json_schema(sub) for name, sub in value.items()}
        elif key == "items":
            out["items"] = to_json_schema(value)
        elif key in ("enum", "required"):
            out[key] = list(value)
        else:
            out[key] = value
    return out


def _tool_payload(tools: list[ToolSpec]) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": to_json_schema(t.parameters)}} for t in tools]


def _transient_status(status: int) -> bool:
    return status in (408, 409, 429) or status >= 500


def _record(name: str, resp) -> None:
    u = getattr(resp, "usage", None)
    if u is not None:
        details = getattr(u, "prompt_tokens_details", None)
        record_usage(name, getattr(u, "prompt_tokens", 0), getattr(details, "cached_tokens", 0) if details else 0,
                     getattr(u, "completion_tokens", 0))


def _create(client, reasoning_effort: str = "", temperature: str = "", provider: str = "openai", **kwargs):
    """chat.completions.create with openai exceptions mapped to TimeoutError / ProviderError."""
    import openai
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if temperature:
        kwargs["temperature"] = float(temperature)
    try:
        resp = client.chat.completions.create(**kwargs)
        _record(provider, resp)
        return resp
    except openai.APITimeoutError as e:
        raise TimeoutError(f"OpenAI timeout: {e}") from e
    except openai.APIStatusError as e:
        # 401/403 messages can echo (part of) the API key: keep only status and error type
        detail = type(e).__name__ if e.status_code in (401, 403) else e.message
        raise ProviderError(f"OpenAI HTTP {e.status_code}: {detail}",
                            transient=_transient_status(e.status_code), status=e.status_code) from e
    except openai.APIConnectionError as e:
        raise ProviderError(f"OpenAI connection error: {e}", transient=True) from e


def _choice(resp):
    choices = getattr(resp, "choices", None) or []
    if not choices or choices[0].message is None:
        raise LLMError("OpenAI returned no choices")
    choice = choices[0]
    if choice.finish_reason == "content_filter":
        raise LLMError("OpenAI blocked the reply (finish_reason=content_filter)")
    if choice.finish_reason == "length":
        raise LLMError("OpenAI reply was cut off (finish_reason=length)")
    if getattr(choice.message, "refusal", None):
        raise LLMError(f"OpenAI refused: {choice.message.refusal}")
    return choice


def _parse_args(raw: str | None, name: str) -> dict:
    try:
        args = json.loads(raw or "{}")
    except (TypeError, ValueError) as e:
        raise LLMError(f"OpenAI returned invalid JSON arguments for {name}: {e}") from e
    if not isinstance(args, dict):
        raise LLMError(f"OpenAI returned non-object arguments for {name}")
    return args


def _dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


class OpenAIChat:
    name = "openai"

    def __init__(self, client, model: str, system: str, tools: list[ToolSpec], reasoning_effort: str = "",
                 temperature: str = "", name: str = "openai"):
        self._client, self._model, self._effort = client, model, reasoning_effort
        self._temperature, self.name = temperature, name
        self._tools = _tool_payload(tools)
        self._messages: list[dict] = [{"role": "system", "content": system}]

    def _call(self, new: list[dict]) -> LLMReply:
        candidate = [*self._messages, *new]
        kwargs = dict(model=self._model, messages=candidate)
        if self._tools:
            kwargs.update(tools=self._tools, tool_choice="auto")
        msg = _choice(_create(self._client, self._effort, self._temperature, self.name, **kwargs)).message
        calls, native = [], []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                raise LLMError(f"OpenAI returned an unsupported tool call type {getattr(tc, 'type', None)!r}")
            calls.append(ToolCall(fn.name, _parse_args(fn.arguments, fn.name)))
            native.append({"id": tc.id, "type": "function",
                           "function": {"name": fn.name, "arguments": fn.arguments or "{}"}})
        text = msg.content or ""
        if not text.strip() and not calls:
            raise EmptyReplyError("OpenAI returned an empty reply")
        assistant = {"role": "assistant", "content": msg.content}
        if native:
            assistant["tool_calls"] = native
        self._messages = [*candidate, assistant]  # commit only after a successful response
        note_provider(self.name)
        return LLMReply(text, calls)

    def send_text(self, text: str) -> LLMReply:
        return self._call([{"role": "user", "content": text}])

    def send_tool_results(self, results: list[tuple[str, dict]]) -> LLMReply:
        return self._call(self._tool_messages(results))

    def _tool_messages(self, results: list[tuple[str, dict]]) -> list[dict]:
        """One tool message per call of the previous assistant message. In call order when the counts match,
        else by name in order; calls left without a result get an explicit "not executed" error."""
        last = self._messages[-1] if self._messages else {}
        calls = (last.get("tool_calls") or []) if last.get("role") == "assistant" else []
        if not calls:
            raise RuntimeError("tool results sent but the previous reply had no tool calls")  # a bug, not the model
        if len(results) == len(calls) and all(r[0] == c["function"]["name"] for r, c in zip(results, calls)):
            pairs = [(c["id"], payload) for c, (_, payload) in zip(calls, results)]
        else:
            used: set[str] = set()
            pairs = []
            for name, payload in results:
                match = next((c for c in calls if c["id"] not in used and c["function"]["name"] == name), None)
                if match is None:
                    raise RuntimeError(f"tool result for {name!r} matches no pending tool call")  # a bug, not the model
                used.add(match["id"])
                pairs.append((match["id"], payload))
            pairs += [(c["id"], {"error": "not executed"}) for c in calls if c["id"] not in used]
        return [{"role": "tool", "tool_call_id": cid, "content": _dump(payload)} for cid, payload in pairs]

    def load_history(self, events: list[tuple[str, object]]) -> None:
        """Rebuild native messages from a provider-neutral transcript (see app.agent.llm_fallback)."""
        messages = self._messages[:1]
        n = 0
        for kind, payload in events:
            if kind == "user_text":
                messages.append({"role": "user", "content": payload})
            elif kind == "assistant":
                if not payload.text and not payload.tool_calls:
                    continue  # an empty reply cannot be replayed (the API rejects it)
                msg = {"role": "assistant", "content": payload.text or None}
                if payload.tool_calls:
                    msg["tool_calls"] = []
                    for c in payload.tool_calls:
                        n += 1
                        msg["tool_calls"].append({"id": f"call_replay_{n}", "type": "function",
                                                  "function": {"name": c.name, "arguments": _dump(c.args)}})
                messages.append(msg)
            elif kind == "tool_results":
                self._messages = messages
                messages = [*messages, *self._tool_messages(payload)]
            else:
                raise ValueError(f"unknown transcript event {kind!r}")
        self._messages = messages


_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)


def _repair_json(text: str):
    """json.loads, or the same text with the missing closing brackets appended (small local models often stop
    one brace short)."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    stack, in_str, esc = [], False, False
    for ch in text:
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    try:
        return json.loads(text + "".join(reversed(stack)))
    except ValueError:
        return None


class TextToolChat(OpenAIChat):
    """Tool calling through the prompt (Qwen/Hermes <tool_call> format) instead of the API's tools field, for local
    OpenAI-compatible servers whose tool-call parser drops calls (Ollama returns an empty reply for a call it cannot
    parse). The tools are listed in the system message; <tool_call>{name, arguments}</tool_call> blocks are parsed
    from the reply text, with missing closing brackets repaired; tool results go back as <tool_response> blocks."""

    def __init__(self, client, model: str, system: str, tools: list[ToolSpec], reasoning_effort: str = "",
                 temperature: str = "", name: str = "openai"):
        super().__init__(client, model, system, [], reasoning_effort, temperature, name)
        listing = "\n".join(json.dumps(t, ensure_ascii=False) for t in _tool_payload(tools))
        self._messages = [{"role": "system", "content": (
            f"{system}\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\nYou are "
            f"provided with function signatures within <tools></tools> XML tags:\n<tools>\n{listing}\n</tools>\n\n"
            "For each function call, return a json object with function name and arguments within "
            "<tool_call></tool_call> XML tags:\n<tool_call>\n{\"name\": <function-name>, \"arguments\": "
            "<args-json-object>}\n</tool_call>")}]
        self._pending: list[str] = []

    def _call(self, new: list[dict]) -> LLMReply:
        candidate = [*self._messages, *new]
        msg = _choice(_create(self._client, self._effort, self._temperature, self.name,
                              model=self._model, messages=candidate)).message
        text = msg.content or ""
        calls = []
        for block in _TOOL_CALL_RE.findall(text):
            obj = _repair_json(block)
            if isinstance(obj, dict) and isinstance(obj.get("name"), str):
                args = obj.get("arguments")
                if isinstance(args, str):
                    args = _repair_json(args)
                calls.append(ToolCall(obj["name"], args if isinstance(args, dict) else {}))
        if not calls:                   # a bare (or fenced) {"name", "arguments"} object without the tags
            obj = _repair_json(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip())
            if isinstance(obj, dict) and isinstance(obj.get("name"), str) and isinstance(obj.get("arguments"), dict):
                calls.append(ToolCall(obj["name"], obj["arguments"]))
                text_is_call = True
            else:
                text_is_call = False
        else:
            text_is_call = False
        visible = "" if text_is_call else (_TOOL_CALL_RE.sub("", text).strip() if calls else text)
        if not visible.strip() and not calls:
            raise EmptyReplyError("OpenAI returned an empty reply")
        self._messages = [*candidate, {"role": "assistant", "content": text}]
        self._pending = [c.name for c in calls]
        note_provider(self.name)
        return LLMReply(visible, calls)

    def _tool_messages(self, results: list[tuple[str, dict]]) -> list[dict]:
        if not self._pending:
            raise RuntimeError("tool results sent but the previous reply had no tool calls")  # a bug, not the model
        body = "\n".join(f"<tool_response>\n{_dump({'name': n, 'result': p})}\n</tool_response>" for n, p in results)
        return [{"role": "user", "content": body}]

    def load_history(self, events: list[tuple[str, object]]) -> None:
        messages = self._messages[:1]
        for kind, payload in events:
            if kind == "user_text":
                messages.append({"role": "user", "content": payload})
            elif kind == "assistant":
                calls = "".join(f"<tool_call>\n{_dump({'name': c.name, 'arguments': c.args})}\n</tool_call>"
                                for c in payload.tool_calls)
                if payload.text or calls:
                    messages.append({"role": "assistant", "content": (payload.text or "") + calls})
                self._pending = [c.name for c in payload.tool_calls]
            elif kind == "tool_results":
                messages += self._tool_messages(payload)
            else:
                raise ValueError(f"unknown transcript event {kind!r}")
        self._messages = messages


class OpenAILLM:
    """Any OpenAI-compatible Chat Completions server: OpenAI itself, or a local one (Ollama, vLLM, llama.cpp) via
    base_url. temperature / text_tools default to the global OPENAI_* settings."""

    def __init__(self, api_key: str, model: str, timeout_s: float, client=None, reasoning_effort: str | None = None,
                 base_url: str = "", temperature: str | None = None, text_tools: bool | None = None,
                 name: str = "openai"):
        if client is None:
            import openai
            # a local server needs no key, but the client requires a non-empty one
            client = openai.OpenAI(api_key=api_key or "local", base_url=base_url or None, timeout=timeout_s,
                                   max_retries=0)  # retries: call_with_retry
        self._client, self._model, self.name = client, model, name
        self._effort = (config.OPENAI_REASONING_EFFORT if reasoning_effort is None else reasoning_effort).strip()
        self._temperature = (config.OPENAI_TEMPERATURE if temperature is None else temperature).strip()
        self._text_tools = config.OPENAI_TEXT_TOOLS if text_tools is None else text_tools

    def start_chat(self, system: str, tools: list[ToolSpec]) -> OpenAIChat:
        cls = TextToolChat if self._text_tools and tools else OpenAIChat
        return cls(self._client, self._model, system, tools, self._effort, self._temperature, self.name)

    def complete(self, system: str, prompt: str) -> str:
        resp = _create(self._client, self._effort, self._temperature, self.name, model=self._model,
                       messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}])
        text = (_choice(resp).message.content or "").strip()
        if not text:
            raise LLMError("OpenAI returned empty text")
        note_provider(self.name)
        return text
