"""Real TicketActivities in ActivityEnvironment with scripted/spy LLMs: S-27, S-30 .. S-36."""
import dataclasses
import json

import pytest

from tests import fixtures as fx
from tests.llm_helpers import ScriptedLLM, SpyLLM, text_reply, tools_reply


def activity_env(workflow_id):
    from temporalio.testing import ActivityEnvironment

    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, workflow_id=workflow_id)
    return env


def make_activities(llm, repo, helpdesk=None, **kw):
    from ticketflow.activities.ticket_activities import TicketActivities
    from ticketflow.services.helpdesk import FakeHelpdesk

    return TicketActivities(llm=llm, helpdesk=helpdesk or FakeHelpdesk(failure_rate=0.0), repo=repo, **kw)


async def decide(acts, ticket):
    from ticketflow.models import DecideInput

    wid = f"ticket-{ticket.ticket_id}"
    return await activity_env(wid).run(acts.decide_ticket, DecideInput(ticket=ticket, workflow_id=wid))


def submit(cid, dec):
    return tools_reply((cid, "submit_decision", fx.decision_args(dec)))


def tool_messages(messages):
    return [m for m in messages if m.get("role") == "tool"]


# ---------------------------------------------------------------- S-27


async def test_s27_refund_issued_once_when_response_lost(repo):
    """S-27"""
    from ticketflow.models import ExecuteActionInput
    from ticketflow.services.helpdesk import FakeHelpdesk

    helpdesk = FakeHelpdesk(failure_rate=0.0)
    helpdesk.fail_next(1, after_commit=True)
    acts = make_activities(ScriptedLLM([]), repo, helpdesk=helpdesk)
    inp = ExecuteActionInput(
        ticket_id="TK-2",
        workflow_id="ticket-TK-2",
        idempotency_key="ticket-TK-2:refund",
        decision=fx.refund_decision(),
    )
    env = activity_env("ticket-TK-2")
    with pytest.raises(Exception):
        await env.run(acts.execute_action, inp)

    second = await env.run(acts.execute_action, inp)
    assert second.action_type == "refund"
    assert second.duplicate is True
    assert second.helpdesk_ref
    third = await env.run(acts.execute_action, inp)
    assert third.helpdesk_ref == second.helpdesk_ref
    assert helpdesk.refunds == [("INV-A2", 50.0)]


@pytest.mark.parametrize(
    "dec,expected",
    [
        (lambda: fx.reply_decision(), "reply"),
        (lambda: fx.escalate_decision(), "escalate"),
        (lambda: fx.close_decision(), "close"),
        (lambda: fx.refund_decision(), "refund"),
    ],
    ids=["reply", "escalate", "close", "refund"],
)
async def test_s27_execute_action_performs_each_action_once(repo, dec, expected):
    """S-27 / R11: each action type goes through the helpdesk with the given idempotency key."""
    from ticketflow.models import ExecuteActionInput
    from ticketflow.services.helpdesk import FakeHelpdesk

    helpdesk = FakeHelpdesk(failure_rate=0.0)
    acts = make_activities(ScriptedLLM([]), repo, helpdesk=helpdesk)
    key = f"ticket-TK-1:{expected}"
    inp = ExecuteActionInput(ticket_id="TK-1", workflow_id="ticket-TK-1", idempotency_key=key, decision=dec())
    res = await activity_env("ticket-TK-1").run(acts.execute_action, inp)
    assert res.action_type == expected
    assert res.duplicate is False
    assert res.helpdesk_ref
    assert helpdesk.was_seen(key)
    assert len(helpdesk.actions) == 1


# ---------------------------------------------------------------- S-30


async def test_s30_unknown_customer_detected_before_llm(repo):
    """S-30"""
    from temporalio.exceptions import ApplicationError

    spy = SpyLLM()
    acts = make_activities(spy, repo)
    with pytest.raises(ApplicationError) as excinfo:
        await decide(acts, fx.tk7())
    err = excinfo.value
    assert err.type == "UnknownCustomer"
    assert err.non_retryable is True
    assert "CUST-X" in str(err)
    assert spy.calls == []


# ---------------------------------------------------------------- S-31


async def test_s31_invalid_answer_fed_back_then_accepted(repo):
    """S-31"""
    final = fx.reply_decision(category="billing", message="We are checking the charge.", article_ids=["KB-BILL"])
    llm = ScriptedLLM(
        [
            text_reply("I think this is billing."),
            tools_reply(("c2", "submit_decision", {"category": "billing"})),
            submit("c3", final),
        ]
    )
    out = await decide(make_activities(llm, repo), fx.tk2())

    assert out.proposed == final
    assert len(llm.calls) == 3
    turn2 = llm.calls[1]
    assert turn2[-1]["role"] == "user"
    assert turn2[-1]["content"]
    turn3 = llm.calls[2]
    assert turn3[-1]["role"] == "tool"
    assert turn3[-1]["tool_call_id"] == "c2"
    assert "error" in json.loads(turn3[-1]["content"])
    # data snapshot for the rule check comes from the repo, not the model
    assert sorted(i.invoice_id for i in out.invoices) == ["INV-A1", "INV-A2"]
    assert sorted(out.known_article_ids) == sorted(fx.KB_IDS)


async def test_s31_messages_start_with_system_and_ticket(repo):
    """S-31 / R6: the loop starts from build_messages and appends assistant messages verbatim."""
    from ticketflow.activities.agent import TOOLS, build_messages

    final = fx.reply_decision()
    first = tools_reply(("c1", "search_kb", {"query": "password"}))
    llm = ScriptedLLM([first, submit("c2", final)])
    await decide(make_activities(llm, repo), fx.tk1())
    assert llm.calls[0] == build_messages(fx.tk1())
    assert llm.tools[0] == TOOLS
    assert llm.calls[1][2] == first.assistant_message
    tool_msg = llm.calls[1][3]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert "KB-PW" in tool_msg["content"]


# ---------------------------------------------------------------- S-32


@pytest.mark.parametrize("variant", ["text-only", "invalid-json"])
async def test_s32_too_many_invalid_answers(repo, variant):
    """S-32"""
    from temporalio.exceptions import ApplicationError

    if variant == "text-only":
        llm = ScriptedLLM(lambda i: text_reply(f"answer {i}"))
    else:
        llm = ScriptedLLM(lambda i: tools_reply((f"c{i}", "submit_decision", "{not valid json")))
    with pytest.raises(ApplicationError) as excinfo:
        await decide(make_activities(llm, repo), fx.tk2())
    assert excinfo.value.type == "InvalidModelOutput"
    assert excinfo.value.non_retryable is True
    assert len(llm.calls) == 3


# ---------------------------------------------------------------- S-33


async def test_s33_loop_limit(repo):
    """S-33"""
    from temporalio.exceptions import ApplicationError

    llm = ScriptedLLM(lambda i: tools_reply((f"c{i}", "search_kb", {"query": "password"})))
    with pytest.raises(ApplicationError) as excinfo:
        await decide(make_activities(llm, repo, max_turns=8), fx.tk1())
    assert excinfo.value.type == "AgentLoopExceeded"
    assert excinfo.value.non_retryable is True
    assert len(llm.calls) == 8


# ---------------------------------------------------------------- S-34


async def test_s34_unknown_tool_name_reported_back(repo):
    """S-34"""
    final = fx.reply_decision()
    llm = ScriptedLLM([tools_reply(("c1", "delete_customer", {})), submit("c2", final)])
    out = await decide(make_activities(llm, repo), fx.tk1())
    assert out.proposed == final
    msgs = [m for m in tool_messages(llm.calls[1]) if m.get("tool_call_id") == "c1"]
    assert len(msgs) == 1
    body = json.loads(msgs[0]["content"])
    assert "error" in body
    assert "unknown tool" in str(body["error"]).lower()


# ---------------------------------------------------------------- S-35


async def test_s35_tools_bound_to_ticket_customer(repo):
    """S-35"""
    final = fx.reply_decision(category="billing", article_ids=[])
    llm = ScriptedLLM(
        [
            tools_reply(
                ("c1", "get_invoices", {"customer_id": "CUST-B"}),
                ("c2", "get_customer", {"customer_id": "CUST-B"}),
            ),
            submit("c3", final),
        ]
    )
    out = await decide(make_activities(llm, repo), fx.tk2())
    results = tool_messages(llm.calls[1])
    assert {m["tool_call_id"] for m in results} == {"c1", "c2"}
    text = " ".join(m["content"] for m in results)
    assert "INV-A1" in text and "INV-A2" in text
    assert "business" in text
    for forbidden in ("CUST-B", "INV-B1", "enterprise", "Beta Freight", "900"):
        assert forbidden not in text
    assert sorted(i.invoice_id for i in out.invoices) == ["INV-A1", "INV-A2"]


# ---------------------------------------------------------------- S-36


async def test_s36_transient_llm_error_is_retryable(repo):
    """S-36"""
    from temporalio.exceptions import ApplicationError

    llm = ScriptedLLM([ConnectionError("connection reset")])
    with pytest.raises(BaseException) as excinfo:
        await decide(make_activities(llm, repo), fx.tk2())
    err = excinfo.value
    assert not (isinstance(err, ApplicationError) and err.non_retryable)
    assert len(llm.calls) == 1
