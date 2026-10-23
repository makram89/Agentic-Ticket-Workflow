"""TicketWorkflow: decide -> check -> [wait for approval] -> act (R5, R8-R12).

Deterministic code only: no I/O, randomness or wall-clock; the rule check is a pure function.
"""
import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from ticketflow.models import (
        Action,
        ActionResult,
        Approval,
        DecideInput,
        DecideOutput,
        Decision,
        ExecuteActionInput,
        TicketResult,
        TicketStatus,
        WorkflowInput,
    )
    from ticketflow.rules.decision_rules import check_decision

RECEIVED, DECIDING, CHECKING, AWAITING_APPROVAL, ACTING, COMPLETED, FAILED = (
    "received", "deciding", "checking", "awaiting_approval", "acting", "completed", "failed",
)

DECIDE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=["UnknownCustomer", "InvalidModelOutput", "AgentLoopExceeded", "LLMRequestRejected"],
)
ACT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=10,
)

OUTCOMES = {"reply": "replied", "refund": "refunded", "escalate": "escalated", "close": "closed"}


def _describe(err: ActivityError) -> str:
    cause = err.cause
    if isinstance(cause, ApplicationError):
        return f"{cause.type}: {cause.message}"
    return f"{type(cause).__name__}: {cause}" if cause else str(err)


@workflow.defn(name="TicketWorkflow")
class TicketWorkflow:
    @workflow.init
    def __init__(self, inp: WorkflowInput) -> None:
        self._ticket = inp.ticket
        self._workflow_id = workflow.info().workflow_id
        self._step = RECEIVED
        self._proposed: Decision | None = None
        self._decision: Decision | None = None
        self._violations: list[str] = []
        self._approval: Approval | None = None
        self._result: TicketResult | None = None
        self._error: str | None = None

    def _log(self, msg: str, *args, level: str = "info") -> None:
        getattr(workflow.logger, level)(msg, *args, extra={"ticket_id": self._ticket.ticket_id})

    @workflow.run
    async def run(self, inp: WorkflowInput) -> TicketResult:
        ticket = inp.ticket
        self._log("ticket received")

        # 1. Decide (LLM agent activity). Any failure after retries becomes an escalation (R8).
        self._step = DECIDING
        try:
            out = await workflow.execute_activity(
                "decide_ticket",
                DecideInput(ticket=ticket, workflow_id=self._workflow_id),
                result_type=DecideOutput,
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=DECIDE_RETRY,
            )
        except ActivityError as err:
            reason = f"decision step failed: {_describe(err)}"
            self._log("decide failed, escalating: %s", reason, level="warning")
            self._decision = Decision(category="other", priority="normal", action=Action(type="escalate", reason=reason))
        else:
            # 2. Check the proposed decision in code, whatever the model said (R9).
            self._step = CHECKING
            self._proposed = out.proposed
            checked = check_decision(
                out.proposed,
                customer_id=ticket.customer_id,
                invoices=out.invoices,
                known_article_ids=out.known_article_ids,
            )
            self._decision, self._violations = checked.decision, checked.violations
            if checked.violations:
                self._log("rule violations, escalating: %s", "; ".join(checked.violations), level="warning")

        # 3. Refunds only: wait for a human approval (R10).
        final = self._decision
        outcome = OUTCOMES[final.action.type]
        if final.action.type == "refund":
            self._step = AWAITING_APPROVAL
            self._log("waiting for refund approval")
            try:
                await workflow.wait_condition(
                    lambda: self._approval is not None,
                    timeout=timedelta(seconds=inp.approval_timeout_seconds),
                )
            except asyncio.TimeoutError:
                hours = inp.approval_timeout_seconds / 3600
                final = Decision(
                    category=final.category,
                    priority=final.priority,
                    action=Action(type="escalate", reason=f"refund approval timed out after {hours:g}h"),
                )
                outcome = "escalated"
                self._log("refund approval timed out, escalating", level="warning")
            else:
                if not self._approval.approved:
                    final = Decision(category=final.category, priority=final.priority,
                                     action=Action(type="reply", message=self._decline_message(final.action),
                                                   article_ids=[]))
                    outcome = "refund_declined"
                self._log("refund %s by reviewer", "approved" if self._approval.approved else "rejected")

        # 4. Act through the Helpdesk (idempotent per workflow and action type).
        self._step = ACTING
        try:
            action_result = await workflow.execute_activity(
                "execute_action",
                ExecuteActionInput(
                    ticket_id=ticket.ticket_id,
                    workflow_id=self._workflow_id,
                    idempotency_key=f"{self._workflow_id}:{final.action.type}",
                    decision=final,
                ),
                result_type=ActionResult,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACT_RETRY,
            )
        except ActivityError as err:
            self._step = FAILED
            self._error = f"helpdesk action {final.action.type} failed after retries: {_describe(err)}"
            self._log("%s", self._error, level="error")
            raise ApplicationError(self._error, type="HelpdeskUnavailable", non_retryable=True) from err

        self._result = TicketResult(outcome=outcome, action=action_result)
        self._step = COMPLETED
        self._log("ticket completed: %s", outcome)
        return self._result

    def _decline_message(self, refund: Action) -> str:
        message = (
            f"Your refund request for invoice {refund.invoice_id} ({refund.amount_eur:.2f} EUR) "
            "was reviewed and not approved."
        )
        if self._approval and self._approval.comment:
            message += f" Reviewer comment: {self._approval.comment}"
        return message

    @workflow.signal(name="approval")
    def approval(self, approval: Approval) -> None:
        # Only the first approval received while awaiting counts (R10, N2).
        if self._step != AWAITING_APPROVAL or self._approval is not None:
            self._log("ignoring approval signal in step %s", self._step)
            return
        self._approval = approval

    @workflow.query(name="status")
    def status(self) -> TicketStatus:
        return TicketStatus(
            ticket_id=self._ticket.ticket_id,
            workflow_id=self._workflow_id,
            step=self._step,
            proposed_decision=self._proposed,
            decision=self._decision,
            violations=self._violations,
            approval=self._approval,
            result=self._result,
            error=self._error,
        )
