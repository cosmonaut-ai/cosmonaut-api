"""PostHog AI analytics via OpenTelemetry for LLM call tracking.

Uses pydantic-ai's native OTel instrumentation (Agent.instrument_all) so every
agent run produces an `agent run` parent span plus `chat <model>` child spans
carrying gen_ai.* attributes — model, latency, token usage (including for
streamed Anthropic responses), and cost. The PostHogSpanProcessor exports
those spans to PostHog where they become $ai_trace/$ai_span/$ai_generation
events.

Call init_llm_telemetry() once at application startup before any LLM calls
are made. No-ops when ENV is local or POSTHOG_PROJECT_TOKEN is empty.

In Lambda, call flush() at the end of each request so queued spans are
exported before the process can be frozen — same reason ph_flush() exists.

Call set_request_distinct_id(user_id) once per request (e.g. in middleware)
to associate all LLM spans in that request with a PostHog user, and
set_llm_context(...) at service call sites to stamp world/node/session
context onto every span created in scope. Wrap each user-visible AI
operation in ai_trace_span(...) so its LLM calls share one trace.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from app.core.config import settings

if TYPE_CHECKING:
  from collections.abc import Iterator

  from opentelemetry.sdk.trace import ReadableSpan, Span

log = logging.getLogger(__name__)

_provider = None

_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
  "posthog_llm_context", default=None
)
# Trace id of the most recently opened ai_trace_span in this task's context.
# Survives span close so error handlers can link failure events to the trace.
_last_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
  "posthog_last_ai_trace_id", default=None
)

# Context keys stamped onto spans as posthog.properties.<name>. session_id is
# the client's PostHog replay session (from X-PostHog-Session-Id), mapped to
# $session_id so server-side AI events join the session replay; the domain
# WorldSession id is the separate world_session_id key.
_PROPERTY_KEYS = {
  "session_id": "$session_id",
  "world_session_id": "world_session_id",
  "world_id": "world_id",
  "node_id": "node_id",
  "user_id": "user_id",
}


def init_llm_telemetry() -> None:
  global _provider

  if _provider is not None:
    return

  if settings.ENV == "local" or not settings.POSTHOG_PROJECT_TOKEN:
    log.info("LLM telemetry disabled (env=%s)", settings.ENV)
    return

  from opentelemetry import trace
  from opentelemetry.sdk.resources import Resource
  from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
  from posthog.ai.otel import PostHogSpanProcessor
  from pydantic_ai import Agent
  from pydantic_ai.models.instrumented import InstrumentationSettings

  class _ContextInjector(SpanProcessor):
    """Injects request-scoped PostHog context into every span at creation.

    PostHogSpanProcessor only exports gen_ai.* spans and reads
    posthog.distinct_id (and posthog.properties.*) from the span's own
    attributes — it does not traverse parent spans. Without this injector,
    LLM spans created by pydantic-ai's instrumentation would lack user
    attribution and product context.
    """

    def on_start(self, span: Span, parent_context: object = None) -> None:
      context = _context_var.get()
      if not context or not span.is_recording():
        return
      distinct_id = context.get("distinct_id")
      if distinct_id:
        span.set_attribute("posthog.distinct_id", str(distinct_id))
      for key, property_name in _PROPERTY_KEYS.items():
        value = context.get(key)
        if value:
          span.set_attribute(f"posthog.properties.{property_name}", str(value))

    def on_end(self, span: ReadableSpan) -> None:
      pass

    def shutdown(self) -> None:
      pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
      return True

  _provider = TracerProvider(resource=Resource.create({"service.name": settings.POWERTOOLS_SERVICE_NAME}))
  _provider.add_span_processor(_ContextInjector())
  _provider.add_span_processor(
    PostHogSpanProcessor(
      api_key=settings.POSTHOG_PROJECT_TOKEN,
      host=settings.POSTHOG_HOST,
    )
  )
  trace.set_tracer_provider(_provider)

  # Instruments every Agent (applied per run, so agents constructed at import
  # time are covered). Emits an `agent run` parent span plus `chat <model>`
  # child spans with gen_ai.* semconv attributes; token usage for streamed
  # responses is recorded after the stream completes.
  Agent.instrument_all(InstrumentationSettings(tracer_provider=_provider))

  log.info("LLM telemetry initialized (host=%s)", settings.POSTHOG_HOST)


def set_request_distinct_id(distinct_id: str) -> None:
  """Store the PostHog distinct_id in request-scoped context.

  Call this once per request after the user is known so that all LLM spans
  created during the request carry the posthog.distinct_id attribute.
  """
  context = dict(_context_var.get() or {})
  context["distinct_id"] = distinct_id
  _context_var.set(context)


def clear_request_distinct_id() -> None:
  """Reset the per-request telemetry context at the end of the request."""
  _context_var.set(None)


def get_request_distinct_id() -> str | None:
  """Return the distinct_id stored for the current request, if any."""
  context = _context_var.get()
  return context.get("distinct_id") if context else None


def get_llm_context() -> dict[str, Any]:
  """Return a copy of the telemetry context for the current task (distinct_id
  plus any product context set via set_llm_context)."""
  return dict(_context_var.get() or {})


def set_llm_context(**values: Any) -> contextvars.Token:
  """Merge product context (session_id, world_id, node_id, user_id) into the
  telemetry context for the current task.

  Returns a token; pass it to reset_llm_context() in a finally block to
  restore the previous context.
  """
  context = dict(_context_var.get() or {})
  context.update({key: value for key, value in values.items() if value is not None})
  return _context_var.set(context)


def reset_llm_context(token: contextvars.Token) -> None:
  """Restore the telemetry context saved by set_llm_context()."""
  _context_var.reset(token)


def current_ai_trace_id() -> str | None:
  """Return the trace id (32-char hex) of the active or most recent
  ai_trace_span in this task's context, for linking manual PostHog events
  ($ai_generation captures, failure events) to the OTel trace."""
  if _provider is not None:
    from opentelemetry import trace

    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
      return format(span_context.trace_id, "032x")
  return _last_trace_id_var.get()


@contextmanager
def ai_trace_span(name: str, **attributes: Any) -> Iterator[Any]:
  """Open a root span grouping all LLM/AI work for one user-visible operation
  (story generation, worker task, audio narration) into a single PostHog trace.

  The gen_ai.operation.name attribute is required for the PostHogSpanProcessor
  to export the span. No-ops (yields None) when telemetry is disabled.
  """
  if _provider is None:
    yield None
    return

  from opentelemetry import trace

  tracer = trace.get_tracer("cosmonaut")
  span_attributes: dict[str, Any] = {"gen_ai.operation.name": "chain"}
  span_attributes.update({key: value for key, value in attributes.items() if value is not None})
  with tracer.start_as_current_span(name, attributes=span_attributes) as span:
    span_context = span.get_span_context()
    if span_context.is_valid:
      _last_trace_id_var.set(format(span_context.trace_id, "032x"))
    yield span


def flush() -> None:
  """Force-flush queued OTel spans to PostHog.

  Must be called at the end of each request in Lambda environments where
  the process may be frozen before the BatchSpanProcessor's background
  thread gets a chance to export — same reason ph_flush() exists.
  """
  if _provider is not None:
    _provider.force_flush(timeout_millis=2000)
