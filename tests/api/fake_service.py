"""In-memory TicketService double injected via create_app(service=...)."""


class FakeTicketService:
    def __init__(self):
        self.started = []  # tickets passed to start_ticket
        self.statuses = {}  # ticket_id -> (TicketStatus, workflow_status)
        self.approvals = []  # (ticket_id, Approval) actually signalled

    def put_status(self, ticket_id, step, decision=None, workflow_status="RUNNING", **extra):
        from ticketflow.models import TicketStatus

        fields = dict(
            ticket_id=ticket_id,
            workflow_id=f"ticket-{ticket_id}",
            step=step,
            proposed_decision=decision,
            decision=decision,
            violations=[],
            approval=None,
            result=None,
            error=None,
        )
        fields.update(extra)
        self.statuses[ticket_id] = (TicketStatus(**fields), workflow_status)

    async def start_ticket(self, ticket):
        self.started.append(ticket)
        wid = f"ticket-{ticket.ticket_id}"
        if ticket.ticket_id in self.statuses:
            return wid, False
        self.put_status(ticket.ticket_id, "received")
        return wid, True

    async def get_status(self, ticket_id):
        return self.statuses.get(ticket_id)

    async def send_approval(self, ticket_id, approval):
        from ticketflow.api.service import NotAwaiting, NotFound

        if ticket_id not in self.statuses:
            raise NotFound(ticket_id)
        status, _ = self.statuses[ticket_id]
        if status.step != "awaiting_approval":
            raise NotAwaiting(ticket_id)
        self.approvals.append((ticket_id, approval))
