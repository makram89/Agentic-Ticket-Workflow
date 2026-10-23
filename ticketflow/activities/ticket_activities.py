"""Activities: decide_ticket (agent loop, D7) and execute_action (Helpdesk, idempotent)."""
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ticketflow.activities.agent import run_agent
from ticketflow.data.repository import DataRepo
from ticketflow.models import ActionResult, DecideInput, DecideOutput, ExecuteActionInput
from ticketflow.services.helpdesk import Helpdesk
from ticketflow.services.llm.base import LLMProvider


class TicketActivities:
    def __init__(self, llm: LLMProvider, helpdesk: Helpdesk, repo: DataRepo, *, max_turns: int = 8,
                 max_invalid: int = 3):
        self._llm = llm
        self._helpdesk = helpdesk
        self._repo = repo
        self._max_turns = max_turns
        self._max_invalid = max_invalid

    @activity.defn(name="decide_ticket")
    async def decide_ticket(self, inp: DecideInput) -> DecideOutput:
        ticket = inp.ticket
        ids = {"ticket_id": ticket.ticket_id, "workflow_id": inp.workflow_id}
        if self._repo.get_customer(ticket.customer_id) is None:
            activity.logger.warning("unknown customer %s", ticket.customer_id, extra=ids)
            raise ApplicationError(
                f"unknown customer {ticket.customer_id}", type="UnknownCustomer", non_retryable=True
            )
        activity.logger.info("asking the model for a decision", extra=ids)
        proposed = await run_agent(
            ticket, self._llm, self._repo, max_turns=self._max_turns, max_invalid=self._max_invalid
        )
        activity.logger.info("model proposed action %s", proposed.action.type, extra=ids)
        # The rule inputs come from the data-access module, never from the model (D8).
        return DecideOutput(
            proposed=proposed,
            invoices=self._repo.get_invoices(ticket.customer_id),
            known_article_ids=self._repo.known_article_ids(),
        )

    @activity.defn(name="execute_action")
    async def execute_action(self, inp: ExecuteActionInput) -> ActionResult:
        action = inp.decision.action
        key = inp.idempotency_key
        ids = {"ticket_id": inp.ticket_id, "workflow_id": inp.workflow_id}
        duplicate = self._helpdesk.was_seen(key)
        if action.type == "reply":
            ref = await self._helpdesk.send_reply(inp.ticket_id, action.message, action.article_ids,
                                                  idempotency_key=key)
        elif action.type == "refund":
            ref = await self._helpdesk.issue_refund(inp.ticket_id, action.invoice_id, action.amount_eur,
                                                    idempotency_key=key)
        elif action.type == "escalate":
            ref = await self._helpdesk.escalate(inp.ticket_id, action.reason, idempotency_key=key)
        elif action.type == "close":
            ref = await self._helpdesk.close(inp.ticket_id, action.reason, idempotency_key=key)
        else:
            raise ApplicationError(f"unsupported action {action.type!r}", type="UnsupportedAction",
                                   non_retryable=True)
        activity.logger.info("helpdesk %s done (ref %s, duplicate=%s)", action.type, ref, duplicate, extra=ids)
        return ActionResult(action_type=action.type, helpdesk_ref=ref, duplicate=duplicate)
