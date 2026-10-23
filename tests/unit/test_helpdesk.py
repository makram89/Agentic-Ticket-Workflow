"""FakeHelpdesk: S-29 (failure rate) and idempotency basics (R11)."""
import random

import pytest


async def test_s29_failure_rate_one_always_fails_zero_never():
    """S-29"""
    from ticketflow.services.helpdesk import FakeHelpdesk, HelpdeskTemporaryError

    always = FakeHelpdesk(failure_rate=1.0, rng=random.Random(7))
    never = FakeHelpdesk(failure_rate=0.0, rng=random.Random(7))
    for i in range(20):
        with pytest.raises(HelpdeskTemporaryError):
            await always.send_reply("TK-1", "hello", [], idempotency_key=f"wf-{i}:reply")
        ref = await never.send_reply("TK-1", "hello", [], idempotency_key=f"wf-{i}:reply")
        assert isinstance(ref, str) and ref
    assert len(never.actions) == 20
    assert always.actions == []


async def test_s29_repeated_key_returns_stored_ref_without_side_effect():
    """S-29 / R11: repeated idempotency key -> stored result, no repeated side effect."""
    from ticketflow.services.helpdesk import FakeHelpdesk

    hd = FakeHelpdesk(failure_rate=0.0)
    first = await hd.issue_refund("TK-2", "INV-A2", 50.0, idempotency_key="ticket-TK-2:refund")
    again = await hd.issue_refund("TK-2", "INV-A2", 50.0, idempotency_key="ticket-TK-2:refund")
    assert first == again
    assert hd.refunds == [("INV-A2", 50.0)]
    assert hd.was_seen("ticket-TK-2:refund")
    assert not hd.was_seen("ticket-TK-9:refund")


async def test_s29_fail_next_before_and_after_commit():
    """S-29 / S-27 test hook: planned failures before and after the side effect."""
    from ticketflow.services.helpdesk import FakeHelpdesk, HelpdeskTemporaryError

    hd = FakeHelpdesk(failure_rate=0.0)
    hd.fail_next(1)
    with pytest.raises(HelpdeskTemporaryError):
        await hd.escalate("TK-3", "outage", idempotency_key="ticket-TK-3:escalate")
    assert not hd.was_seen("ticket-TK-3:escalate")
    assert hd.actions == []

    hd.fail_next(1, after_commit=True)
    with pytest.raises(HelpdeskTemporaryError):
        await hd.close("TK-5", "spam", idempotency_key="ticket-TK-5:close")
    assert hd.was_seen("ticket-TK-5:close")
    assert len(hd.actions) == 1
    await hd.close("TK-5", "spam", idempotency_key="ticket-TK-5:close")
    assert len(hd.actions) == 1
