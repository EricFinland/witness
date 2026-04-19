"""Bridge OpenLLMetry spans → buffered LLMCall records, correlated per step.

Flow:
  1. `init_tracing()` registers a WitnessSpanProcessor with the global TracerProvider.
  2. The SDK sets `_active_step_id` (ContextVar) before each agent step runs.
  3. As OpenLLMetry emits spans for Anthropic/OpenAI calls, the processor snapshots
     them and appends to an in-memory list keyed by the active step id.
  4. When the step ends, the SDK calls `drain_for_step(step_id)` to flush those
     snapshots into the DB as LLMCall rows.

Why ContextVar? The Agent's step() is async; spans may arrive from nested tasks.
ContextVars propagate across asyncio tasks the way thread-locals don't.
"""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

# The step id currently "in flight" on this async context, or None if outside a step.
_active_step_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "witness_active_step_id", default=None
)


@dataclass
class LLMSpanSnapshot:
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    prompt: str
    response: str
    end_ts_ns: int


@dataclass
class _Buffer:
    lock: threading.Lock = field(default_factory=threading.Lock)
    by_step: dict[int, list[LLMSpanSnapshot]] = field(default_factory=dict)


_buffer = _Buffer()


class WitnessSpanProcessor(SpanProcessor):
    """Tap into every finished span; keep LLM ones, drop the rest."""

    def on_start(self, span, parent_context=None):  # noqa: D401
        return None

    def on_end(self, span: ReadableSpan) -> None:
        step_id = _active_step_id.get()
        if step_id is None:
            return
        snap = _maybe_extract_llm(span)
        if snap is None:
            return
        with _buffer.lock:
            _buffer.by_step.setdefault(step_id, []).append(snap)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _attr(span: ReadableSpan, key: str, default=None):
    if span.attributes is None:
        return default
    return span.attributes.get(key, default)


def _maybe_extract_llm(span: ReadableSpan) -> LLMSpanSnapshot | None:
    """Return a snapshot if this looks like an LLM span, else None.

    OpenLLMetry/OpenInference semantic conventions use `gen_ai.*` or `llm.*`
    attributes. We accept either family; OpenLLMetry currently emits both
    depending on the instrumentation version.
    """
    attrs = span.attributes or {}
    if not attrs:
        return None

    model = (
        attrs.get("gen_ai.response.model")
        or attrs.get("gen_ai.request.model")
        or attrs.get("llm.response.model")
        or attrs.get("llm.request.model")
    )
    if not model:
        # Not an LLM span.
        return None

    prompt_tokens = int(
        attrs.get("gen_ai.usage.input_tokens")
        or attrs.get("gen_ai.usage.prompt_tokens")
        or attrs.get("llm.usage.prompt_tokens")
        or 0
    )
    completion_tokens = int(
        attrs.get("gen_ai.usage.output_tokens")
        or attrs.get("gen_ai.usage.completion_tokens")
        or attrs.get("llm.usage.completion_tokens")
        or 0
    )

    # OpenLLMetry records prompts/responses as indexed prefix attributes.
    prompt = _join_indexed(attrs, ["gen_ai.prompt", "llm.prompts"])
    response = _join_indexed(attrs, ["gen_ai.completion", "llm.completions"])

    if span.start_time is not None and span.end_time is not None:
        latency_ms = max(0, (span.end_time - span.start_time) // 1_000_000)
    else:
        latency_ms = 0

    return LLMSpanSnapshot(
        model=str(model),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        prompt=prompt,
        response=response,
        end_ts_ns=span.end_time or 0,
    )


def _join_indexed(attrs: dict, prefixes: list[str]) -> str:
    """OpenLLMetry packs messages as `gen_ai.prompt.0.content`, `...1.content`, etc.

    We concatenate them into a single string for display. This is lossy but fine
    for v0 — the viewer renders the joined text in a monospace block.
    """
    out: list[str] = []
    for prefix in prefixes:
        i = 0
        while True:
            role = attrs.get(f"{prefix}.{i}.role")
            content = attrs.get(f"{prefix}.{i}.content")
            if role is None and content is None:
                break
            if role:
                out.append(f"[{role}]")
            if content:
                out.append(str(content))
            i += 1
        if out:
            break
    return "\n".join(out)


def set_active_step(step_id: int | None) -> contextvars.Token:
    """Call before a step starts. Pair with reset_active_step(token) after."""
    return _active_step_id.set(step_id)


def reset_active_step(token: contextvars.Token) -> None:
    _active_step_id.reset(token)


def drain_for_step(step_id: int) -> list[LLMSpanSnapshot]:
    """Pop and return all buffered LLM snapshots for a step."""
    with _buffer.lock:
        return _buffer.by_step.pop(step_id, [])


_initialized = False
_init_lock = threading.Lock()


class _NoopExporter:
    """Swallow spans that Traceloop would otherwise try to ship remotely.
    Our WitnessSpanProcessor already captured them on on_end."""

    def export(self, spans):  # returns SpanExportResult.SUCCESS
        from opentelemetry.sdk.trace.export import SpanExportResult

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def init_tracing(app_name: str = "witness") -> None:
    """Install Traceloop + our span processor. Idempotent."""
    global _initialized
    with _init_lock:
        if _initialized:
            return

        import os

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from traceloop.sdk import Traceloop

        # Silence Traceloop's cloud exporter — we're local-first. The NoopExporter
        # we hand to Traceloop.init() replaces the default OTLP HTTP exporter so
        # spans never leave the process.
        os.environ.setdefault("TRACELOOP_TELEMETRY", "false")

        Traceloop.init(
            app_name=app_name,
            disable_batch=True,
            exporter=_NoopExporter(),
            telemetry_enabled=False,
        )

        provider = trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            provider.add_span_processor(WitnessSpanProcessor())
        else:
            # Some envs return a ProxyTracerProvider; fall back to registering our own.
            new_provider = TracerProvider()
            new_provider.add_span_processor(WitnessSpanProcessor())
            trace.set_tracer_provider(new_provider)

        _initialized = True
