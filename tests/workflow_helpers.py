"""Helpers for workflow-env tests: fake activities, worker/start helpers, bounded polling.

No `from __future__ import annotations` here: the fake activities' type hints must be real
classes so Temporal's pydantic converter can (de)serialise them.
"""
import asyncio
import uuid
from contextlib import asynccontextmanager

RESULT_TIMEOUT = 60


class FakeActivities:
    """Fake `decide_ticket` / `execute_action` with the real signatures.

    decide(inp, attempt) -> DecideOutput | raises ; act(inp, attempt) -> ActionResult | raises
    """

    def __init__(self, decide=None, act=None):
        self._decide = decide
        self._act = act
        self.decide_attempts = []
        self.decide_inputs = []
        self.act_attempts = []
        self.act_inputs = []

    def build(self):
        from temporalio import activity

        from ticketflow.models import ActionResult, DecideInput, DecideOutput, ExecuteActionInput

        fake = self

        @activity.defn(name="decide_ticket")
        async def decide_ticket(inp: DecideInput) -> DecideOutput:
            attempt = activity.info().attempt
            fake.decide_attempts.append(attempt)
            fake.decide_inputs.append(inp)
            result = fake._decide(inp, attempt)
            if asyncio.iscoroutine(result):
                result = await result
            return result

        @activity.defn(name="execute_action")
        async def execute_action(inp: ExecuteActionInput) -> ActionResult:
            attempt = activity.info().attempt
            fake.act_attempts.append(attempt)
            fake.act_inputs.append(inp)
            if fake._act is None:
                return ActionResult(action_type=inp.decision.action.type, helpdesk_ref=f"ref-{len(fake.act_inputs)}")
            result = fake._act(inp, attempt)
            if asyncio.iscoroutine(result):
                result = await result
            return result

        return [decide_ticket, execute_action]

    @property
    def act_types(self):
        return [i.decision.action.type for i in self.act_inputs]


def decide_returning(decision, customer_id="CUST-A"):
    """decide callable returning `decision` with the fixture invoices of `customer_id` and all KB ids."""

    def _decide(inp, attempt):
        from tests import fixtures as fx
        from ticketflow.models import DecideOutput

        return DecideOutput(
            proposed=decision,
            invoices=fx.invoices_of(customer_id),
            known_article_ids=list(fx.KB_IDS),
        )

    return _decide


def task_queue():
    return f"tq-{uuid.uuid4()}"


@asynccontextmanager
async def worker(client, activities, tq=None, **kwargs):
    from temporalio.worker import Worker
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from ticketflow.workflow.ticket_workflow import TicketWorkflow

    tq = tq or task_queue()
    async with Worker(
        client,
        task_queue=tq,
        workflows=[TicketWorkflow],
        activities=activities,
        workflow_runner=SandboxedWorkflowRunner(),
        **kwargs,
    ):
        yield tq


async def start(client, ticket, tq, **kwargs):
    from ticketflow.models import WorkflowInput
    from ticketflow.workflow.ticket_workflow import TicketWorkflow

    return await client.start_workflow(
        TicketWorkflow.run,
        WorkflowInput(ticket=ticket),
        id=f"ticket-{ticket.ticket_id}",
        task_queue=tq,
        **kwargs,
    )


async def result(handle, timeout=RESULT_TIMEOUT):
    return await asyncio.wait_for(handle.result(), timeout=timeout)


async def query_status(handle):
    from ticketflow.workflow.ticket_workflow import TicketWorkflow

    return await handle.query(TicketWorkflow.status)


async def wait_step(handle, steps, tries=100, delay=0.05):
    """Poll the `status` query (bounded) until its step is one of `steps`; returns the status."""
    if isinstance(steps, str):
        steps = (steps,)
    last = None
    last_exc = None
    for _ in range(tries):
        try:
            last = await query_status(handle)
            if last.step in steps:
                return last
        except Exception as exc:  # workflow task not processed yet
            last_exc = exc
        await asyncio.sleep(delay)
    raise AssertionError(f"step never reached {steps}; last status={last!r}; last error={last_exc!r}")


async def approve(handle, approved=True, reviewer=None, comment=None):
    from ticketflow.models import Approval
    from ticketflow.workflow.ticket_workflow import TicketWorkflow

    await handle.signal(TicketWorkflow.approval, Approval(approved=approved, reviewer=reviewer, comment=comment))
