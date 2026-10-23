"""Deterministic keyword-rule stand-in for the LLM (LLM_MODE=fake, D1, design "Fake LLM rules").

It speaks exactly the OpenAI-chat shape of `OpenRouterLLM`: it reads the ticket from the
`<ticket>` block of the user message and the tool results so far, and answers with tool calls.
KB articles are only ever taken from `search_kb` results.
"""
import json
import re

from ticketflow.services.llm.base import LLMReply, make_reply

BILLING_WORDS = ("refund", "charged", "invoice", "payment", "bill")
TECHNICAL_WORDS = ("error", "errors", "down", "outage", "api")
ACCOUNT_WORDS = ("password", "log in", "login", "sign in", "locked")
HIGH_WORDS = ("right now", "asap", "immediately")

_TICKET_RE = re.compile(r"<ticket>\nSubject: (.*?)\nBody: (.*)\n</ticket>", re.S)
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:eur|€)")


def _has(text: str, words) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


class FakeLLM:
    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        user = next(m["content"] for m in messages if m["role"] == "user")
        match = _TICKET_RE.search(user)
        subject, body = match.groups() if match else ("", user)
        results = _tool_results(messages)
        turn = sum(1 for m in messages if m["role"] == "assistant")

        def call(name: str, args: dict) -> LLMReply:
            return make_reply(None, [(f"fake-{turn}-0", name, json.dumps(args))])

        text = f"{subject} {body}".lower()
        customer = results.get("get_customer")
        suspended = bool(customer) and customer.get("status") == "suspended"
        billing = _has(text, BILLING_WORDS)

        if not results:
            return call("get_customer", {})
        if (billing or suspended) and "get_invoices" not in results:
            return call("get_invoices", {})
        if "search_kb" not in results:
            query = f"{subject} {body}" + (" suspended overdue" if suspended else "")
            return call("search_kb", {"query": query})
        decision = _decide(text, billing, suspended, results.get("get_invoices") or [], results["search_kb"] or [])
        return call("submit_decision", decision)


def _tool_results(messages: list[dict]) -> dict:
    """Tool name -> parsed result of the latest call of that tool."""
    names = {
        tc["id"]: tc["function"]["name"]
        for m in messages
        if m["role"] == "assistant"
        for tc in m.get("tool_calls") or []
    }
    results = {}
    for m in messages:
        if m["role"] == "tool" and m.get("tool_call_id") in names:
            try:
                results[names[m["tool_call_id"]]] = json.loads(m["content"])
            except ValueError:
                results[names[m["tool_call_id"]]] = None
    return results


def _decide(text: str, billing: bool, suspended: bool, invoices: list[dict], hits: list[dict]) -> dict:
    top = hits[0]["article_id"] if hits else None
    cited = [top] if top else []
    technical = _has(text, TECHNICAL_WORDS)
    account = _has(text, ACCOUNT_WORDS)
    off_topic = not (billing or suspended or technical or account)

    if _has(text, ("urgent",)):
        priority = "urgent"
    elif _has(text, HIGH_WORDS) or suspended:
        priority = "high"
    elif off_topic:
        priority = "low"
    else:
        priority = "normal"

    def decision(category: str, **action) -> dict:
        return {"category": category, "priority": priority, "action": action}

    def reply(category: str, message: str) -> dict:
        return decision(category, type="reply", message=message, article_ids=cited)

    if billing:
        paid = [i for i in invoices if i.get("status") == "paid"]
        amount = _AMOUNT_RE.search(text)
        if amount and paid:
            latest = max(paid, key=lambda i: (i["month"], i["invoice_id"]))
            return decision("billing", type="refund", invoice_id=latest["invoice_id"], amount_eur=float(amount.group(1)))
        if _has(text, ("twice",)):
            for inv in sorted(paid, key=lambda i: i["invoice_id"], reverse=True):
                if any(o is not inv and (o["month"], o["amount_eur"]) == (inv["month"], inv["amount_eur"]) for o in paid):
                    return decision("billing", type="refund", invoice_id=inv["invoice_id"], amount_eur=inv["amount_eur"])
        return reply("billing", "Thanks for contacting us about billing. Please see the linked article.")
    if suspended:
        overdue = ", ".join(
            f"{i['invoice_id']} ({i['amount_eur']:.2f} EUR)" for i in invoices if i.get("status") == "overdue"
        )
        return reply("account", f"Your account is suspended because of overdue invoices: {overdue}. "
                                "Pay them to unlock the account.")
    if technical:
        if priority == "urgent":
            return decision("technical", type="escalate",
                            reason="urgent technical issue reported" + (f"; see {top}" if top else ""))
        if top:
            return reply("technical", "Sorry for the trouble. Please see the linked article.")
        return decision("technical", type="escalate", reason="technical issue without a matching KB article")
    if account:
        if top:
            return reply("account", "Please follow the steps in the linked article.")
        return decision("account", type="escalate", reason="account issue without a matching KB article")
    return decision("other", type="close", reason="off-topic, not a support request")
