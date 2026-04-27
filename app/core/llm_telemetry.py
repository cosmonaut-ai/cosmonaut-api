"""PostHog AI analytics via OpenTelemetry for LLM call tracking.

Instruments both google-genai (Gemini) and anthropic (Claude via Vertex AI)
SDKs so that every LLM call automatically produces a $ai_generation event in
PostHog with model name, latency, token counts, and estimated cost.

Call init_llm_telemetry() once at application startup before any LLM calls
are made. No-ops when ENV is local or POSTHOG_PROJECT_TOKEN is empty.

In Lambda, call flush() at the end of each request so queued spans are
exported before the process can be frozen — same reason ph_flush() exists.

Call set_request_distinct_id(user_id) once per request (e.g. in middleware)
to associate all LLM spans in that request with a PostHog user.
"""

from __future__ import annotations

import logging

from app.core.config import settings

log = logging.getLogger(__name__)

_provider = None


def init_llm_telemetry() -> None:
  global _provider

  if _provider is not None:
    return

  if settings.ENV == "local" or not settings.POSTHOG_PROJECT_TOKEN:
    log.info("LLM telemetry disabled (env=%s)", settings.ENV)
    return

  from opentelemetry import trace
  from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
  from opentelemetry.instrumentation.google_generativeai import GoogleGenerativeAiInstrumentor
  from opentelemetry.sdk.resources import Resource
  from opentelemetry.sdk.trace import TracerProvider
  from posthog.ai.otel import PostHogSpanProcessor

  _provider = TracerProvider(resource=Resource.create({"service.name": settings.POWERTOOLS_SERVICE_NAME}))
  _provider.add_span_processor(
    PostHogSpanProcessor(
      api_key=settings.POSTHOG_PROJECT_TOKEN,
      host=settings.POSTHOG_HOST,
    )
  )
  trace.set_tracer_provider(_provider)

  GoogleGenerativeAiInstrumentor().instrument()
  AnthropicInstrumentor().instrument()

  log.info("LLM telemetry initialized (host=%s)", settings.POSTHOG_HOST)


def set_request_distinct_id(distinct_id: str) -> None:
  """Attach a PostHog distinct_id to the active OTel span for the current request.

  Call this once per request after the user is known so that LLM spans are
  attributed to the correct user in PostHog LLM analytics.
  """
  if _provider is None:
    return
  from opentelemetry import trace

  span = trace.get_current_span()
  if span.is_recording():
    span.set_attribute("posthog.distinct_id", distinct_id)


def flush() -> None:
  """Force-flush queued OTel spans to PostHog.

  Must be called at the end of each request in Lambda environments where
  the process may be frozen before the BatchSpanProcessor's background
  thread gets a chance to export — same reason ph_flush() exists.
  """
  if _provider is not None:
    _provider.force_flush(timeout_millis=2000)
