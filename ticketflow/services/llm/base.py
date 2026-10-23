"""Provider-neutral LLM interface. The message/tool format IS the OpenAI chat format (D15)."""
import json
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None  # None when raw_arguments is not a JSON object
    raw_arguments: str


@dataclass
class LLMReply:
    content: str | None
    tool_calls: list[ToolCall]
    assistant_message: dict  # OpenAI-chat assistant message, appended verbatim to the history


class LLMProvider(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply: ...


def parse_arguments(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def make_reply(content: str | None, calls: list[tuple[str, str, str]]) -> LLMReply:
    """Build an LLMReply from (id, name, raw_arguments) triples; used by every provider."""
    message: dict = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": raw}} for cid, name, raw in calls
        ]
    tool_calls = [ToolCall(id=cid, name=name, arguments=parse_arguments(raw), raw_arguments=raw) for cid, name, raw in calls]
    return LLMReply(content=content, tool_calls=tool_calls, assistant_message=message)
