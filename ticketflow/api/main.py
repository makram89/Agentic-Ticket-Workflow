"""FastAPI app: `uvicorn ticketflow.api.main:app`."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from ticketflow.api.service import NotAwaiting, NotFound, TemporalTicketService, TicketService, workflow_id_for
from ticketflow.models import Approval, Ticket, TicketStatus

log = logging.getLogger("ticketflow.api")


class TicketAccepted(BaseModel):
    ticket_id: str
    workflow_id: str
    created: bool


class TicketStatusResponse(TicketStatus):
    workflow_status: str


class ApprovalAccepted(BaseModel):
    ticket_id: str
    workflow_id: str
    accepted: bool = True


def create_app(service: TicketService | None = None, *, tracer_provider=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.service is None:  # production: wire everything from the environment
            from ticketflow.config import load_settings
            from ticketflow.observability.logging import setup_logging
            from ticketflow.observability.tracing import setup_tracing
            from ticketflow.temporal_client import connect

            settings = load_settings()
            setup_logging(settings.log_level)
            setup_tracing(settings, service_name="api")
            client = await connect(settings)
            app.state.service = TemporalTicketService(
                client, task_queue=settings.task_queue, approval_timeout_seconds=settings.approval_timeout_seconds
            )
        yield

    app = FastAPI(title="ticketflow", lifespan=lifespan)
    app.state.service = service

    def svc(request: Request) -> TicketService:
        return request.app.state.service

    @app.post("/tickets", status_code=202, response_model=TicketAccepted)
    async def submit_ticket(ticket: Ticket, request: Request, response: Response) -> TicketAccepted:
        workflow_id, created = await svc(request).start_ticket(ticket)
        ids = {"ticket_id": ticket.ticket_id, "workflow_id": workflow_id}
        if created:
            log.info("ticket accepted, workflow started", extra=ids)
        else:
            log.info("duplicate ticket, returning the existing workflow", extra=ids)
            response.status_code = 200
        return TicketAccepted(ticket_id=ticket.ticket_id, workflow_id=workflow_id, created=created)

    @app.get("/tickets/{ticket_id}", response_model=TicketStatusResponse)
    async def get_ticket(ticket_id: str, request: Request) -> TicketStatusResponse:
        found = await svc(request).get_status(ticket_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"unknown ticket {ticket_id}")
        status, workflow_status = found
        return TicketStatusResponse(**status.model_dump(), workflow_status=workflow_status)

    @app.post("/tickets/{ticket_id}/approval", status_code=202, response_model=ApprovalAccepted)
    async def approve_ticket(ticket_id: str, approval: Approval, request: Request) -> ApprovalAccepted:
        ids = {"ticket_id": ticket_id, "workflow_id": workflow_id_for(ticket_id)}
        try:
            await svc(request).send_approval(ticket_id, approval)
        except NotFound:
            raise HTTPException(status_code=404, detail=f"unknown ticket {ticket_id}") from None
        except NotAwaiting:
            log.info("approval rejected: ticket is not awaiting approval", extra=ids)
            raise HTTPException(status_code=409, detail=f"ticket {ticket_id} is not awaiting approval") from None
        log.info("approval sent (approved=%s, reviewer=%s)", approval.approved, approval.reviewer, extra=ids)
        return ApprovalAccepted(ticket_id=ticket_id, workflow_id=ids["workflow_id"])

    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)
    return app


app = create_app()
