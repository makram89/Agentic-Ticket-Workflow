"""Temporal worker process: `python -m ticketflow.worker`."""
import asyncio
import logging

from temporalio.worker import Worker

from ticketflow.activities.ticket_activities import TicketActivities
from ticketflow.config import load_settings
from ticketflow.data.repository import DataRepo
from ticketflow.observability.logging import setup_logging
from ticketflow.observability.tracing import setup_tracing
from ticketflow.services.helpdesk import FakeHelpdesk
from ticketflow.services.llm.factory import build_llm
from ticketflow.temporal_client import connect
from ticketflow.workflow.ticket_workflow import TicketWorkflow

log = logging.getLogger("ticketflow.worker")


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    setup_tracing(settings, service_name="worker")
    llm = build_llm(settings)  # ConfigError at start for a bad LLM_MODE or a missing key
    activities = TicketActivities(
        llm=llm,
        helpdesk=FakeHelpdesk(failure_rate=settings.helpdesk_failure_rate),
        repo=DataRepo.from_seed(),
        max_turns=settings.llm_max_turns,
        max_invalid=settings.llm_max_invalid_answers,
    )
    client = await connect(settings)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[TicketWorkflow],
        activities=[activities.decide_ticket, activities.execute_action],
    )
    log.info("worker started (task queue %s, LLM mode %s)", settings.task_queue, settings.llm_mode)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
