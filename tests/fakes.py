from app.agent.llm import LLMReply, ToolCall


def call(name, **args):
    return LLMReply(tool_calls=[ToolCall(name, args)])


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def _next(self):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):  # scripted provider failure (e.g. an empty reply)
            raise reply
        return reply

    def send_text(self, text):
        self.sent.append(("text", text))
        return self._next()

    def send_tool_results(self, results):
        self.sent.append(("tools", results))
        return self._next()


class FakeLLM:
    def __init__(self, chat_replies=(), completions=()):
        self.chat = FakeChat(chat_replies)
        self.completions = list(completions)
        self.prompts = []
        self.system = None

    def start_chat(self, system, tools):
        self.system = system
        return self.chat

    def complete(self, system, prompt):
        self.prompts.append(prompt)
        return self.completions.pop(0)
