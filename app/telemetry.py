from contextvars import ContextVar
from uuid import uuid4

from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider

from app.config import Settings

SERVICE = "azure-ai-saas-platform"
TENANT: ContextVar[str] = ContextVar("tenant_id", default="")


class TenantSpanProcessor(SpanProcessor):
    """Every span of a request carries the tenant it served, so traces, metrics and logs split by customer."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        if TENANT.get():
            span.set_attribute("tenant_id", TENANT.get())


def configure(settings: Settings) -> None:
    if not settings.applicationinsights_connection_string:
        return
    resource = Resource.create({"service.name": SERVICE})
    connection = settings.applicationinsights_connection_string
    configure_azure_monitor(connection_string=connection, credential=DefaultAzureCredential(), resource=resource)
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(TenantSpanProcessor())


def current_trace_id() -> str:
    trace_id = trace.get_current_span().get_span_context().trace_id
    return trace.format_trace_id(trace_id) if trace_id else uuid4().hex
