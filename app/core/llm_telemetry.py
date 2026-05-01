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

import contextvars
import logging
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
  from opentelemetry.sdk.trace import ReadableSpan, Span

log = logging.getLogger(__name__)

_provider = None

_distinct_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("posthog_distinct_id", default=None)


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
  from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
  from posthog.ai.otel import PostHogSpanProcessor

  class _DistinctIdInjector(SpanProcessor):
    """Injects posthog.distinct_id from the request-scoped context variable
    into every span at creation time.

    PostHogSpanProcessor only processes gen_ai.* spans and reads
    posthog.distinct_id from the span's own attributes — it does not
    traverse parent spans. Without this injector, LLM spans created by
    the Google/Anthropic instrumentors would lack user attribution.
    """

    def on_start(self, span: Span, parent_context: object = None) -> None:
      distinct_id = _distinct_id_var.get()
      if distinct_id and span.is_recording():
        span.set_attribute("posthog.distinct_id", distinct_id)

    def on_end(self, span: ReadableSpan) -> None:
      pass

    def shutdown(self) -> None:
      pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
      return True

  _provider = TracerProvider(resource=Resource.create({"service.name": settings.POWERTOOLS_SERVICE_NAME}))
  _provider.add_span_processor(_DistinctIdInjector())
  _provider.add_span_processor(
    PostHogSpanProcessor(
      api_key=settings.POSTHOG_PROJECT_TOKEN,
      host=settings.POSTHOG_HOST,
    )
  )
  trace.set_tracer_provider(_provider)

  GoogleGenerativeAiInstrumentor().instrument()
  AnthropicInstrumentor().instrument()

  # ---- Workaround 1: AnthropicAsyncStream missing async-context-manager ----
  # The upstream wrapper implements __aiter__/__anext__ but not
  # __aenter__/__aexit__. The Anthropic SDK's AsyncStream supports both,
  # and pydantic-ai relies on `async with response:` after create(stream=True).
  # Without this patch every instrumented streaming call crashes with:
  #   "'AnthropicAsyncStream' object does not support the asynchronous
  #    context manager protocol"
  try:
    from opentelemetry.instrumentation.anthropic.streaming import AnthropicAsyncStream

    if not hasattr(AnthropicAsyncStream, "__aenter__"):

      async def _aenter(self):
        await self.__wrapped__.__aenter__()
        return self

      async def _aexit(self, exc_type, exc_val, exc_tb):
        if not self._instrumentation_completed:
          self._complete_instrumentation()
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

      AnthropicAsyncStream.__aenter__ = _aenter
      AnthropicAsyncStream.__aexit__ = _aexit
  except Exception:
    log.warning("Failed to patch AnthropicAsyncStream async-context-manager", exc_info=True)

  # ---- Workaround 2: Vertex AI beta messages not instrumented ----
  # The AnthropicInstrumentor patches the standard beta messages classes
  # (anthropic.resources.beta.messages.messages) and Bedrock-specific ones,
  # but NOT the Vertex AI-specific classes (anthropic.lib.vertex._beta_messages).
  # pydantic-ai calls client.beta.messages.create() which on AsyncAnthropicVertex
  # routes through the Vertex classes, leaving those calls uninstrumented.
  # Copy the FunctionWrapper descriptors so Vertex calls produce spans too.
  try:
    from anthropic.lib.vertex._beta_messages import AsyncMessages as VertexBetaAsync
    from anthropic.lib.vertex._beta_messages import Messages as VertexBetaSync
    from anthropic.resources.beta.messages.messages import AsyncMessages as StdBetaAsync
    from anthropic.resources.beta.messages.messages import Messages as StdBetaSync

    for method_name in ("create", "stream"):
      for std_cls, vertex_cls in ((StdBetaAsync, VertexBetaAsync), (StdBetaSync, VertexBetaSync)):
        patched = std_cls.__dict__.get(method_name)
        if patched is not None:
          setattr(vertex_cls, method_name, patched)
  except Exception:
    log.warning("Failed to patch Vertex AI Anthropic beta messages — Vertex LLM spans may be missing", exc_info=True)

  log.info("LLM telemetry initialized (host=%s)", settings.POSTHOG_HOST)


def set_request_distinct_id(distinct_id: str) -> None:
  """Store the PostHog distinct_id in request-scoped context.

  Call this once per request after the user is known so that all LLM spans
  created during the request carry the posthog.distinct_id attribute.
  The _DistinctIdInjector SpanProcessor reads this context variable in
  on_start and stamps it onto every new span.
  """
  _distinct_id_var.set(distinct_id)


def clear_request_distinct_id() -> None:
  """Reset the per-request distinct_id at the end of the request."""
  _distinct_id_var.set(None)


def flush() -> None:
  """Force-flush queued OTel spans to PostHog.

  Must be called at the end of each request in Lambda environments where
  the process may be frozen before the BatchSpanProcessor's background
  thread gets a chance to export — same reason ph_flush() exists.
  """
  if _provider is not None:
    _provider.force_flush(timeout_millis=2000)
