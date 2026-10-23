"""Shared pydantic models and the allowed-value constants (system rules, not seed data).

Only pydantic is imported here so the module is safe inside the Temporal workflow sandbox
and usable by the framework-free rules module.
"""
from typing import Literal

from pydantic import BaseModel, Field

ALLOWED_CATEGORIES = ("account", "billing", "technical", "other")
ALLOWED_PRIORITIES = ("low", "normal", "high", "urgent")
ALLOWED_ACTIONS = ("reply", "refund", "escalate", "close")


class Ticket(BaseModel):
    ticket_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    customer_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


class Customer(BaseModel):
    customer_id: str
    name: str
    plan: str
    status: str


class Invoice(BaseModel):
    invoice_id: str
    customer_id: str
    month: str
    amount_eur: float
    status: str


class KbArticle(BaseModel):
    article_id: str
    title: str
    text: str


class Action(BaseModel):
    """Shape only; allowed values and refund rules are checked by `ticketflow.rules`."""

    type: str
    message: str | None = None
    article_ids: list[str] = []
    invoice_id: str | None = None
    amount_eur: float | None = None
    reason: str | None = None


class Decision(BaseModel):
    category: str
    priority: str
    action: Action


class CheckResult(BaseModel):
    decision: Decision
    violations: list[str]


class DecideInput(BaseModel):
    ticket: Ticket
    workflow_id: str


class DecideOutput(BaseModel):
    proposed: Decision
    invoices: list[Invoice]
    known_article_ids: list[str]


class ExecuteActionInput(BaseModel):
    ticket_id: str
    workflow_id: str
    idempotency_key: str
    decision: Decision


class ActionResult(BaseModel):
    action_type: str
    helpdesk_ref: str
    duplicate: bool = False


class Approval(BaseModel):
    approved: bool
    reviewer: str | None = None
    comment: str | None = None


class TicketResult(BaseModel):
    outcome: Literal["replied", "refunded", "refund_declined", "escalated", "closed"]
    action: ActionResult


class WorkflowInput(BaseModel):
    ticket: Ticket
    approval_timeout_seconds: int = 86400


class TicketStatus(BaseModel):
    ticket_id: str
    workflow_id: str
    step: str
    proposed_decision: Decision | None = None
    decision: Decision | None = None
    violations: list[str] = []
    approval: Approval | None = None
    result: TicketResult | None = None
    error: str | None = None
