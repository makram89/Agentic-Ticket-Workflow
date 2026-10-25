"""TemporalTicketService against the workflow env with fake activities: S-10, S-11."""
import asyncio

from tests import fixtures as fx
from tests.workflow_helpers import FakeActivities, decide_returning, result, worker


async def test_s10_duplicate_start_against_real_temporal(workflow_env):
    """S-10"""
    from ticketflow.api.service import TemporalTicketService

    fake = FakeActivities(
        decide=decide_returning(fx.reply_decision(article_ids=["KB-PW"]), customer_id="CUST-F")
    )
    async with worker(workflow_env.client, fake.build()) as tq:
        service = TemporalTicketService(workflow_env.client, task_queue=tq)
        wid1, created1 = await service.start_ticket(fx.tk1())
        handle = workflow_env.client.get_workflow_handle(wid1)
        run_id = (await handle.describe()).run_id
        await result(handle)

        changed = fx.ticket("TK-1", "CUST-F", "Can't log in", "a different body")
        wid2, created2 = await service.start_ticket(changed)
        desc = await workflow_env.client.get_workflow_handle(wid2).describe()

    assert (wid1, created1) == ("ticket-TK-1", True)
    assert (wid2, created2) == ("ticket-TK-1", False)
    assert desc.run_id == run_id
    assert desc.status.name == "COMPLETED"
    assert len(fake.act_inputs) == 1
    assert len(fake.decide_inputs) == 1


async def test_s10_get_status_unknown_and_known(workflow_env):
    """S-10 / R3: get_status returns None for unknown tickets, (status, workflow_status) otherwise."""
    from ticketflow.api.service import TemporalTicketService

    fake = FakeActivities(
        decide=decide_returning(fx.reply_decision(article_ids=["KB-PW"]), customer_id="CUST-F")
    )
    async with worker(workflow_env.client, fake.build()) as tq:
        service = TemporalTicketService(workflow_env.client, task_queue=tq)
        assert await service.get_status("NOPE") is None
        wid, _ = await service.start_ticket(fx.tk1())
        await result(workflow_env.client.get_workflow_handle(wid))
        status, wf_status = await service.get_status("TK-1")

    assert status.step == "completed"
    assert status.ticket_id == "TK-1"
    assert wf_status == "COMPLETED"


async def _poll(client, path, pred, tries=100, delay=0.05):
    last = None
    for _ in range(tries):
        r = await client.get(path)
        last = r
        if r.status_code == 200 and pred(r.json()):
            return r.json()
        await asyncio.sleep(delay)
    raise AssertionError(f"condition never met for {path}; last={last.status_code if last else None} "
                         f"{last.text if last else ''}")


async def test_s11_end_to_end_through_http(workflow_env):
    """S-11"""
    import httpx

    from ticketflow.api.main import create_app
    from ticketflow.api.service import TemporalTicketService

    fake = FakeActivities(decide=decide_returning(fx.refund_decision()))
    async with worker(workflow_env.client, fake.build()) as tq:
        app = create_app(service=TemporalTicketService(workflow_env.client, task_queue=tq))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            body = {"ticket_id": "TK-2", "customer_id": "CUST-A", "subject": "Charged twice",
                    "body": "Please refund the extra payment"}
            r = await client.post("/tickets", json=body)
            assert r.status_code == 202
            assert r.json() == {"ticket_id": "TK-2", "workflow_id": "ticket-TK-2", "created": True}

            await _poll(client, "/tickets/TK-2", lambda b: b["step"] == "awaiting_approval")
            r = await client.post("/tickets/TK-2/approval", json={"approved": True})
            assert r.status_code == 202

            final = await _poll(client, "/tickets/TK-2", lambda b: b["workflow_status"] == "COMPLETED")
            assert final["step"] == "completed"
            assert final["result"]["action"]["action_type"] == "refund"
            assert final["result"]["outcome"] == "refunded"
            assert final["approval"]["approved"] is True

            r = await client.post("/tickets/TK-2/approval", json={"approved": True})
            assert r.status_code == 409
            assert (await client.get("/tickets/NOPE")).status_code == 404
            assert (await client.post("/tickets/NOPE/approval", json={"approved": True})).status_code == 404

    assert fake.act_types == ["refund"]


async def test_approval_after_workflow_closed_is_not_awaiting(workflow_env):
    """Race: the step check says awaiting_approval, but the workflow closes before the signal -> NotAwaiting (409)."""
    import pytest

    from ticketflow.api.service import NotAwaiting, TemporalTicketService
    from ticketflow.models import Approval, TicketStatus
    from ticketflow.workflow.ticket_workflow import AWAITING_APPROVAL

    fake = FakeActivities(
        decide=decide_returning(fx.reply_decision(article_ids=["KB-PW"]), customer_id="CUST-F")
    )
    async with worker(workflow_env.client, fake.build()) as tq:
        service = TemporalTicketService(workflow_env.client, task_queue=tq)
        wid, _ = await service.start_ticket(fx.tk1())
        await result(workflow_env.client.get_workflow_handle(wid))

        async def stale_status(ticket_id):
            return TicketStatus(ticket_id=ticket_id, workflow_id=wid, step=AWAITING_APPROVAL), "RUNNING"

        service.get_status = stale_status
        with pytest.raises(NotAwaiting):
            await service.send_approval("TK-1", Approval(approved=True))
