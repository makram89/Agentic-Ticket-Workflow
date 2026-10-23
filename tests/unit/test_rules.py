"""Pure rule checks (spec R9): scenarios S-42 .. S-54."""
import pytest

from tests import fixtures as fx


def check(dec, customer_id="CUST-A", invoices=None):
    from ticketflow.rules.decision_rules import check_decision

    if invoices is None:
        invoices = fx.invoices_of(customer_id)
    return check_decision(dec, customer_id=customer_id, invoices=invoices, known_article_ids=set(fx.KB_IDS))


def assert_escalated(res):
    assert res.violations, "expected at least one violation"
    assert res.decision.action.type == "escalate"
    assert res.decision.action.reason
    assert res.decision.action.reason.startswith("rule violation: ")
    assert res.decision.action.reason == "rule violation: " + "; ".join(res.violations)


@pytest.mark.parametrize(
    "make",
    [
        lambda: fx.reply_decision(article_ids=["KB-PW"]),
        lambda: fx.refund_decision("INV-A2", 50.0),
        lambda: fx.refund_decision("INV-A2", 0.01),
        lambda: fx.escalate_decision(reason="outage needs a human"),
        lambda: fx.close_decision(reason="spam"),
    ],
    ids=["reply", "refund-full", "refund-cent", "escalate", "close"],
)
def test_s42_valid_decisions_pass_unchanged(make):
    """S-42"""
    proposed = make()
    res = check(proposed)
    assert res.violations == []
    assert res.decision == proposed


def test_s43_bad_category_escalates_with_fallback_other():
    """S-43"""
    res = check(fx.reply_decision(category="sales", priority="high"))
    assert_escalated(res)
    assert any("category" in v for v in res.violations)
    assert res.decision.category == "other"
    assert res.decision.priority == "high"


def test_s44_bad_priority_escalates_with_fallback_normal():
    """S-44"""
    res = check(fx.reply_decision(category="account", priority="critical"))
    assert_escalated(res)
    assert any("priority" in v for v in res.violations)
    assert res.decision.priority == "normal"
    assert res.decision.category == "account"


def test_s45_bad_action_type_escalates():
    """S-45"""
    proposed = fx.decision("account", "normal", fx.action("delete_account", reason="please"))
    res = check(proposed)
    assert_escalated(res)
    assert any("action" in v for v in res.violations)


def test_s46_refund_of_other_customers_invoice_escalates():
    """S-46"""
    res = check(fx.refund_decision("INV-B1", 10.0), customer_id="CUST-A", invoices=fx.invoices())
    assert_escalated(res)
    assert any("INV-B1" in v and "customer" in v.lower() for v in res.violations)


def test_s47_refund_over_invoice_amount_and_boundary():
    """S-47"""
    over = check(fx.refund_decision("INV-A2", 50.01))
    assert_escalated(over)
    assert any("50.01" in v for v in over.violations)

    exact = check(fx.refund_decision("INV-A2", 50.0))
    assert exact.violations == []
    assert exact.decision.action.type == "refund"


@pytest.mark.parametrize("amount", [0, 0.0, -5, -5.0])
def test_s48_non_positive_refund_amount_escalates(amount):
    """S-48"""
    assert_escalated(check(fx.refund_decision("INV-A2", amount)))


@pytest.mark.parametrize(
    "make",
    [
        lambda: fx.refund_decision("INV-NOPE", 10.0),
        lambda: fx.decision("billing", "normal", fx.action("refund", invoice_id=None, amount_eur=10.0)),
        lambda: fx.decision("billing", "normal", fx.action("refund", invoice_id="INV-A2", amount_eur=None)),
    ],
    ids=["unknown-invoice", "no-invoice-id", "no-amount"],
)
def test_s49_unknown_or_missing_invoice_escalates(make):
    """S-49"""
    assert_escalated(check(make()))


def test_s50_refund_of_non_paid_invoice_escalates():
    """S-50"""
    res = check(fx.refund_decision("INV-S1", 50.0), customer_id="CUST-S")
    assert_escalated(res)
    assert any("status" in v.lower() for v in res.violations)


def test_s51_injected_oversized_refund_escalates():
    """S-51"""
    res = check(fx.refund_decision("INV-A2", 5000.0))
    assert_escalated(res)
    assert res.decision.action.reason.startswith("rule violation: ")


def test_s52_reply_checks():
    """S-52"""
    assert_escalated(check(fx.reply_decision(message="", article_ids=[])))
    assert_escalated(check(fx.reply_decision(message="see article", article_ids=["KB-NOPE"])))
    ok = check(fx.reply_decision(message="Thanks, we are on it.", article_ids=[]))
    assert ok.violations == []
    assert ok.decision.action.type == "reply"


def test_s53_escalate_and_close_need_a_reason():
    """S-53"""
    for proposed in (
        fx.decision("other", "low", fx.action("close", reason="")),
        fx.decision("technical", "high", fx.action("escalate", reason=None)),
    ):
        res = check(proposed)
        assert_escalated(res)
        assert res.decision.action.reason.strip() != "rule violation:"


def test_s54_all_violations_are_listed():
    """S-54"""
    proposed = fx.decision("x", "y", fx.action("refund", invoice_id="INV-B1", amount_eur=5000.0))
    res = check(proposed, customer_id="CUST-A", invoices=fx.invoices())
    assert len(res.violations) >= 4
    assert_escalated(res)
    for v in res.violations:
        assert v in res.decision.action.reason
    assert res.decision.category == "other"
    assert res.decision.priority == "normal"
