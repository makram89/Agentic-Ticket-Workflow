# Spec: Agentic support-ticket workflow (FastAPI + Temporal)

Terms: *ticket* = `{ticket_id, customer_id, subject, body}`. *Proposed decision* = what the model
returned. *Final decision* = decision after the rule check (step 2). *Workflow id* = `ticket-{ticket_id}`.
*Data-access module* = `DataRepo`, the only component that reads customers, invoices and KB articles.
Decision ids D1..D24 refer to `intent.md` "Open decisions / assumptions". Requirements are generic; no
requirement depends on specific seed records.

## Requirements

### API
- R1: `POST /tickets` MUST accept JSON `{ticket_id, customer_id, subject, body}` (all non-empty strings;
  `ticket_id` matches `^[A-Za-z0-9_-]{1,64}$`), start a `TicketWorkflow` with workflow id
  `ticket-{ticket_id}` and return **202** `{ticket_id, workflow_id, created: true}` without waiting for
  the workflow to progress. Invalid bodies MUST return 422.
- R2: A `POST /tickets` with a `ticket_id` that already has a workflow (running or closed) MUST NOT start
  a new run and MUST return **200** `{ticket_id, workflow_id, created: false}` for the existing workflow
  (D13). Request content of the duplicate is ignored.
- R3: `GET /tickets/{ticket_id}` MUST return 200 with `{ticket_id, workflow_id, workflow_status, step,
  proposed_decision, decision, violations, approval, result, error}` read from the workflow (query +
  describe). Unknown ticket MUST return 404.
- R4: `POST /tickets/{ticket_id}/approval` with `{approved: bool, reviewer?: str, comment?: str}` MUST
  send the approval signal and return **202** when the workflow is in step `awaiting_approval`; MUST
  return 404 for an unknown ticket, 409 when not awaiting approval (incl. finished), 422 for an invalid
  body (D12).

### Workflow
- R5: `TicketWorkflow` MUST run the steps in order and expose the current step via the `status` query.
  Steps: `received` -> `deciding` -> `checking` -> [`awaiting_approval`] -> `acting` -> `completed`
  (or `failed`). The query MUST also return the proposed decision, final decision, violations, approval
  and result as soon as each is known.
- R6: Step 1 (`decide_ticket` activity) MUST run an agent loop in which the LLM may call the tools
  `get_customer` (plan, status), `get_invoices` (the customer's invoices), `search_kb(query)` (matching KB
  articles) and MUST finish with `submit_decision({category, priority, action})`. Code executes tool calls
  via the data-access module and returns results to the model until a valid `submit_decision` arrives (D7).
- R7: The agent loop MUST be bounded: at most `LLM_MAX_TURNS` (default 8) model calls and at most
  `LLM_MAX_INVALID_ANSWERS` (default 3) invalid final answers (text without `submit_decision`, or a
  `submit_decision` whose arguments are not valid JSON or fail shape validation). Invalid answers and
  unknown tool names MUST be reported back to the model as tool/user error messages. Exceeding a bound MUST
  raise a non-retryable `ApplicationError` of type `AgentLoopExceeded` resp. `InvalidModelOutput` (D5).
- R8: If the ticket's `customer_id` is unknown to the data-access module, `decide_ticket` MUST raise a
  non-retryable `ApplicationError(type="UnknownCustomer")` before calling the LLM, and the workflow MUST
  escalate the ticket with a reason containing that customer id (category `other`, priority `normal`)
  (D10). Any other `decide_ticket` failure after retries MUST also produce such an escalation whose reason
  contains the error type.
- R9: Step 2 MUST check the proposed decision in code with a pure function (D3, D8, D9):
  - category in {account, billing, technical, other}; priority in {low, normal, high, urgent};
    action type in {reply, refund, escalate, close} (code constants);
  - reply: non-empty `message`; every id in `article_ids` is a known KB article (empty list allowed);
  - refund: `invoice_id` present and known; the invoice belongs to the **ticket's** customer; invoice
    status `paid`; `0 < amount_eur <= invoice.amount_eur`;
  - escalate / close: non-empty `reason`.
  A decision with one or more violations MUST become an escalation with reason
  `"rule violation: " + "; ".join(violations)`, keeping the proposed category/priority when valid and
  falling back to `other` / `normal` otherwise. All violations MUST be listed.
- R10: Step 3 MUST run only for refunds that passed step 2. The workflow MUST wait for the `approval`
  signal up to `approval_timeout_seconds` (default 86400 = 24h, D14):
  - approved -> act with the refund;
  - rejected -> act with a reply declining the refund (template naming invoice id and amount with two
    decimals, e.g. `50.00 EUR`, reviewer comment appended if any, `article_ids=[]`) (D2);
  - timeout -> act with an escalation, reason `"refund approval timed out after 24h"` (hours derived
    from the configured timeout).
  Approvals received while not in `awaiting_approval` MUST be ignored (logged); only the first approval
  while awaiting counts.
- R11: Step 4 (`execute_action` activity) MUST perform the final action through the Helpdesk interface
  (`send_reply`, `issue_refund`, `escalate`, `close`) passing idempotency key
  `"{workflow_id}:{action_type}"`. The fake Helpdesk MUST raise a temporary error with probability
  `HELPDESK_FAILURE_RATE` (float in [0,1], default 0.0 in code) and MUST return the stored result for a
  repeated idempotency key without repeating the side effect (D4).
- R12: Retry policies (bounded):
  - `decide_ticket`: start-to-close 120s, initial interval 1s, backoff 2.0, max interval 30s, max attempts 5,
    non-retryable types `UnknownCustomer`, `InvalidModelOutput`, `AgentLoopExceeded`, `LLMRequestRejected`;
  - `execute_action`: start-to-close 30s, initial interval 1s, backoff 2.0, max interval 30s, max attempts 10.
  If `execute_action` exhausts retries, the workflow MUST end in step `failed` with `error` set and fail
  with `ApplicationError(type="HelpdeskUnavailable")` (D11).

### Agent / LLM
- R13: `LLM_MODE=fake` (default) MUST use a deterministic, keyword-rule fake provider (design "Fake LLM
  rules") that returns tool calls and the final `submit_decision` call in exactly the same `LLMReply` /
  OpenAI-chat message shape as the OpenRouter adapter, chooses KB articles only from `search_kb` results,
  and needs no key and no network (D1).
- R14: `LLM_MODE=openrouter` MUST use the `openai` SDK against `https://openrouter.ai/api/v1` with
  `OPEN_ROUTER_API_KEY`, model `OPENROUTER_MODEL` (default `openai/gpt-4o-mini`), SDK retries disabled,
  `temperature=0`, `max_tokens=1024`, behind the same `LLMProvider` interface. Connection errors, timeouts,
  429 and 5xx MUST propagate as retryable; 400/401/403/404 MUST raise non-retryable
  `ApplicationError(type="LLMRequestRejected")`. Missing `OPEN_ROUTER_API_KEY` in this mode, or an unknown
  `LLM_MODE`, MUST fail at worker start with a clear `ConfigError` (D15).
- R15: The prompt MUST put ticket subject/body only in the user message inside `<ticket>` ... `</ticket>`
  delimiters marked as untrusted data; occurrences of `<ticket>`/`</ticket>` inside ticket text MUST be
  neutralised. The system prompt MUST contain the allowed values and state that rules are enforced by code (D6).
- R16: Tools MUST be read-only and bound to the ticket's `customer_id`; `get_customer`/`get_invoices`
  take no customer argument and ignore any supplied one. `search_kb` MUST use token-overlap scoring and
  return up to 3 articles with score > 0, each `{article_id, title, text}` (D21).

### Data
- R20: Seed data (customers, invoices, KB articles, example tickets from the brief, values unchanged) MUST
  live in `ticketflow/data/seed/*.json` and be loaded once at process start by `DataRepo`, validated
  against the pydantic models; a malformed seed file MUST fail start-up with an error naming the file.
  `DataRepo` MUST also be constructible from in-memory lists (for tests). No database (D18).

### Cross-cutting
- R17: API, workflow and activities MUST log JSON lines with at least `timestamp`, `level`, `logger`,
  `message`; every line about a ticket MUST include top-level `ticket_id` and `workflow_id`. Workflow code
  MUST use the replay-safe `workflow.logger`.
- R18: All settings MUST come from environment variables: `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`,
  `TEMPORAL_TASK_QUEUE`, `LLM_MODE`, `OPEN_ROUTER_API_KEY`, `OPENROUTER_MODEL`, `LLM_MAX_TURNS`,
  `LLM_MAX_INVALID_ANSWERS`, `HELPDESK_FAILURE_RATE`, `APPROVAL_TIMEOUT_SECONDS`, `LOG_LEVEL`,
  `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`. Invalid values (e.g. failure rate outside [0,1]) MUST
  raise `ConfigError`. `.env.example` MUST list all names with safe defaults (`LLM_MODE=fake`, empty
  `OPEN_ROUTER_API_KEY`).
- R19: `docker-compose.yml` MUST define services `temporal` (server + web UI), `api`, `worker` and
  `jaeger`; `docker compose up` MUST work in fake mode with no API key, with api/worker exporting traces to
  Jaeger by default.
- R21: Code MUST be separated into: HTTP API (`ticketflow/api`), workflow (`ticketflow/workflow`),
  activities (`ticketflow/activities`), business rules (`ticketflow/rules`), outside services
  (`ticketflow/services/llm`, `ticketflow/services/helpdesk`), data access (`ticketflow/data`),
  observability (`ticketflow/observability`).
- R22: `pytest -q` from the repo root MUST run all tests: Temporal `WorkflowEnvironment` time-skipping
  workflow tests with fake activities, rule unit tests, API tests, activity tests, tracing test.
- R23: `README.md` MUST cover the sections required by the brief (G9 in intent.md), including an
  architecture diagram, example curl requests for all three endpoints using seed example tickets, Azure
  deployment notes, and how to view traces.
- R24: `.github/workflows/tests.yml` MUST run `pytest -q` on every push (Python 3.12, fake mode) without
  referencing any secret (D24).
- R25: When `OTEL_EXPORTER_OTLP_ENDPOINT` is set, the API and worker MUST export OpenTelemetry spans over
  OTLP/HTTP such that one ticket yields one trace containing: the FastAPI server span of `POST /tickets`,
  Temporal `StartWorkflow:TicketWorkflow`, `RunWorkflow:TicketWorkflow`, and `StartActivity`/`RunActivity`
  spans for `decide_ticket` and `execute_action` (via `TracingInterceptor` on client and worker). When it is
  unset, tracing MUST be a no-op and start-up MUST NOT fail (D22).
- R26: When a valid span is current, JSON log lines MUST include `trace_id` (32 hex chars) and `span_id`;
  this MUST hold for API and activity log lines; workflow log lines include them when available (D22).

## Negative requirements
- N1: Ticket text MUST NOT be able to cause an action that violates R9, skip step 2, or skip approval for a refund.
- N2: The system MUST NOT issue a refund without an explicit approving `approval` signal received while in
  `awaiting_approval`.
- N3: The same action MUST NOT be performed twice for one workflow, including across activity retries.
- N4: The same `ticket_id` MUST NOT be processed by more than one workflow run.
- N5: Non-retryable errors (`UnknownCustomer`, `InvalidModelOutput`, `AgentLoopExceeded`,
  `LLMRequestRejected`) MUST NOT be retried.
- N6: `POST /tickets` MUST NOT wait for the decision or the result.
- N7: The repo MUST NOT contain the assignment text or any real key/secret; code MUST NOT log the API key.
- N8: `ticketflow/rules` MUST NOT import `fastapi`, `temporalio`, `openai`, `opentelemetry`, HTTP
  libraries, or other `ticketflow` layers except `ticketflow.models`.
- N9: LLM tools MUST NOT have side effects and MUST NOT expose data of a customer other than the ticket's.
- N10: Tests MUST NOT need network access (beyond the one-time Temporal test-server download), an API key,
  Docker, or a running OTLP collector.
- N11: Workflow code MUST NOT perform I/O, randomness or wall-clock reads (only `workflow.*` APIs).
- N12: The LLM MUST NOT perform Helpdesk actions; only `execute_action` does, with the final decision.
- N13: Tests MUST NOT depend on seed file contents, except the single seed smoke scenario S-39.
- N14: The system MUST NOT require a database, migration framework, vector store or embedding model.
- N15: Automated tests (including CI) MUST NOT call the real LLM provider.

## Open questions
None blocking. All ambiguities were decided and are listed as D1..D24 in `intent.md` for approval.
