"""OpenTelemetry setup (R25, D22): OTLP/HTTP export when configured, no-op otherwise."""
from temporalio.contrib.opentelemetry import TracingInterceptor

from ticketflow.config import Settings


def setup_tracing(settings: Settings, service_name: str) -> bool:
    """Install a global SDK tracer provider if OTEL_EXPORTER_OTLP_ENDPOINT is set; return whether it did."""
    if not settings.otel_endpoint:
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name or service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # reads the standard OTEL_* env vars
    trace.set_tracer_provider(provider)
    return True


def tracing_interceptor() -> TracingInterceptor:
    """Temporal client + worker interceptor using the global tracer (a no-op without setup_tracing)."""
    return TracingInterceptor()
