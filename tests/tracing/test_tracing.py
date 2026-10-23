"""OpenTelemetry tracing: S-63 (span tree), S-64 (trace ids in logs), S-65 (no-op without endpoint).

Tracer providers are passed explicitly; the global provider is never set in-process
(S-65 runs setup_tracing in subprocesses).
"""
import json
import os
import re
import subprocess
import sys
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path

from tests import fixtures as fx
from tests.log_helpers import capture_json_logs
from tests.workflow_helpers import FakeActivities, decide_returning, result, worker

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_SPANS = [
    "StartWorkflow:TicketWorkflow",
    "RunWorkflow:TicketWorkflow",
    "StartActivity:decide_ticket",
    "RunActivity:decide_ticket",
    "StartActivity:execute_action",
    "RunActivity:execute_action",
]

TK1_BODY = {"ticket_id": "TK-1", "customer_id": "CUST-F", "subject": "Can't log in", "body": "I forgot my password"}


def make_provider():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


@asynccontextmanager
async def traced_stack(env, activities, provider):
    """Client + worker with TracingInterceptor, app with FastAPI instrumentation, all on `provider`."""
    import httpx
    from temporalio.client import Client
    from temporalio.contrib.opentelemetry import TracingInterceptor

    from ticketflow.api.main import create_app
    from ticketflow.api.service import TemporalTicketService

    interceptor = TracingInterceptor(tracer=provider.get_tracer("test"))
    client = Client(**{**env.client.config(), "interceptors": [interceptor]})
    async with worker(client, activities) as tq:
        app = create_app(service=TemporalTicketService(client, task_queue=tq), tracer_provider=provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield client, http


def http_server_span(spans):
    from opentelemetry.trace import SpanKind

    cands = [s for s in spans if s.kind == SpanKind.SERVER and s.name.startswith("POST") and "/tickets" in s.name]
    assert cands, f"no HTTP server span for POST /tickets in {[s.name for s in spans]}"
    return cands[0]


def by_name(spans, name):
    found = [s for s in spans if s.name == name]
    assert found, f"missing span {name!r}; have {[s.name for s in spans]}"
    return found[0]


async def test_s63_one_span_tree_per_ticket(workflow_env):
    """S-63"""
    provider, exporter = make_provider()
    fake = FakeActivities(decide=decide_returning(fx.reply_decision(article_ids=["KB-PW"]), customer_id="CUST-F"))
    async with traced_stack(workflow_env, fake.build(), provider) as (client, http):
        r = await http.post("/tickets", json=TK1_BODY)
        assert r.status_code == 202
        await result(client.get_workflow_handle("ticket-TK-1"))
    provider.force_flush()
    spans = exporter.get_finished_spans()

    root = http_server_span(spans)
    trace_id = root.context.trace_id
    for name in EXPECTED_SPANS:
        assert by_name(spans, name).context.trace_id == trace_id, name

    # StartWorkflow has the HTTP server span as ancestor
    parents = {s.context.span_id: (s.parent.span_id if s.parent else None) for s in spans}
    node = by_name(spans, "StartWorkflow:TicketWorkflow").context.span_id
    ancestors = set()
    while node is not None and node in parents:
        node = parents[node]
        if node is not None:
            ancestors.add(node)
    assert root.context.span_id in ancestors


async def test_s64_logs_carry_trace_id(workflow_env, repo):
    """S-64: API and activity log lines for TK-1 carry the ticket's trace_id and a span_id."""
    import logging

    from ticketflow.activities.ticket_activities import TicketActivities
    from ticketflow.services.helpdesk import FakeHelpdesk
    from ticketflow.services.llm.fake import FakeLLM

    provider, exporter = make_provider()
    acts = TicketActivities(llm=FakeLLM(), helpdesk=FakeHelpdesk(failure_rate=0.0), repo=repo)
    async with traced_stack(workflow_env, [acts.decide_ticket, acts.execute_action], provider) as (client, http):
        with capture_json_logs() as cap:
            r = await http.post("/tickets", json=TK1_BODY)
            assert r.status_code == 202
            api_mark = cap.mark()
            await result(client.get_workflow_handle("ticket-TK-1"))
            logging.getLogger("ticketflow.tests").info(
                "outside any span", extra={"ticket_id": "TK-1", "workflow_id": "ticket-TK-1"}
            )
    provider.force_flush()
    trace_hex = format(http_server_span(exporter.get_finished_spans()).context.trace_id, "032x")

    records = [json.loads(line) for line in cap.lines()]
    api_lines = [
        r for r in records[:api_mark]
        if "TK-1" in json.dumps(r) and "activity_type" not in r
        and not str(r.get("logger", "")).startswith(("temporalio", "httpx", "httpcore"))
    ]
    activity_lines = [r for r in records if r.get("activity_type") and "TK-1" in json.dumps(r)]
    assert api_lines, f"no API log line for TK-1: {records[:api_mark]}"
    assert activity_lines, f"no activity log line for TK-1: {records}"
    for rec in api_lines + activity_lines:
        assert re.fullmatch(r"[0-9a-f]{32}", str(rec.get("trace_id"))), rec
        assert rec["trace_id"] == trace_hex, rec
        assert re.fullmatch(r"[0-9a-f]{16}", str(rec.get("span_id"))), rec

    outside = [r for r in records if r.get("message") == "outside any span"]
    assert len(outside) == 1
    assert outside[0].get("trace_id") is None


def _run_python(code, extra_env):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env.update(extra_env)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


_PRELUDE = """
import json, socket
def _blocked(*a, **k):
    raise RuntimeError("network access attempted")
socket.create_connection = _blocked
socket.socket.connect = _blocked

from opentelemetry.exporter.otlp.proto.http import trace_exporter as _te
created = []
_orig_init = _te.OTLPSpanExporter.__init__
def _init(self, *a, **k):
    created.append(True)
    _orig_init(self, *a, **k)
_te.OTLPSpanExporter.__init__ = _init
_te.OTLPSpanExporter.export = lambda self, spans: __import__(
    "opentelemetry.sdk.trace.export", fromlist=["SpanExportResult"]).SpanExportResult.SUCCESS

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider as SdkProvider
from ticketflow.config import load_settings
from ticketflow.observability.tracing import setup_tracing
"""


def test_s65_tracing_disabled_without_endpoint():
    """S-65 (unset): returns False, installs no SDK provider, spans are no-ops, no exporter."""
    out = _run_python(
        _PRELUDE
        + """
import os
assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
settings = load_settings()
ok = setup_tracing(settings, service_name="api")
with trace.get_tracer("t").start_as_current_span("s") as span:
    valid = span.get_span_context().is_valid
print(json.dumps({"ok": ok, "sdk": isinstance(trace.get_tracer_provider(), SdkProvider),
                  "valid": valid, "exporters": len(created)}))
""",
        {},
    )
    assert out == {"ok": False, "sdk": False, "valid": False, "exporters": 0}


def test_s65_tracing_enabled_with_endpoint():
    """S-65 (set): returns True and installs an SDK provider with an OTLP/HTTP exporter (nothing exported)."""
    out = _run_python(
        _PRELUDE
        + """
settings = load_settings()
ok = setup_tracing(settings, service_name="api")
provider = trace.get_tracer_provider()
with trace.get_tracer("t").start_as_current_span("s") as span:
    valid = span.get_span_context().is_valid
print(json.dumps({"ok": ok, "sdk": isinstance(provider, SdkProvider), "valid": valid,
                  "exporters": len(created),
                  "service": provider.resource.attributes.get("service.name") if hasattr(provider, "resource") else None}))
""",
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"},
    )
    assert out == {"ok": True, "sdk": True, "valid": True, "exporters": 1, "service": "api"}


def test_s65_tracing_interceptor_factory():
    """S-65 / R25: tracing_interceptor() returns Temporal's TracingInterceptor (no-op without SDK)."""
    from temporalio.contrib.opentelemetry import TracingInterceptor

    from ticketflow.observability.tracing import tracing_interceptor

    assert isinstance(tracing_interceptor(), TracingInterceptor)
