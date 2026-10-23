# Code review: ticket-workflow

Reviewed: `ticketflow/`, `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `.github/workflows/tests.yml`,
`.env.example`, `README.md`. Compared against the assignment (`taskaiticketworkflow.md`, not in git) and the
archived spec (`sdlc/archive/2026-10-23-ticket-workflow/spec.md`, requirements R1-R26, N1-N15).

Scope: a read-only review. No code was changed. The locked tests were not re-run for this review. They passed
at gate time (140 passed). The seven seed example tickets were run through the fake agent and the rule check
directly, without Temporal.

## Verdict

- **Coherent with spec and assignment:** yes. Layering, the rule check, the approval flow, retry classification,
  the 24h timeout, idempotency keys and the logging contract match the spec.
- **Secure:** not yet. The approval endpoint and the status endpoint have no authentication or authorization.
- **Cloud-ready:** not yet. Helpdesk idempotency is in process memory, Temporal is reached without TLS or
  credentials, and there is no health endpoint.

The README discloses several of these gaps. The exceptions are noted below.

## Coherence: what holds

- `ticketflow/rules/decision_rules.py` imports only `ticketflow.models` (N8). Its refund checks cover customer
  ownership, paid status, positive amount and the invoice ceiling. All violations are listed.
- `ticketflow/workflow/ticket_workflow.py` is deterministic: no I/O, no wall clock, `workflow.logger` (N11).
  Only the first approval received while `awaiting_approval` counts (R10, N2). Timeout escalates. Rejection
  becomes a decline reply.
- Retry policies match R12. Non-retryable types match N5. An exhausted Helpdesk ends in `failed` with
  `HelpdeskUnavailable`.
- Idempotency key is `"{workflow_id}:{action_type}"` (R11, N3 within one workflow). `FakeHelpdesk` returns the
  stored reference for a repeated key.
- Seed example tickets: T-1 reply, T-2 refund (awaits approval), T-3 urgent escalate, T-4 suspended-account
  reply, T-5 close, T-6 escalated by the rule (5000 EUR > 49 EUR invoice), T-7 escalated as unknown customer.
- `ticketflow/activities/agent.py`: the ticket is wrapped in `<ticket>` delimiters and occurrences inside the
  text are neutralised (R15). Read tools are bound to the ticket's customer (N9).
- Secrets: no real key in the repo. The only key-like string is a test value in
  `tests/unit/test_llm_providers.py`. The OpenRouter key is `repr=False` in `config.py`. Provider error
  messages are not copied into the workflow history.

## Security findings

| # | Sev | Finding | Where | Disclosed in README? |
|---|---|---|---|---|
| S1 | High | No authentication or authorization on `POST /tickets/{id}/approval`. Anyone who can reach the API can approve or reject a refund. `reviewer` is a self-declared string. | `ticketflow/api/main.py` (approve_ticket) | Yes |
| S2 | High | `GET /tickets/{id}` is unauthenticated. It returns decisions, reply text and approvals for any ticket ID. IDs are guessable (`T-1`, `T-2`, ...). | `ticketflow/api/main.py` (get_ticket) | No |
| S3 | Med | No refund ledger across tickets. Two tickets can refund the same invoice, each within the invoice amount. Each refund still needs human approval. | `ticketflow/rules/decision_rules.py`, `ticketflow/workflow/ticket_workflow.py` | Yes |
| S4 | Med | Model-written reply text goes to the customer unchecked. A prompt-injected ticket could make the model write arbitrary text, for example a phishing link. The rules only require a non-empty message. Money movement is protected by the code rules and by approval. | `ticketflow/rules/decision_rules.py` (reply branch) | Partly |
| S5 | Med | Ticket text and invoice data are stored in Temporal history (personal data). No retention setting. | `ticketflow/workflow/ticket_workflow.py`, `DecideOutput` | No |
| S6 | Low | The container runs as root. Base and service images are unpinned: `python:3.12-slim`, `temporalio/temporal:latest`, `jaegertracing/jaeger:latest`. | `Dockerfile`, `docker-compose.yml` | No |
| S7 | Low | Approval race. The status query and the signal are not atomic. If the workflow times out between them, the API may return 500 instead of 409. Not tested. | `ticketflow/api/service.py` (send_approval) | Partly |

## Cloud-readiness findings

- **C1 (blocker for more than one worker):** `FakeHelpdesk` keeps idempotency keys in memory (`ticketflow/services/helpdesk.py`).
  A worker restart or a second replica can perform the same refund again, which breaks N3 across processes.
  The README discloses the restart case. It does not disclose the multi-replica case. The Azure section says a
  real Helpdesk must enforce keys server-side, which is the correct fix.
- **C2 (blocker for Temporal Cloud):** `ticketflow/temporal_client.py` connects with no TLS and no API key, and
  `config.py` has no settings for them. The README's Azure section describes Temporal Cloud mTLS stored in
  Key Vault. The code cannot use it. Either the README should be softened or TLS settings should be added.
- **C3:** no health endpoint on the API for container probes.
- **C4:** `docker-compose.yml` uses the single-binary Temporal dev server. The README discloses that it is not
  production.
- **C5:** OTLP export has no auth headers. Azure Monitor or other managed OTLP backends need them.

## Suggested priorities

1. Authentication on the approval endpoint, with the reviewer identity taken from the token. Authorization on
   `GET /tickets/{id}`.
2. Durable idempotency for the Helpdesk: the real Helpdesk must enforce keys, or the fake needs durable storage
   before more than one worker runs.
3. Make the README's Temporal Cloud claim match the code, or add TLS settings.
4. Non-root container user, pinned image tags, and a health endpoint.
5. A refund ledger per invoice, which the README already lists as a next step.
6. Handle the approval race: return 409 when the signal is rejected by a completed workflow, or use a Temporal Update with a validator.
