"""LLM providers: FakeLLM shape (S-40), OpenRouter adapter translation (S-57) and error mapping (S-67)."""
from decimal import Decimal
import json

import httpx
import pytest

from tests import fixtures as fx
from tests.llm_helpers import SpyLLM

API_KEY = "sk-or-test-SECRET-123456"
REQ = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def assert_openai_assistant_shape(reply):
    from ticketflow.services.llm.base import LLMReply, ToolCall

    assert isinstance(reply, LLMReply)
    msg = reply.assistant_message
    assert msg["role"] == "assistant"
    assert "content" in msg
    assert msg["content"] == reply.content
    if reply.tool_calls:
        assert len(msg["tool_calls"]) == len(reply.tool_calls)
        for raw, tc in zip(msg["tool_calls"], reply.tool_calls):
            assert isinstance(tc, ToolCall)
            assert set(raw) == {"id", "type", "function"}
            assert raw["type"] == "function"
            assert raw["id"] == tc.id
            assert raw["function"]["name"] == tc.name
            assert isinstance(raw["function"]["arguments"], str)
            assert json.loads(raw["function"]["arguments"]) == tc.arguments
            assert tc.raw_arguments == raw["function"]["arguments"]
            assert isinstance(tc.arguments, dict)
    else:
        assert "tool_calls" not in msg


def completion(content=None, tool_calls=None):
    from openai.types.chat import ChatCompletion

    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}
            for cid, name, args in tool_calls
        ]
    return ChatCompletion.model_validate(
        {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 0,
            "model": "openai/gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                    "message": message,
                }
            ],
        }
    )


class StubCompletions:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.kwargs = []

    async def create(self, **kwargs):
        self.kwargs.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


class StubClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {})()
        self.chat.completions = completions


def history():
    from ticketflow.activities.agent import build_messages

    msgs = build_messages(fx.tk1())
    msgs.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_0", "type": "function", "function": {"name": "get_customer", "arguments": "{}"}}
            ],
        }
    )
    msgs.append({"role": "tool", "tool_call_id": "call_0", "content": json.dumps({"plan": "free"})})
    return msgs


async def test_s57_openrouter_adapter_translation():
    """S-57"""
    from ticketflow.activities.agent import TOOLS
    from ticketflow.services.llm.base import ToolCall
    from ticketflow.services.llm.openrouter import OpenRouterLLM

    stub = StubCompletions(result=completion(None, [("call_1", "search_kb", '{"query":"x"}')]))
    llm = OpenRouterLLM(api_key=API_KEY, client=StubClient(stub))
    msgs = history()
    reply = await llm.complete(msgs, TOOLS)

    assert len(stub.kwargs) == 1
    sent = stub.kwargs[0]
    assert sent["model"] == "openai/gpt-4o-mini"
    assert sent["temperature"] == 0
    assert sent["max_tokens"] == 1024
    assert sent["messages"][0] == msgs[0]
    assert len(sent["messages"]) == len(msgs)
    assert sent["tools"] == TOOLS
    for tool in sent["tools"]:
        assert tool["type"] == "function"
        assert {"name", "description", "parameters"} <= set(tool["function"])

    assert reply.content is None
    assert reply.tool_calls == [ToolCall(id="call_1", name="search_kb", arguments={"query": "x"}, raw_arguments='{"query":"x"}')]
    assert_openai_assistant_shape(reply)


async def test_s57_invalid_tool_arguments_become_none():
    """S-57 / design: ToolCall.arguments is None if the JSON is invalid."""
    from ticketflow.services.llm.openrouter import OpenRouterLLM

    stub = StubCompletions(result=completion(None, [("call_9", "submit_decision", "{not json")]))
    llm = OpenRouterLLM(api_key=API_KEY, model="some/model", client=StubClient(stub))
    reply = await llm.complete(history(), [])
    assert stub.kwargs[0]["model"] == "some/model"
    assert reply.tool_calls[0].arguments is None
    assert reply.tool_calls[0].raw_arguments == "{not json"
    assert reply.assistant_message["tool_calls"][0]["function"]["arguments"] == "{not json"


async def test_s40_openrouter_text_reply_shape():
    """S-40 (adapter side, text-only reply: no tool_calls key)"""
    from ticketflow.services.llm.openrouter import OpenRouterLLM

    llm = OpenRouterLLM(api_key=API_KEY, client=StubClient(StubCompletions(result=completion("hello"))))
    reply = await llm.complete(history(), [])
    assert reply.content == "hello"
    assert reply.tool_calls == []
    assert_openai_assistant_shape(reply)


async def test_s40_openrouter_tool_call_reply_shape():
    """S-40 (adapter side)"""
    from ticketflow.services.llm.openrouter import OpenRouterLLM

    stub = StubCompletions(
        result=completion(None, [("call_a", "get_customer", "{}"), ("call_b", "search_kb", '{"query": "pw"}')])
    )
    llm = OpenRouterLLM(api_key=API_KEY, client=StubClient(stub))
    reply = await llm.complete(history(), [])
    assert [tc.name for tc in reply.tool_calls] == ["get_customer", "search_kb"]
    assert_openai_assistant_shape(reply)


async def test_s40_fake_llm_speaks_adapter_shape(repo):
    """S-40 (fake side): first call get_customer, finally submit_decision, same message shape."""
    from ticketflow.activities.agent import TOOLS, build_messages, run_agent
    from ticketflow.services.llm.fake import FakeLLM

    first = await FakeLLM().complete(build_messages(fx.tk1()), TOOLS)
    assert_openai_assistant_shape(first)
    assert [tc.name for tc in first.tool_calls] == ["get_customer"]

    spy = SpyLLM(FakeLLM())
    decision = await run_agent(fx.tk1(), spy, repo)
    assert spy.replies[0].tool_calls[0].name == "get_customer"
    for reply in spy.replies:
        assert_openai_assistant_shape(reply)
        assert reply.tool_calls
    last = spy.replies[-1]
    assert [tc.name for tc in last.tool_calls] == ["submit_decision"]
    called = [tc.name for r in spy.replies for tc in r.tool_calls]
    assert "search_kb" in called
    assert decision.category == "account"
    assert decision.action.type == "reply"
    assert decision.action.article_ids == ["KB-PW"]


async def test_s40_fake_llm_is_deterministic(repo):
    """S-40 / R13: deterministic keyword rules."""
    from ticketflow.activities.agent import run_agent
    from ticketflow.services.llm.fake import FakeLLM

    a = await run_agent(fx.tk2(), FakeLLM(), repo)
    b = await run_agent(fx.tk2(), FakeLLM(), repo)
    assert a == b
    # "charged twice" with two equal paid invoices in the same month -> refund the higher invoice id
    assert a.category == "billing"
    assert a.action.type == "refund"
    assert a.action.invoice_id == "INV-A2"
    assert a.action.amount_eur == Decimal("50")


def _err(cls, status):
    return cls("upstream said no", response=httpx.Response(status, request=REQ), body=None)


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda o: o.APIConnectionError(request=REQ),
        lambda o: o.APITimeoutError(request=REQ),
        lambda o: _err(o.RateLimitError, 429),
        lambda o: _err(o.InternalServerError, 500),
    ],
    ids=["connection", "timeout", "429", "500"],
)
async def test_s67_retryable_errors_propagate(make_exc):
    """S-67 (a)-(c)"""
    import openai
    from temporalio.exceptions import ApplicationError

    from ticketflow.services.llm.openrouter import OpenRouterLLM

    llm = OpenRouterLLM(api_key=API_KEY, client=StubClient(StubCompletions(exc=make_exc(openai))))
    with pytest.raises(Exception) as excinfo:
        await llm.complete(history(), [])
    err = excinfo.value
    assert not (isinstance(err, ApplicationError) and err.non_retryable)


@pytest.mark.parametrize(
    "cls_name,status",
    [
        ("AuthenticationError", 401),
        ("PermissionDeniedError", 403),
        ("NotFoundError", 404),
        ("BadRequestError", 400),
    ],
)
async def test_s67_rejected_requests_are_non_retryable(cls_name, status):
    """S-67 (d)-(f)"""
    import openai
    from temporalio.exceptions import ApplicationError

    from ticketflow.services.llm.openrouter import OpenRouterLLM

    exc = _err(getattr(openai, cls_name), status)
    llm = OpenRouterLLM(api_key=API_KEY, client=StubClient(StubCompletions(exc=exc)))
    with pytest.raises(ApplicationError) as excinfo:
        await llm.complete(history(), [])
    err = excinfo.value
    assert err.type == "LLMRequestRejected"
    assert err.non_retryable is True
    assert API_KEY not in str(err)
    assert API_KEY not in (err.message or "")
    assert API_KEY not in repr(err.details)
