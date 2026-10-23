"""Scripted / spy LLM providers for activity tests (test doubles of LLMProvider, not of the unit under test)."""
import copy
import json


def tool_call_message(calls):
    """OpenAI-chat assistant message for [(id, name, raw_arguments)]."""
    msg = {"role": "assistant", "content": None}
    if calls:
        msg["tool_calls"] = [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": raw}}
            for cid, name, raw in calls
        ]
    return msg


def text_reply(text):
    from ticketflow.services.llm.base import LLMReply

    return LLMReply(content=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})


def tools_reply(*calls):
    """calls: (id, name, args) where args is a dict (serialised) or a raw str (possibly invalid JSON)."""
    from ticketflow.services.llm.base import LLMReply, ToolCall

    tool_calls = []
    raw_calls = []
    for cid, name, args in calls:
        if isinstance(args, str):
            raw = args
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    parsed = None
            except ValueError:
                parsed = None
        else:
            raw = json.dumps(args)
            parsed = args
        tool_calls.append(ToolCall(id=cid, name=name, arguments=parsed, raw_arguments=raw))
        raw_calls.append((cid, name, raw))
    return LLMReply(content=None, tool_calls=tool_calls, assistant_message=tool_call_message(raw_calls))


class ScriptedLLM:
    """Returns the scripted replies in order; `script` may also be a callable(turn_index) -> LLMReply.

    Records a deep copy of the messages and tools of every call.
    """

    def __init__(self, script):
        self.script = script
        self.calls = []  # list of messages lists
        self.tools = []

    async def complete(self, messages, tools):
        self.calls.append(copy.deepcopy(messages))
        self.tools.append(copy.deepcopy(tools))
        i = len(self.calls) - 1
        if callable(self.script):
            item = self.script(i)
        else:
            item = self.script[i]
        if isinstance(item, BaseException):
            raise item
        return item


class SpyLLM:
    """Wraps another provider and records replies (or counts calls when no inner provider)."""

    def __init__(self, inner=None):
        self.inner = inner
        self.calls = []
        self.replies = []

    async def complete(self, messages, tools):
        self.calls.append(copy.deepcopy(messages))
        if self.inner is None:
            raise AssertionError("LLM must not be called")
        reply = await self.inner.complete(messages, tools)
        self.replies.append(reply)
        return reply
