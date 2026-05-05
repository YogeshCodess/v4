"""
OpenTelemetry instrumentation — emits traces for FastAPI routes, SQLAlchemy
queries, and LLM calls. No-op when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset,
so local dev / air-gapped demos keep working unchanged.

Wiring:
  main.py::lifespan             → configure_otel(app)  (after get_engine())
  agents/base_agent.py::call_llm → uses tracer() from this module

Viewer: point OTEL_EXPORTER_OTLP_ENDPOINT at Tempo / Jaeger / Honeycomb /
SigNoz / Datadog's OTLP receiver. Example:
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
  export OTEL_SERVICE_NAME=hardware-pipeline
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

log = logging.getLogger(__name__)


_CONFIGURED = False
_NOOP_TRACER: Any = None  # lazy


def is_enabled() -> bool:
    """OTel is active only when an OTLP endpoint is configured."""
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))


def configure_otel(app=None, engine=None) -> bool:
    """Configure tracer provider + FastAPI/SQLAlchemy auto-instrumentation.

    Returns True when OTel was wired up, False when skipped (no endpoint set,
    or otel SDK not importable). Safe to call multiple times.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return True
    if not is_enabled():
        log.debug("otel.skipped — OTEL_EXPORTER_OTLP_ENDPOINT not set")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError as exc:
        log.warning("otel.import_failed: %s — tracing disabled", exc)
        return False

    service_name = os.getenv("OTEL_SERVICE_NAME", "hardware-pipeline")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # Auto-instrument FastAPI (captures every route as a span)
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app)
        except Exception as exc:
            log.warning("otel.fastapi_instrument_failed: %s", exc)

    # Auto-instrument SQLAlchemy (captures every SQL query)
    if engine is not None:
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
            SQLAlchemyInstrumentor().instrument(engine=engine)
        except Exception as exc:
            log.warning("otel.sqlalchemy_instrument_failed: %s", exc)

    _CONFIGURED = True
    log.info("otel.configured service=%s endpoint=%s",
             service_name, os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
    return True


def tracer(name: str = "hardware-pipeline"):
    """Return a tracer — real one when OTel is configured, no-op otherwise.

    The OTel SDK already returns a ProxyTracer that no-ops when no provider
    has been installed, so this is safe to call at any time.
    """
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        # Fallback when the opentelemetry package isn't even installed:
        # return a dummy that supports `start_as_current_span` as a context manager.
        global _NOOP_TRACER
        if _NOOP_TRACER is None:
            _NOOP_TRACER = _DummyTracer()
        return _NOOP_TRACER


class _DummyTracer:
    """Fallback tracer when the opentelemetry package is absent. Produces
    context managers that do nothing so call sites don't have to check."""

    def start_as_current_span(self, *_a, **_k):
        return _DummySpanCM()


class _DummySpanCM:
    def __enter__(self):
        return _DummySpan()

    def __exit__(self, *_a):
        return False


class _DummySpan:
    def set_attribute(self, *_a, **_k):
        pass

    def set_status(self, *_a, **_k):
        pass

    def record_exception(self, *_a, **_k):
        pass
