"""JSON log lines (R17): formatter unit checks and S-58 (b) activity log lines."""
import dataclasses
import json
import logging

from tests import fixtures as fx
from tests.log_helpers import assert_ticket_line, capture_json_logs


def _format(record_extra=None, msg="hello"):
    from ticketflow.observability.logging import JsonFormatter

    logger = logging.getLogger("ticketflow.test")
    record = logger.makeRecord("ticketflow.test", logging.INFO, __file__, 1, msg, (), None, extra=record_extra)
    return json.loads(JsonFormatter().format(record))


def test_s58_formatter_base_fields_and_extras():
    """S-58: timestamp, level, logger, message + extras at top level."""
    rec = _format({"ticket_id": "TK-1", "workflow_id": "ticket-TK-1"})
    assert_ticket_line(rec, "TK-1", "ticket-TK-1")
    assert rec["level"] == "INFO"
    assert rec["logger"] == "ticketflow.test"
    assert rec["message"] == "hello"
    assert rec.get("trace_id") is None  # S-64: no current span -> no trace id


def test_s58_formatter_flattens_temporal_extras():
    """S-58: temporal_workflow / temporal_activity extras are flattened into workflow_id (+ run_id, activity_type)."""
    wf = _format({"ticket_id": "TK-1", "temporal_workflow": {"workflow_id": "ticket-TK-1", "run_id": "r1",
                                                              "workflow_type": "TicketWorkflow"}})
    assert wf["workflow_id"] == "ticket-TK-1"
    assert wf["run_id"] == "r1"
    act = _format({"ticket_id": "TK-1", "temporal_activity": {"workflow_id": "ticket-TK-1", "workflow_run_id": "r1",
                                                               "activity_type": "decide_ticket",
                                                               "activity_id": "1", "attempt": 1}})
    assert act["workflow_id"] == "ticket-TK-1"
    assert act["activity_type"] == "decide_ticket"


def _activity_env(workflow_id):
    from temporalio.testing import ActivityEnvironment

    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, workflow_id=workflow_id)
    return env


async def test_s58_activity_log_lines_carry_ids(repo):
    """S-58 (b): real decide_ticket / execute_action for TK-1 log lines with ticket_id and workflow_id."""
    from ticketflow.activities.ticket_activities import TicketActivities
    from ticketflow.models import DecideInput, ExecuteActionInput
    from ticketflow.services.helpdesk import FakeHelpdesk
    from ticketflow.services.llm.fake import FakeLLM

    acts = TicketActivities(llm=FakeLLM(), helpdesk=FakeHelpdesk(failure_rate=0.0), repo=repo)
    env = _activity_env("ticket-TK-1")

    with capture_json_logs() as decide_cap:
        out = await env.run(acts.decide_ticket, DecideInput(ticket=fx.tk1(), workflow_id="ticket-TK-1"))
    with capture_json_logs() as act_cap:
        await env.run(
            acts.execute_action,
            ExecuteActionInput(ticket_id="TK-1", workflow_id="ticket-TK-1",
                               idempotency_key="ticket-TK-1:reply", decision=out.proposed),
        )

    for cap in (decide_cap, act_cap):
        lines = cap.ticket_records("TK-1")
        assert lines, f"activity emitted no log line about TK-1: {cap.lines()}"
        for rec in lines:
            assert_ticket_line(rec, "TK-1", "ticket-TK-1")


def test_s58_setup_logging_emits_json(capsys):
    """S-58: setup_logging installs JSON lines on stdout/stderr."""
    from ticketflow.observability.logging import setup_logging

    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        setup_logging("INFO")
        logging.getLogger("ticketflow.test").info("ping", extra={"ticket_id": "TK-1", "workflow_id": "ticket-TK-1"})
        for h in root.handlers:
            h.flush()
        captured = capsys.readouterr()
        lines = [ln for ln in (captured.out + captured.err).splitlines() if "ping" in ln]
        assert lines
        assert_ticket_line(json.loads(lines[-1]), "TK-1", "ticket-TK-1")
    finally:
        for h in list(root.handlers):
            if h not in saved_handlers:
                root.removeHandler(h)
        for h in saved_handlers:
            if h not in root.handlers:
                root.addHandler(h)
        root.setLevel(saved_level)
