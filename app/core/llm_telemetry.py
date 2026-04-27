"""PostHog AI analytics via OpenTelemetry for LLM call tracking.

Instruments both google-genai (Gemini) and anthropic (Claude via Vertex AI)
SDKs so that every LLM call automatically produces a $ai_generation event in
PostHog with model name, latency, token counts, and estimated cost.

Call init_llm_telemetry() once at application startup before any LLM calls
are made. No-ops when ENV is local or POSTHOG_PROJECT_TOKEN is empty.
"""

from __future__ import annotations

import logging

from app.core.config import settings

log = logging.getLogger(__name__)

_initialized = False


def init_llm_telemetry() -> None:
  global _initialized

  if _initialized:
    return

  if settings.ENV == "local" or not settings.POSTHOG_PROJECT_TOKEN:
    log.info("LLM telemetry disabled (env=%s)", settings.ENV)
    return

  from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
  from opentelemetry.instrumentation.google_generativeai import GoogleGenerativeAiInstrumentor
  from opentelemetry.sdk.resources import Resource
  from opentelemetry.sdk.trace import TracerProvider
  from posthog.ai.otel import PostHogSpanProcessor

  provider = TracerProvider(resource=Resource.create({"service.name": settings.POWERTOOLS_SERVICE_NAME}))
  provider.add_span_processor(
    PostHogSpanProcessor(
      api_key=settings.POSTHOG_PROJECT_TOKEN,
      host=settings.POSTHOG_HOST,
    )
  )

  GoogleGenerativeAiInstrumentor().instrument(tracer_provider=provider)
  AnthropicInstrumentor().instrument(tracer_provider=provider)

  _initialized = True
  log.info("LLM telemetry initialized (host=%s)", settings.POSTHOG_HOST)
