"""
Tests for observability.py — OTel wiring.

Strategy:
- Install an InMemorySpanExporter onto a fresh TracerProvider and verify
  `base_agent.call_llm` emits a span with the expected attributes.
- Also assert that `configure_otel` no-ops when `OTEL_EXPORTER_OTLP_ENDPOINT`
  is unset (local-dev / air-gap story).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agents.base_agent import BaseAgent


# ---------------------------------------------------------------------------
# In-memory tracer setup — install once per session, reset per test.
# ---------------------------------------------------------------------------

@pytest.fixture
def span_exporter(monkeypatch):
    """Install a fresh InMemorySpanExporter + TracerProvider for this test."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Point the global OTel API at our in-memory provider for this test.
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    # Also rebind the cached tracer inside base_agent to the new provider.
    import agents.base_agent as ba
    monkeypatch.setattr(ba, "_otel_tracer", provider.get_tracer("hardware-pipeline.agent"))
    yield exporter
    provider.shutdown()


class _ProbeAgent(BaseAgent):
    """Minimal concrete agent — just enough to exercise call_llm."""
    def __init__(self):
        super().__init__(phase_number="P_TEST", phase_name="Probe", model="stub-model")
        self.fallback_chain = ["stub-model"]

    def get_system_prompt(self, project_context: dict) -> str:
        return "you are a test agent"

    async def execute(self, project_context: dict, user_input: str) -> dict:
        return {}


# ---------------------------------------------------------------------------
# configure_otel — gate behaviour
# ---------------------------------------------------------------------------

def test_configure_otel_skips_when_endpoint_unset(monkeypatch):
    """No endpoint → return False, don't touch the global provider."""
    from observability import configure_otel, _CONFIGURED  # noqa: F401
    import observability
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    # Reset module-level flag so the test sees a clean state.
    monkeypatch.setattr(observability, "_CONFIGURED", False)
    assert configure_otel(app=None, engine=None) is False


def test_tracer_returns_something_even_when_otel_not_configured(monkeypatch):
    """Call sites must be able to use the tracer unconditionally — even when
    OTel isn't wired up, the returned object must support
    start_as_current_span as a context manager."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from observability import tracer
    t = tracer("test")
    with t.start_as_current_span("noop") as span:
        # Attribute setters must not raise
        span.set_attribute("k", "v")


# ---------------------------------------------------------------------------
# call_llm emits an llm.<phase> span with expected attributes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_llm_emits_span_on_success(span_exporter):
    agent = _ProbeAgent()

    fake_result = {
        "content": "hi",
        "tool_calls": [],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 11, "output_tokens": 22},
    }
    with patch.object(
        agent, "_call_model", new=AsyncMock(return_value=fake_result),
    ):
        result = await agent.call_llm([{"role": "user", "content": "hi"}])

    assert result["content"] == "hi"

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "llm.P_TEST"
    attrs = dict(span.attributes or {})
    assert attrs["llm.phase"] == "P_TEST"
    assert attrs["llm.model_used"] == "stub-model"
    assert attrs["llm.tokens_in"] == 11
    assert attrs["llm.tokens_out"] == 22
    assert attrs["llm.stop_reason"] == "end_turn"
    assert attrs["llm.tool_calls"] == 0
    assert attrs["llm.message_count"] == 1


@pytest.mark.asyncio
async def test_call_llm_span_records_exception_when_all_models_fail(span_exporter):
    agent = _ProbeAgent()
    with patch.object(
        agent, "_call_model",
        new=AsyncMock(side_effect=RuntimeError("network down")),
    ):
        with pytest.raises(RuntimeError, match="fallback chain failed"):
            await agent.call_llm([{"role": "user", "content": "x"}])

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    # Status set to ERROR
    assert span.status.status_code.name == "ERROR"
    # Exception recorded as a span event
    assert any(ev.name == "exception" for ev in span.events)


@pytest.mark.asyncio
async def test_call_llm_captures_tool_call_count(span_exporter):
    agent = _ProbeAgent()
    fake_result = {
        "content": "",
        "tool_calls": [{"name": "x"}, {"name": "y"}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    with patch.object(
        agent, "_call_model", new=AsyncMock(return_value=fake_result),
    ):
        await agent.call_llm(
            [{"role": "user", "content": "hi"}],
            tools=[{"name": "x"}, {"name": "y"}, {"name": "z"}],
        )

    spans = span_exporter.get_finished_spans()
    attrs = dict(spans[0].attributes or {})
    assert attrs["llm.tool_calls"] == 2
    assert attrs["llm.tool_count"] == 3  # tools offered (schema count)
