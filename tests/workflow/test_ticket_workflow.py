"""TicketWorkflow in a time-skipping workflow env with fake activities: S-12 .. S-26, S-62."""
from datetime import timedelta

import pytest

from tests import fixtures as fx
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


def reply_tk1():
    return fx.reply_decision(category="account", priority="normal", message="Use the reset link.", article_ids=["KB-PW"])


# ---------------------------------------------------------------- S-12


async def test_s12_answered_ticket(workflow_env):
    """S-12"""
    fake = FakeActivities(decide=decide_returning(reply_tk1(), customer_id="CUST-F"))
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk1(), tq)
        res = await result(handle)
        status = await query_status(handle)

    assert len(fake.act_inputs) == 1
    act = fake.act_inputs[0]
    assert act.decision.action.type == "reply"
    assert act.idempotency_key == "ticket-TK-1:reply"
    assert act.ticket_id == "TK-1"
    assert act.workflow_id == "ticket-TK-1"
    assert fake.decide_inputs[0].workflow_id == "ticket-TK-1"
    assert fake.decide_inputs[0].ticket == fx.tk1()
    assert res.outcome == "replied"
    assert res.action.action_type == "reply"
    assert status.step == "completed"
    assert status.violations == []
    assert status.result.action.action_type == "reply"
    assert status.ticket_id == "TK-1"
    assert status.workflow_id == "ticket-TK-1"
    assert status.error is None


# ---------------------------------------------------------------- S-13


async def test_s13_approved_refund(workflow_env):
    """S-13"""
    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        await wait_step(handle, "awaiting_approval")
        assert fake.act_inputs == []
        await approve(handle, approved=True, reviewer="ana")
        res = await result(handle)
        status = await query_status(handle)

    assert fake.act_types == ["refund"]
    act = fake.act_inputs[0]
    assert act.decision.action.invoice_id == "INV-A2"
    assert act.decision.action.amount_eur == 50.0
    assert act.idempotency_key == "ticket-TK-2:refund"
    assert res.outcome == "refunded"
    assert status.approval is not None and status.approval.approved is True
    assert status.approval.reviewer == "ana"
    assert status.step == "completed"


# ---------------------------------------------------------------- S-14


async def test_s14_refund_not_answered_in_24h_is_escalated(workflow_env):
    """S-14"""
    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        res = await result(handle)
        status = await query_status(handle)
        desc = await handle.describe()

    assert desc.close_time - desc.start_time >= timedelta(hours=24)
    assert fake.act_types == ["escalate"]
    assert fake.act_inputs[0].decision.action.reason == "refund approval timed out after 24h"
    assert fake.act_inputs[0].idempotency_key == "ticket-TK-2:escalate"
    assert res.outcome == "escalated"
    assert status.approval is None
    assert status.step == "completed"


# ---------------------------------------------------------------- S-15


async def test_s15_no_premature_timeout(workflow_env):
    """S-15"""
    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        await wait_step(handle, "awaiting_approval")
        await workflow_env.sleep(timedelta(hours=23))
        status = await query_status(handle)
        assert status.step == "awaiting_approval"
        assert fake.act_inputs == []

        await approve(handle, approved=True)
        res = await result(handle)

    assert fake.act_types == ["refund"]
    assert res.outcome == "refunded"


# ---------------------------------------------------------------- S-16


async def test_s16_rejected_refund_gets_declining_reply(workflow_env):
    """S-16"""
    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        await wait_step(handle, "awaiting_approval")
        await approve(handle, approved=False, reviewer="ana", comment="not a duplicate charge")
        res = await result(handle)
        status = await query_status(handle)

    assert fake.act_types == ["reply"]
    act = fake.act_inputs[0].decision.action
    assert act.article_ids == []
    assert "INV-A2" in act.message
    assert "50.00" in act.message
    assert "not a duplicate charge" in act.message
    assert fake.act_inputs[0].idempotency_key == "ticket-TK-2:reply"
    assert res.outcome == "refund_declined"
    assert status.approval.approved is False


# ---------------------------------------------------------------- S-17


async def test_s17_approval_before_awaiting_is_ignored(workflow_env):
    """S-17"""
    from ticketflow.models import Approval

    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(
            workflow_env.client,
            fx.tk2(),
            tq,
            start_signal="approval",
            start_signal_args=[Approval(approved=True, reviewer="early")],
        )
        res = await result(handle)
        status = await query_status(handle)

    assert "refund" not in fake.act_types
    assert fake.act_types == ["escalate"]
    assert fake.act_inputs[0].decision.action.reason == "refund approval timed out after 24h"
    assert res.outcome == "escalated"
    assert status.approval is None


# ---------------------------------------------------------------- S-18


async def test_s18_only_first_approval_counts(workflow_env):
    """S-18"""
    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    activities = fake.build()
    # max_cached_workflows=0: no sticky queue, so the next worker picks up the workflow task at once
    async with worker(workflow_env.client, activities, max_cached_workflows=0) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        await wait_step(handle, "awaiting_approval")
    # No worker is running now, so both signals are delivered in the same workflow task.
    await approve(handle, approved=True, reviewer="first")
    await approve(handle, approved=False, reviewer="second")
    async with worker(workflow_env.client, activities, tq=tq):
        res = await result(handle)
        status = await query_status(handle)

    assert fake.act_types == ["refund"]
    assert res.outcome == "refunded"
    assert status.approval.approved is True
    assert status.approval.reviewer == "first"


# ---------------------------------------------------------------- S-19


async def test_s19_activity_fails_once_then_succeeds(workflow_env):
    """S-19"""
    from ticketflow.models import ActionResult

    def act(inp, attempt):
        if attempt == 1:
            raise RuntimeError("helpdesk 503")
        return ActionResult(action_type=inp.decision.action.type, helpdesk_ref="ref-ok")

    fake = FakeActivities(decide=decide_returning(reply_tk1(), customer_id="CUST-F"), act=act)
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk1(), tq)
        res = await result(handle)

    assert res.action.action_type == "reply"
    assert res.action.helpdesk_ref == "ref-ok"
    assert fake.act_attempts == [1, 2]


# ---------------------------------------------------------------- S-20


async def test_s20_decide_fails_transiently_then_succeeds(workflow_env):
    """S-20"""
    ok = decide_returning(reply_tk1(), customer_id="CUST-F")

    def decide(inp, attempt):
        if attempt < 3:
            raise RuntimeError("llm timeout")
        return ok(inp, attempt)

    fake = FakeActivities(decide=decide)
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk1(), tq)
        res = await result(handle)

    assert res.outcome == "replied"
    assert fake.act_types == ["reply"]
    assert fake.decide_attempts == [1, 2, 3]


# ---------------------------------------------------------------- S-21


async def test_s21_unknown_customer_not_retried_and_escalated(workflow_env):
    """S-21"""
    from temporalio.exceptions import ApplicationError

    def decide(inp, attempt):
        raise ApplicationError("unknown customer CUST-X", type="UnknownCustomer", non_retryable=True)

    fake = FakeActivities(decide=decide)
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk7(), tq)
        res = await result(handle)
        status = await query_status(handle)

    assert fake.decide_attempts == [1]
    assert fake.act_types == ["escalate"]
    dec = fake.act_inputs[0].decision
    assert dec.category == "other"
    assert dec.priority == "normal"
    assert "CUST-X" in dec.action.reason
    assert fake.act_inputs[0].idempotency_key == "ticket-TK-7:escalate"
    assert res.outcome == "escalated"
    assert status.step == "completed"


# ---------------------------------------------------------------- S-22


async def test_s22_decide_exhausting_retries_escalates(workflow_env):
    """S-22"""

    def decide(inp, attempt):
        raise RuntimeError("llm down")

    fake = FakeActivities(decide=decide)
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        res = await result(handle)

    assert fake.decide_attempts == [1, 2, 3, 4, 5]
    assert fake.act_types == ["escalate"]
    assert "RuntimeError" in fake.act_inputs[0].decision.action.reason
    assert res.outcome == "escalated"


# ---------------------------------------------------------------- S-23


async def test_s23_invalid_model_output_not_retried(workflow_env):
    """S-23"""
    from temporalio.exceptions import ApplicationError

    def decide(inp, attempt):
        raise ApplicationError("model answer unreadable", type="InvalidModelOutput", non_retryable=True)

    fake = FakeActivities(decide=decide)
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        res = await result(handle)

    assert fake.decide_attempts == [1]
    assert fake.act_types == ["escalate"]
    assert "InvalidModelOutput" in fake.act_inputs[0].decision.action.reason
    assert res.outcome == "escalated"


@pytest.mark.parametrize("error_type", ["AgentLoopExceeded", "LLMRequestRejected"])
async def test_s23_other_non_retryable_decide_errors_not_retried(workflow_env, error_type):
    """S-23 / N5: every non-retryable decide error type is attempted once and escalated."""
    from temporalio.exceptions import ApplicationError

    def decide(inp, attempt):
        # deliberately NOT marked non_retryable: the retry policy must list the type
        raise ApplicationError("nope", type=error_type)

    fake = FakeActivities(decide=decide)
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        await result(handle)

    assert fake.decide_attempts == [1]
    assert fake.act_types == ["escalate"]
    assert error_type in fake.act_inputs[0].decision.action.reason


# ---------------------------------------------------------------- S-24


async def test_s24_helpdesk_permanently_down_fails_workflow(workflow_env):
    """S-24"""
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    def act(inp, attempt):
        raise RuntimeError("helpdesk down")

    fake = FakeActivities(decide=decide_returning(reply_tk1(), customer_id="CUST-F"), act=act)
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk1(), tq)
        with pytest.raises(WorkflowFailureError) as excinfo:
            await result(handle)
        status = await query_status(handle)

    cause = excinfo.value.cause
    assert isinstance(cause, ApplicationError)
    assert cause.type == "HelpdeskUnavailable"
    assert cause.non_retryable is True
    assert len(fake.act_attempts) == 10
    assert fake.act_attempts == list(range(1, 11))
    assert status.step == "failed"
    assert status.error


# ---------------------------------------------------------------- S-25


async def test_s25_rule_violation_escalates_without_waiting(workflow_env):
    """S-25"""
    proposed = fx.refund_decision("INV-A2", 5000.0, category="billing", priority="high")
    fake = FakeActivities(decide=decide_returning(proposed))
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        res = await result(handle)
        status = await query_status(handle)
        desc = await handle.describe()

    assert desc.close_time - desc.start_time < timedelta(hours=1)
    assert fake.act_types == ["escalate"]
    dec = fake.act_inputs[0].decision
    assert dec.category == "billing"
    assert dec.priority == "high"
    assert dec.action.reason.startswith("rule violation: ")
    assert "5000" in dec.action.reason
    assert res.outcome == "escalated"
    assert status.violations
    assert status.proposed_decision.action.type == "refund"
    assert status.decision.action.type == "escalate"
    assert status.approval is None


# ---------------------------------------------------------------- S-26


async def test_s26_query_exposes_decision_while_waiting(workflow_env):
    """S-26"""
    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    async with worker(workflow_env.client, fake.build()) as tq:
        handle = await start(workflow_env.client, fx.tk2(), tq)
        status = await wait_step(handle, "awaiting_approval")
        assert status.proposed_decision == fx.refund_decision()
        assert status.decision == fx.refund_decision()
        assert status.violations == []
        assert status.result is None
        assert status.approval is None
        assert status.error is None
        await approve(handle, approved=True)
        await result(handle)


# ---------------------------------------------------------------- S-62


async def test_s62_workflow_runs_in_sandbox_offline(workflow_env):
    """S-62: workflow tests run with the Temporal sandbox and without key/endpoint env vars."""
    import os

    for var in ("OPEN_ROUTER_API_KEY", "LLM_MODE", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        assert var not in os.environ
    fake = FakeActivities(decide=decide_returning(reply_tk1(), customer_id="CUST-F"))
    async with worker(workflow_env.client, fake.build()) as tq:  # SandboxedWorkflowRunner
        handle = await start(workflow_env.client, fx.tk1(), tq)
        res = await result(handle)
    assert res.outcome == "replied"
