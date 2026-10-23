"""HTTP API against an in-memory service: S-01 .. S-09, S-58 (a)."""
import pytest

from tests import fixtures as fx
from tests.api.fake_service import FakeTicketService
from tests.log_helpers import assert_ticket_line, capture_json_logs

TK1_BODY = {"ticket_id": "TK-1", "customer_id": "CUST-F", "subject": "Can't log in", "body": "I forgot my password"}

STATUS_FIELDS = {
    "ticket_id",
    "workflow_id",
    "workflow_status",
    "step",
    "proposed_decision",
    "decision",
    "violations",
    "approval",
    "result",
    "error",
}


@pytest.fixture
def service():
    return FakeTicketService()


@pytest.fixture
def client(service):
    from fastapi.testclient import TestClient

    from ticketflow.api.main import create_app

    with TestClient(create_app(service=service)) as c:
        yield c


def test_s01_new_ticket_accepted(client, service):
    """S-01"""
    r = client.post("/tickets", json=TK1_BODY)
    assert r.status_code == 202
    assert r.json() == {"ticket_id": "TK-1", "workflow_id": "ticket-TK-1", "created": True}
    assert len(service.started) == 1
    assert service.started[0] == fx.tk1()


@pytest.mark.parametrize(
    "body",
    [
        {k: v for k, v in TK1_BODY.items() if k != "customer_id"},
        {**TK1_BODY, "ticket_id": "T 1/../x"},
        {**TK1_BODY, "body": ""},
        {**TK1_BODY, "subject": ""},
        {**TK1_BODY, "ticket_id": "x" * 65},
    ],
    ids=["missing-customer", "bad-ticket-id", "empty-body", "empty-subject", "too-long-id"],
)
def test_s02_invalid_ticket_rejected(client, service, body):
    """S-02"""
    r = client.post("/tickets", json=body)
    assert r.status_code == 422
    assert service.started == []


def test_s03_duplicate_post_returns_existing(client, service):
    """S-03"""
    assert client.post("/tickets", json=TK1_BODY).status_code == 202
    r = client.post("/tickets", json={**TK1_BODY, "body": "something else entirely"})
    assert r.status_code == 200
    assert r.json() == {"ticket_id": "TK-1", "workflow_id": "ticket-TK-1", "created": False}


def test_s04_status_of_known_ticket(client, service):
    """S-04"""
    service.put_status("TK-2", "awaiting_approval", decision=fx.refund_decision())
    r = client.get("/tickets/TK-2")
    assert r.status_code == 200
    body = r.json()
    assert STATUS_FIELDS <= set(body)
    assert body["ticket_id"] == "TK-2"
    assert body["workflow_id"] == "ticket-TK-2"
    assert body["workflow_status"] == "RUNNING"
    assert body["step"] == "awaiting_approval"
    assert body["decision"]["action"]["type"] == "refund"
    assert body["decision"]["action"]["invoice_id"] == "INV-A2"
    assert body["proposed_decision"]["action"]["type"] == "refund"
    assert body["violations"] == []
    assert body["result"] is None


def test_s05_status_of_unknown_ticket(client):
    """S-05"""
    assert client.get("/tickets/NOPE").status_code == 404


def test_s06_approval_of_waiting_refund(client, service):
    """S-06"""
    service.put_status("TK-2", "awaiting_approval", decision=fx.refund_decision())
    r = client.post("/tickets/TK-2/approval", json={"approved": True, "reviewer": "ana"})
    assert r.status_code == 202
    assert len(service.approvals) == 1
    tid, approval = service.approvals[0]
    assert tid == "TK-2"
    assert approval.approved is True
    assert approval.reviewer == "ana"


def test_s07_approval_for_unknown_ticket(client, service):
    """S-07"""
    r = client.post("/tickets/NOPE/approval", json={"approved": True})
    assert r.status_code == 404
    assert service.approvals == []


@pytest.mark.parametrize("ticket_id,step", [("TK-1", "completed"), ("TK-3", "deciding")])
def test_s08_approval_when_not_waiting(client, service, ticket_id, step):
    """S-08"""
    service.put_status(ticket_id, step, workflow_status="COMPLETED" if step == "completed" else "RUNNING")
    r = client.post(f"/tickets/{ticket_id}/approval", json={"approved": True})
    assert r.status_code == 409
    assert service.approvals == []


@pytest.mark.parametrize("body", [{"approved": "maybe"}, {}])
def test_s09_invalid_approval_body(client, service, body):
    """S-09"""
    service.put_status("TK-2", "awaiting_approval", decision=fx.refund_decision())
    r = client.post("/tickets/TK-2/approval", json=body)
    assert r.status_code == 422
    assert service.approvals == []


def test_s58_api_log_lines_carry_ids(service):
    """S-58 (a)"""
    from fastapi.testclient import TestClient

    from ticketflow.api.main import create_app

    with TestClient(create_app(service=service)) as c:
        with capture_json_logs() as cap:
            assert c.post("/tickets", json=TK1_BODY).status_code == 202
    lines = cap.ticket_records("TK-1")
    assert lines, f"API emitted no log line about TK-1: {cap.lines()}"
    for rec in lines:
        assert_ticket_line(rec, "TK-1", "ticket-TK-1")
