"""Helpdesk interface and the in-process fake Helpdesk (R11, D4)."""
import random
from typing import Protocol


class HelpdeskTemporaryError(Exception):
    """A temporary Helpdesk failure; the activity retry policy retries it."""


class Helpdesk(Protocol):
    def was_seen(self, idempotency_key: str) -> bool: ...
    async def send_reply(self, ticket_id: str, message: str, article_ids: list[str], *, idempotency_key: str) -> str: ...
    async def issue_refund(self, ticket_id: str, invoice_id: str, amount_eur: float, *, idempotency_key: str) -> str: ...
    async def escalate(self, ticket_id: str, reason: str, *, idempotency_key: str) -> str: ...
    async def close(self, ticket_id: str, reason: str, *, idempotency_key: str) -> str: ...


class FakeHelpdesk:
    """Remembers idempotency key -> helpdesk_ref; a repeated key returns the stored ref without a side effect.

    State is in memory, so it is lost when the worker restarts (documented limitation).
    """

    def __init__(self, failure_rate: float = 0.0, rng: random.Random | None = None):
        if not 0.0 <= failure_rate <= 1.0:
            raise ValueError(f"failure_rate must be in [0, 1], got {failure_rate}")
        self.failure_rate = failure_rate
        self._rng = rng or random.Random()
        self._refs: dict[str, str] = {}
        self._fail_before = 0
        self._fail_after = 0
        self.refunds: list[tuple[str, float]] = []
        self.actions: list[dict] = []

    def fail_next(self, n: int = 1, *, after_commit: bool = False) -> None:
        """Test hook: fail the next n new calls, before or after the side effect (lost response)."""
        if after_commit:
            self._fail_after += n
        else:
            self._fail_before += n

    def was_seen(self, idempotency_key: str) -> bool:
        return idempotency_key in self._refs

    async def send_reply(self, ticket_id, message, article_ids, *, idempotency_key) -> str:
        return self._perform("reply", ticket_id, idempotency_key, message=message, article_ids=list(article_ids))

    async def issue_refund(self, ticket_id, invoice_id, amount_eur, *, idempotency_key) -> str:
        return self._perform("refund", ticket_id, idempotency_key, invoice_id=invoice_id, amount_eur=amount_eur)

    async def escalate(self, ticket_id, reason, *, idempotency_key) -> str:
        return self._perform("escalate", ticket_id, idempotency_key, reason=reason)

    async def close(self, ticket_id, reason, *, idempotency_key) -> str:
        return self._perform("close", ticket_id, idempotency_key, reason=reason)

    def _perform(self, kind: str, ticket_id: str, idempotency_key: str, **payload) -> str:
        if idempotency_key in self._refs:
            return self._refs[idempotency_key]
        if self._fail_before:
            self._fail_before -= 1
            raise HelpdeskTemporaryError("helpdesk temporarily unavailable (planned)")
        if self._rng.random() < self.failure_rate:
            raise HelpdeskTemporaryError("helpdesk temporarily unavailable")
        ref = f"HD-{len(self.actions) + 1:04d}"
        self.actions.append({"kind": kind, "ticket_id": ticket_id, "idempotency_key": idempotency_key, "ref": ref, **payload})
        if kind == "refund":
            self.refunds.append((payload["invoice_id"], payload["amount_eur"]))
        self._refs[idempotency_key] = ref
        if self._fail_after:
            self._fail_after -= 1
            raise HelpdeskTemporaryError("helpdesk response lost (planned)")
        return ref
