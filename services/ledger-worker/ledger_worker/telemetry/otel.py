from __future__ import annotations

import os
import sys

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_configured = False


def configure_otel(service_name: str) -> None:
    global _configured
    if _configured:
        return
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    disabled = os.getenv("DISABLE_OTEL_EXPORTER", "0") == "1" or "pytest" in sys.modules
    if not disabled:
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _configured = True
