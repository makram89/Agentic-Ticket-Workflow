"""TicketService: what the HTTP layer needs from Temporal (start, status, approval)."""
from typing import Protocol

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from ticketflow.models import Approval, Ticket, TicketStatus, WorkflowInput
from ticketflow.workflow.ticket_workflow import AWAITING_APPROVAL, TicketWorkflow


class NotFound(Exception):
    """No workflow for this ticket."""


class NotAwaiting(Exception):
    """The ticket's workflow is not in step awaiting_approval."""


def workflow_id_for(ticket_id: str) -> str:
    return f"ticket-{ticket_id}"


class TicketService(Protocol):
    async def start_ticket(self, ticket: Ticket) -> tuple[str, bool]: ...
    async def get_status(self, ticket_id: str) -> tuple[TicketStatus, str] | None: ...
    async def send_approval(self, ticket_id: str, approval: Approval) -> None: ...


class TemporalTicketService:
    def __init__(self, client: Client, task_queue: str = "tickets", approval_timeout_seconds: int = 86400):
        self._client = client
        self._task_queue = task_queue
        self._approval_timeout_seconds = approval_timeout_seconds

    async def start_ticket(self, ticket: Ticket) -> tuple[str, bool]:
        """Start the workflow; a ticket_id that already has a workflow (running or closed) is not re-run (D13)."""
        workflow_id = workflow_id_for(ticket.ticket_id)
        try:
            await self._client.start_workflow(
                TicketWorkflow.run,
                WorkflowInput(ticket=ticket, approval_timeout_seconds=self._approval_timeout_seconds),
                id=workflow_id,
                task_queue=self._task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return workflow_id, False
        return workflow_id, True

    async def get_status(self, ticket_id: str) -> tuple[TicketStatus, str] | None:
        handle = self._client.get_workflow_handle(workflow_id_for(ticket_id))
        try:
            desc = await handle.describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return None
            raise
        status = await handle.query(TicketWorkflow.status)
        return status, desc.status.name

    async def send_approval(self, ticket_id: str, approval: Approval) -> None:
        found = await self.get_status(ticket_id)
        if found is None:
            raise NotFound(ticket_id)
        if found[0].step != AWAITING_APPROVAL:
            raise NotAwaiting(ticket_id)
        handle = self._client.get_workflow_handle(workflow_id_for(ticket_id))
        try:
            await handle.signal(TicketWorkflow.approval, approval)
        except RPCError as exc:
            # The workflow closed between the check above and the signal.
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise NotAwaiting(ticket_id) from exc
            raise
