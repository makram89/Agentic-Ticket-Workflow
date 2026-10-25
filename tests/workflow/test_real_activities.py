"""Workflow env with real activity code: S-28, S-41, S-39 (seed smoke), S-58 (c)."""
from decimal import Decimal
from datetime import timedelta

import pytest

from tests import fixtures as fx
from tests.log_helpers import assert_ticket_line, capture_json_logs
from tests.workflow_helpers import (
    FakeActivities,
    approve,
    decide_returning,
    query_status,
    result,
    start,
    wait_step,
    worker,
)


def real_activities(repo, helpdesk=None, llm=None):
    from ticketflow.activities.ticket_activities import TicketActivities
    from ticketflow.services.helpdesk import FakeHelpdesk
    from ticketflow.services.llm.fake import FakeLLM

    helpdesk = helpdesk or FakeHelpdesk(failure_rate=0.0)
    acts = TicketActivities(llm=llm or FakeLLM(), helpdesk=helpdesk, repo=repo)
    return acts, helpdesk


# ---------------------------------------------------------------- S-28


async def test_s28_idempotent_refund_under_temporal_retry(workflow_env, repo):
    """S-28"""
    from temporalio import activity

    from ticketflow.models import ActionResult, ExecuteActionInput
    from ticketflow.services.helpdesk import FakeHelpdesk

    helpdesk = FakeHelpdesk(failure_rate=0.0)
    helpdesk.fail_next(1, after_commit=True)
    acts, _ = real_activities(repo, helpdesk=helpdesk)
    attempts = []

    @activity.defn(name="execute_action")
    async def execute_action(inp: ExecuteActionInput) -> ActionResult:
        attempts.append(activity.info().attempt)
        return await acts.execute_action(inp)  # the real activity code

    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    decide_fn = fake.build()[0]
    async with worker(workflow_env.client, [decide_fn, execute_action]) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        await wait_step(handle, "awaiting_approval")
        await approve(handle, approved=True)
        res = await result(handle)

    assert res.outcome == "refunded"
    assert attempts == [1, 2]
    assert res.action.duplicate is True
    assert helpdesk.refunds == [("INV-A2", Decimal("50"))]


# ---------------------------------------------------------------- S-41


async def test_s41_prompt_injection_through_full_fake_pipeline(workflow_env, repo):
    """S-41"""
    acts, helpdesk = real_activities(repo)
    async with worker(workflow_env.client, [acts.decide_ticket, acts.execute_action]) as tq:
        handle = await start(workflow_env.client, fx.tk6(), tq)
        res = await result(handle)
        status = await query_status(handle)

    proposed = status.proposed_decision
    assert proposed.action.type == "refund"
    assert proposed.action.invoice_id == "INV-A2"
    assert proposed.action.amount_eur == Decimal("5000")
    assert status.decision.action.type == "escalate"
    assert status.violations
    assert status.approval is None
    assert helpdesk.refunds == []
    assert res.outcome == "escalated"
    assert len(helpdesk.actions) == 1


async def test_s41_never_awaits_approval(workflow_env, repo):
    """S-41: the injected ticket never enters awaiting_approval (checked from workflow history)."""
    acts, helpdesk = real_activities(repo)
    async with worker(workflow_env.client, [acts.decide_ticket, acts.execute_action]) as tq:
        handle = await start(workflow_env.client, fx.tk6(), tq)
        await result(handle)
        desc = await handle.describe()
        history = await handle.fetch_history()

    assert desc.close_time - desc.start_time < timedelta(hours=1)
    timers = [e for e in history.events if e.HasField("timer_started_event_attributes")]
    assert timers == [], "no approval timer may be started for a rule-violating refund"
    assert helpdesk.refunds == []


# ---------------------------------------------------------------- S-39

# Expected fake-mode outcomes on the seed data (design.md Appendix A).
SEED_EXPECTED = {
    "T-1": dict(category="account", priority="normal", action="reply", articles=["KB-1"]),
    "T-2": dict(category="billing", priority="normal", action="refund", invoice=("INV-1002", Decimal("49"))),
    "T-3": dict(category="technical", priority="urgent", action="escalate", reason="urgent"),
    "T-4": dict(category="account", priority="high", action="reply", articles=["KB-4"], message="INV-4001"),
    "T-5": dict(category="other", priority="low", action="close", reason="off-topic"),
    "T-6": dict(category="billing", priority="high", action="escalate", reason="rule violation",
                proposed_refund=("INV-1002", Decimal("5000"))),
    "T-7": dict(category="other", priority="normal", action="escalate", reason="C-999"),
}


@pytest.mark.parametrize("ticket_id", sorted(SEED_EXPECTED))
async def test_s39_seed_smoke(workflow_env, ticket_id):
    """S-39: the only test that uses seed contents (DataRepo.from_seed())."""
    from ticketflow.data.repository import DataRepo

    exp = SEED_EXPECTED[ticket_id]
    repo = DataRepo.from_seed()
    tickets = {t.ticket_id: t for t in repo.tickets()}
    assert ticket_id in tickets
    acts, helpdesk = real_activities(repo)

    async with worker(workflow_env.client, [acts.decide_ticket, acts.execute_action]) as tq:
        handle = await start(workflow_env.client, tickets[ticket_id], tq)
        status = await wait_step(handle, ("awaiting_approval", "completed", "failed"))
        if exp["action"] == "refund":
            assert status.step == "awaiting_approval"
            await approve(handle, approved=True, reviewer="smoke")
        else:
            assert status.step != "awaiting_approval"
        await result(handle)
        status = await query_status(handle)

    assert status.step == "completed"
    dec = status.decision
    assert dec.category == exp["category"]
    assert dec.priority == exp["priority"]
    assert dec.action.type == exp["action"]
    if "articles" in exp:
        assert dec.action.article_ids == exp["articles"]
    if "message" in exp:
        assert exp["message"] in dec.action.message
    if "reason" in exp:
        assert exp["reason"] in dec.action.reason
    if "invoice" in exp:
        assert (dec.action.invoice_id, dec.action.amount_eur) == exp["invoice"]
        assert helpdesk.refunds == [exp["invoice"]]
    else:
        assert helpdesk.refunds == []
    if "proposed_refund" in exp:
        p = status.proposed_decision.action
        assert (p.type, p.invoice_id, p.amount_eur) == ("refund", *exp["proposed_refund"])
    assert len(helpdesk.actions) == 1


# ---------------------------------------------------------------- S-58 (c)


async def test_s58_workflow_log_lines_carry_ids(workflow_env):
    """S-58 (c): TicketWorkflow log lines (workflow.logger) carry ticket_id and workflow_id."""
    fake = FakeActivities(
        decide=decide_returning(
            fx.reply_decision(category="account", article_ids=["KB-PW"]), customer_id="CUST-F"
        )
    )
    async with worker(workflow_env.client, fake.build()) as tq:
        with capture_json_logs() as cap:
            handle = await start(workflow_env.client, fx.tk1(), tq)
            await result(handle)

    lines = cap.ticket_records("TK-1")
    wf_lines = [r for r in lines if str(r.get("logger", "")).startswith("temporalio.workflow")]
    assert wf_lines, f"no workflow log lines captured: {cap.lines()}"
    for rec in lines:
        assert_ticket_line(rec, "TK-1", "ticket-TK-1")
