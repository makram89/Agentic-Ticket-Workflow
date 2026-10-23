# Intent: Agentic support-ticket workflow (FastAPI + Temporal)

## Problem
A (fictional) software company's support team receives many tickets a day: account problems,
billing questions, outages, spam. It needs a backend that processes each ticket through several
durable steps with an LLM agent, and keeps working when the LLM or the Helpdesk service fails for a
moment. Ticket text is untrusted customer input and must never be able to bypass business rules or the
human approval of refunds.

This change builds the whole service from scratch in a greenfield repo, as a recruitment deliverable.
The brief rewards a small, working, well-explained solution over a large one. The customers, invoices,
knowledge-base articles and example tickets given in the brief are **seed / demo data**, not the
system's behaviour; the system's behaviour is defined generically in `spec.md`.

## Goals
- G1: FastAPI service with `POST /tickets`, `GET /tickets/{ticket_id}`, `POST /tickets/{ticket_id}/approval`.
- G2: One Temporal workflow per ticket: Decide (LLM activity with tool calling) -> Check (pure rules in
  code) -> Wait for approval (refunds only, 24h timeout -> escalate) -> Act (Helpdesk activity).
- G3: Agent with tools `get_customer`, `get_invoices`, `search_kb` (plus `submit_decision` for the final
  answer). A deterministic fake LLM (`LLM_MODE=fake`, no key, no network) and a real provider via
  OpenRouter (`LLM_MODE=openrouter`) behind one small interface, using the same message/tool-call shape.
- G4: Resilience: fake Helpdesk with env-configured random temporary failures, bounded retry policies,
  non-retryable errors that end in a clear escalation or failure, idempotent Helpdesk actions.
- G5: Idempotent ticket submission (the same `ticket_id` is never processed twice).
- G6: JSON-line logs from API, workflow and activities, every ticket line carrying `ticket_id` and
  `workflow_id` (and `trace_id` when tracing is active).
- G7: `docker compose up` runs Temporal (+ web UI), API, worker and Jaeger in fake mode with no key;
  `.env.example` lists every setting.
- G8: Tests (Temporal time-skipping workflow tests with fake activities, rule unit tests, API tests,
  tracing test) run with `pytest -q`, no network (after the one-time test-server download) and no API key.
  Tests build their own small fixtures; only one smoke test uses the seed data.
- G9: README covering everything the brief asks for (run, examples, architecture + diagram,
  workflow-vs-activity rationale, retries/timeouts/duplicates, prompt-injection defence, Azure
  deployment, AI-assistant usage, assumptions/limitations/next steps).
- G10 (extra): GitHub Actions workflow running `pytest -q` on every push, fake mode, no secrets.
- G11 (extra): OpenTelemetry tracing API -> workflow -> activities, exported via OTLP when configured,
  viewable in Jaeger; no-op when not configured.

## Non-goals
- Remaining extras: Terraform/Bicep, the written real-vs-fake LLM comparison.
- A database or migration framework (seed JSON files loaded at startup instead); a vector store or
  embeddings for the knowledge base.
- A real Helpdesk; authentication/authorization of the API; ticket storage outside Temporal.
- Production-grade Temporal deployment (compose uses the single-binary dev server).
- A refund ledger across tickets (blocking a second refund of the same invoice from another ticket).
- Quality evaluation of the real LLM; using the real LLM in automated tests.

## Success criteria
- SC1: `docker compose up` with a copy of `.env.example` (fake mode) starts all services; the seed example
  tickets can be submitted with the README curl commands and reach the outcomes in design.md
  Appendix A; traces appear in Jaeger.
- SC2: `pytest -q` passes from the repo root without network access (given the cached Temporal
  test-server binary, see design "Constraints") and without any API key; it also passes in GitHub Actions.
- SC3: Every scenario in `scenarios.md` is implemented as at least one automated test, except those
  marked "manual".
- SC4: The rules module has no import of `fastapi`, `temporalio`, `openai`, `opentelemetry` or other
  framework/provider libraries.
- SC5: The assignment text is not in the repository; no real secrets are committed.
- SC6: A manual real-mode smoke run over the seed tickets stays well under the user's ~$2.5 OpenRouter budget.

## Open decisions / assumptions (please approve or change at the gate)
IDs are referenced from spec/design. Removed IDs are kept as "(removed)" so references stay stable.

- D1 The fake LLM is a deterministic keyword-rule stand-in (rules in design.md "Fake LLM rules"); it
  picks KB articles from `search_kb` results, never by hard-coded ids. Its outcomes on the seed tickets are
  a single smoke fixture (design.md Appendix A), not part of the core spec. On the seed prompt-injection
  ticket it deliberately "falls" for the injection and proposes an oversized refund, to show that the code
  check, not the model, is the safety boundary.
- D2 Approval rejection -> the workflow sends a templated **reply** to the customer that the refund for
  invoice X (amount Y) was reviewed and not approved, plus the reviewer comment if given; no KB article.
  Not an escalation: a human already looked at it. Outcome `refund_declined`.
- D3 Refund rules: invoice must exist and belong to the ticket's `customer_id` (from the ticket, never
  from the model); `0 < amount_eur <= invoice.amount_eur`; invoice status must be `paid` (an unpaid /
  overdue invoice has nothing to refund). Blocking an already-refunded invoice across tickets is NOT
  enforced (no refund ledger); within one ticket a refund is issued at most once (D4).
- D4 Helpdesk idempotency: idempotency key = `"{workflow_id}:{action_type}"` (one action per workflow).
  The fake Helpdesk is an in-process class inside the worker that remembers key -> result and returns the
  stored result for a repeated key without repeating the side effect. In-memory state is lost on worker
  restart (documented limitation; a real Helpdesk would persist keys server-side).
- D5 Agent loop bounds: max 8 model turns (`LLM_MAX_TURNS`), max 3 invalid final answers
  (`LLM_MAX_INVALID_ANSWERS`). The final decision arrives via a `submit_decision` tool call whose
  arguments are validated with pydantic for *shape*; invalid/absent decisions are fed back to the model.
  Exceeding either bound raises a non-retryable error -> the workflow escalates.
- D6 Prompt-injection defence: (a) ticket subject/body only in the user message inside
  `<ticket>...</ticket>`, labelled untrusted, delimiter sequences in the text neutralised; (b) tools are
  read-only and bound to the ticket's customer (no `customer_id` argument; supplied ids ignored); (c) all
  rules enforced in code in step 2 regardless of model output; (d) refunds always need a human approval
  signal; (e) the model never executes actions.
- D7 The whole tool loop runs inside ONE `decide_ticket` activity (not one activity per tool call):
  tools are read-only, local and fast, so re-running the loop on retry is safe and cheap; it keeps
  workflow history and code small. Trade-off: a retry repeats earlier LLM turns (token cost).
- D8 The rule check runs in workflow code as a pure function. `decide_ticket` returns, besides the
  proposed decision, a snapshot of the customer's invoices and the known KB article ids loaded by the
  data-access module (not by the model), so the workflow stays deterministic and the rules stay pure.
- D9 Shape vs. semantics split: the activity validates only the JSON shape; allowed values and refund
  rules are checked by the rules module (so unit tests can feed bad values).
- D10 Unknown customer is detected in `decide_ticket` *before* any LLM call ->
  `ApplicationError(type="UnknownCustomer", non_retryable=True)` -> the workflow escalates (does not fail)
  with a reason naming the customer id. Any other decide failure after retries also ends in escalation.
- D11 If `execute_action` exhausts its retries, the workflow **fails** with a clear reason
  (`HelpdeskUnavailable`); step `failed`. No fallback escalation (it would hit the same Helpdesk).
- D12 Approval API: unknown ticket -> 404; ticket not in step `awaiting_approval` (incl. finished) -> 409;
  accepted -> 202. Approval is a Temporal **signal**; the API pre-checks the step with a query. The workflow
  also ignores approvals that arrive when not awaiting, and only the first approval counts. (Temporal
  Update with a validator would be atomic, but adds moving parts; not chosen.)
- D13 `POST /tickets`: workflow id `ticket-{ticket_id}`, id-reuse policy `REJECT_DUPLICATE`, so a duplicate
  is rejected even after the first run completed; the API catches `WorkflowAlreadyStartedError` and
  returns 200 with the existing workflow id (`created: false`); new -> 202 (`created: true`). A duplicate with
  different content is NOT re-processed. Limitation: after namespace retention the id can be reused.
- D14 Approval timeout is 24h by default, passed as workflow input from env `APPROVAL_TIMEOUT_SECONDS`
  (default 86400) so a demo can shorten it. Tests use the default with time skipping.
- D15 Real LLM = **OpenRouter** through the OpenAI-compatible chat-completions API with tool calling, using
  the `openai` SDK (`base_url=https://openrouter.ai/api/v1`, `max_retries=0` so Temporal owns retries,
  `temperature=0`, `max_tokens=1024`). Env: `OPEN_ROUTER_API_KEY`, `OPENROUTER_MODEL` (default
  `openai/gpt-4o-mini`, a cheap tool-calling model; any OpenRouter model id with tool support works, verify
  on openrouter.ai/models). Auth/bad-request/unknown-model errors are non-retryable
  (`LLMRequestRejected` -> escalate) so a misconfiguration does not burn retries or budget. Real mode is only
  for a manual smoke check, never in tests. `LLM_MODE=openrouter` without key fails at worker start.
  The provider-neutral message format IS the OpenAI chat format, so the fake LLM emits exactly the same shape.
- D16 Stack: Python 3.12, `pyproject.toml` + pip (uv works, not required), package `ticketflow/` at repo
  root, pydantic v2 with Temporal's pydantic data converter, `pytest` + `pytest-asyncio`.
- D17 Docker Compose: `temporalio/temporal` CLI image (`server start-dev`, server + web UI in one
  container), one app image running either API or worker, plus Jaeger.
- D18 Seed data: the brief's customers, invoices, KB articles and example tickets live as
  `ticketflow/data/seed/*.json`, loaded once at process start by one small data-access module
  (`DataRepo`), which the agent tools use and which supplies the rule inputs. No database, no migrations;
  "swap the seed loader for a real DB" is a next step. Allowed categories/priorities/actions are system
  rules, so they are code constants, not seed data. Tests build their own small `DataRepo` fixtures and do
  not depend on seed contents, except the single seed smoke scenario (S-39).
- D19 Fake mode has no LLM failure injection; transient failures are demonstrated with the Helpdesk
  (`HELPDESK_FAILURE_RATE`, 0.2 in `.env.example`). Decide-activity retries are covered by tests.
- D20 Fake model fallback: a ticket matching none of its keyword rules is closed as off-topic. This is a
  property of the stand-in only, documented as a limitation.
- D21 Knowledge base search = simple keyword/token-overlap scoring over the seed articles behind
  `search_kb` (lowercase word tokens, small stopword list, score = number of shared distinct tokens with
  title+text, top 3 with score > 0). No vector store, no embeddings: four short articles do not justify them.
- D22 Tracing: OpenTelemetry SDK with `temporalio.contrib.opentelemetry.TracingInterceptor` on the Temporal
  client (API and worker) and worker, `opentelemetry-instrumentation-fastapi` on the app, OTLP/HTTP exporter
  configured by standard `OTEL_*` env vars. If `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, no SDK provider is
  installed (no-op tracer, nothing exported, no errors). `trace_id`/`span_id` are added to JSON log lines
  whenever a valid span is current; this is guaranteed for API and activity lines and best-effort for
  workflow lines (workflow code runs in the sandbox where the current span is not always set).
- D23 Trace viewer: a Jaeger v2 all-in-one container in compose (OTLP 4318, UI 16686). Chosen over a
  console exporter because one extra container gives a real span tree view at near-zero complexity; the
  Temporal UI shows workflow history but not the API -> workflow -> activity causality.
- D24 GitHub Actions: `.github/workflows/tests.yml` on `push` (and `pull_request`), Ubuntu, Python 3.12, pip
  cache, `pip install -e .[dev]`, `pytest -q`, `LLM_MODE=fake`, no secrets. The Temporal test-server binary
  is simply downloaded at test time (GHA has network); no extra cache step.
