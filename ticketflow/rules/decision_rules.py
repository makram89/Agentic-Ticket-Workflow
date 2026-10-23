"""Step 2: check the model's proposed decision in code (spec R9).

Pure function: no I/O, no framework imports. Every input that matters (the ticket's customer,
the customer's invoices, the known KB ids) comes from the caller, never from the model.
"""
from collections.abc import Collection, Sequence

from ticketflow.models import (
    ALLOWED_ACTIONS,
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    Action,
    CheckResult,
    Decision,
    Invoice,
)


def check_decision(
    proposed: Decision,
    *,
    customer_id: str,
    invoices: Sequence[Invoice],
    known_article_ids: Collection[str],
) -> CheckResult:
    """Return the proposed decision unchanged, or an escalation listing every violation."""
    violations: list[str] = []
    if proposed.category not in ALLOWED_CATEGORIES:
        violations.append(f"category {proposed.category!r} is not allowed")
    if proposed.priority not in ALLOWED_PRIORITIES:
        violations.append(f"priority {proposed.priority!r} is not allowed")

    action = proposed.action
    if action.type not in ALLOWED_ACTIONS:
        violations.append(f"action type {action.type!r} is not allowed")
    elif action.type == "reply":
        if not (action.message or "").strip():
            violations.append("reply needs a non-empty message")
        for article_id in action.article_ids:
            if article_id not in known_article_ids:
                violations.append(f"unknown KB article {article_id!r}")
    elif action.type == "refund":
        violations.extend(_refund_violations(action, customer_id, invoices))
    elif not (action.reason or "").strip():
        violations.append(f"{action.type} needs a non-empty reason")

    if not violations:
        return CheckResult(decision=proposed, violations=[])
    escalation = Decision(
        category=proposed.category if proposed.category in ALLOWED_CATEGORIES else "other",
        priority=proposed.priority if proposed.priority in ALLOWED_PRIORITIES else "normal",
        action=Action(type="escalate", reason="rule violation: " + "; ".join(violations)),
    )
    return CheckResult(decision=escalation, violations=violations)


def _refund_violations(action: Action, customer_id: str, invoices: Sequence[Invoice]) -> list[str]:
    violations: list[str] = []
    invoice = None
    if not action.invoice_id:
        violations.append("refund needs an invoice_id")
    else:
        invoice = next((i for i in invoices if i.invoice_id == action.invoice_id), None)
        if invoice is None:
            violations.append(f"unknown invoice {action.invoice_id!r}")
        else:
            if invoice.customer_id != customer_id:
                violations.append(f"invoice {invoice.invoice_id} does not belong to customer {customer_id}")
            if invoice.status != "paid":
                violations.append(
                    f"invoice {invoice.invoice_id} has status {invoice.status!r}; only paid invoices can be refunded"
                )

    amount = action.amount_eur
    if amount is None:
        violations.append("refund needs amount_eur")
    elif amount <= 0:
        violations.append(f"refund amount {amount:.2f} EUR must be positive")
    elif invoice is not None and amount > invoice.amount_eur:
        violations.append(
            f"refund amount {amount:.2f} EUR exceeds invoice {invoice.invoice_id} amount {invoice.amount_eur:.2f} EUR"
        )
    return violations
