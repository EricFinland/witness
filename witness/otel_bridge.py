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
import logging
import threading
from dataclasses import dataclass, field

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

logger = logging.getLogger("witness")

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


# --- OTel GenAI semantic conventions -------------------------------------
#
# We normalize toward the OpenTelemetry GenAI semantic conventions so that
# spans we forward to an OTLP collector (Jaeger/Grafana/Honeycomb) are
# convention-compliant even when the upstream instrumentation only emitted
# the older OpenLLMetry `llm.*` attributes. The conventions we set:
#
#   gen_ai.system                -> provider/system name (e.g. "anthropic", "openai")
#   gen_ai.operation.name        -> "chat" (the operation kind)
#   gen_ai.request.model         -> requested model id
#   gen_ai.response.model        -> model id returned by the provider
#   gen_ai.usage.input_tokens    -> prompt/input token count
#   gen_ai.usage.output_tokens   -> completion/output token count
#
# Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
#
# Mapping from the legacy OpenLLMetry `llm.*` names we may see upstream:
_LLM_TO_GENAI: dict[str, str] = {
    "llm.request.model": "gen_ai.request.model",
    "llm.response.model": "gen_ai.response.model",
    "llm.usage.prompt_tokens": "gen_ai.usage.input_tokens",
    "llm.usage.completion_tokens": "gen_ai.usage.output_tokens",
    "llm.request.type": "gen_ai.operation.name",
    "llm.vendor": "gen_ai.system",
    "llm.system": "gen_ai.system",
}


def _infer_genai_system(model: str | None) -> str | None:
    """Best-effort provider name from a model id (Anthropic/OpenAI/etc.)."""
    if not model:
        return None
    m = model.lower()
    if m.startswith("claude") or "anthropic" in m:
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt")) or "openai" in m:
        return "openai"
    if m.startswith("gemini") or "google" in m:
        return "gcp.gen_ai"
    return None


def genai_attributes(attrs: dict) -> dict:
    """Compute the OTel GenAI (`gen_ai.*`) attributes for a set of span attrs.

    Reads both the modern `gen_ai.*` and the legacy `llm.*` families and returns
    a dict of normalized `gen_ai.*` attributes. Existing `gen_ai.*` values win;
    `llm.*` values fill the gaps. Keys whose value cannot be determined are
    omitted so we never write empty/None attributes onto a span.
    """
    attrs = attrs or {}
    out: dict[str, object] = {}

    for legacy, canonical in _LLM_TO_GENAI.items():
        if canonical in attrs and attrs[canonical] not in (None, ""):
            continue
        val = attrs.get(legacy)
        if val not in (None, ""):
            out[canonical] = val

    # Carry forward any gen_ai.* values that are already present and non-empty so
    # callers/tests can rely on the returned dict being the full normalized view.
    for canonical in set(_LLM_TO_GENAI.values()):
        if canonical in attrs and attrs[canonical] not in (None, ""):
            out[canonical] = attrs[canonical]

    # Operation name: default to "chat" for any LLM span.
    model = (
        attrs.get("gen_ai.response.model")
        or attrs.get("gen_ai.request.model")
        or out.get("gen_ai.response.model")
        or out.get("gen_ai.request.model")
        or attrs.get("llm.response.model")
        or attrs.get("llm.request.model")
    )
    if model and "gen_ai.operation.name" not in attrs and "gen_ai.operation.name" not in out:
        out["gen_ai.operation.name"] = "chat"

    # System/provider: infer from the model id when upstream did not set it.
    if (
        model
        and "gen_ai.system" not in attrs
        and "gen_ai.system" not in out
    ):
        system = _infer_genai_system(str(model))
        if system:
            out["gen_ai.system"] = system

    return out


class GenAINormalizingSpanProcessor(SpanProcessor):
    """Set OTel GenAI (`gen_ai.*`) attributes on LLM spans before export.

    Upstream instrumentation sometimes emits only the legacy `llm.*` attributes.
    This processor copies/derives the equivalent `gen_ai.*` attributes onto the
    span at on_end so anything downstream (an OTLP exporter) sees compliant
    GenAI semantic-convention attributes. Spans that are not LLM-related are
    left untouched.

    The span handed to on_end is read-only by type (ReadableSpan); when the
    concrete object still exposes ``set_attribute`` (it usually does, since the
    SDK passes the same Span instance) we set the attributes there, otherwise we
    skip silently. on_end runs before the exporter's BatchSpanProcessor in
    registration order, so the normalized attributes are present at export time.
    """

    def on_start(self, span, parent_context=None):  # noqa: D401
        return None

    def on_end(self, span: ReadableSpan) -> None:
        attrs = span.attributes or {}
        if not attrs:
            return
        derived = genai_attributes(dict(attrs))
        if not derived:
            return
        setter = getattr(span, "set_attribute", None)
        if not callable(setter):
            return
        for key, value in derived.items():
            if attrs.get(key) in (None, ""):
                try:
                    setter(key, value)
                except Exception:  # noqa: BLE001 - never break the pipeline on export
                    logger.debug("could not set gen_ai attribute %s", key, exc_info=True)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


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

    # OpenLLMetry 0.60+ emits OTel GenAI conventions: system_instructions +
    # input.messages + output.messages as JSON-serialized strings. Older
    # versions used indexed gen_ai.prompt.{i}.content / gen_ai.completion.{i}.*.
    prompt = _structured_prompt(attrs) or _join_indexed(
        attrs, ["gen_ai.prompt", "llm.prompts"]
    )
    response = _structured_response(attrs) or _join_indexed(
        attrs, ["gen_ai.completion", "llm.completions"]
    )

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


def _structured_prompt(attrs: dict) -> str:
    parts: list[str] = []
    sys_instr = attrs.get("gen_ai.system_instructions")
    if sys_instr:
        parts.append(_render_messages(sys_instr, default_role="system"))
    input_msgs = attrs.get("gen_ai.input.messages")
    if input_msgs:
        parts.append(_render_messages(input_msgs))
    return "\n\n".join(p for p in parts if p)


def _structured_response(attrs: dict) -> str:
    output_msgs = attrs.get("gen_ai.output.messages")
    return _render_messages(output_msgs) if output_msgs else ""


def _render_messages(value, default_role: str | None = None) -> str:
    """Render OpenLLMetry's JSON-serialized messages array as readable text."""
    import json as _json

    if isinstance(value, str):
        try:
            data = _json.loads(value)
        except _json.JSONDecodeError:
            return value
    else:
        data = value
    if not isinstance(data, list):
        return str(data)

    out: list[str] = []
    for msg in data:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or default_role
        if role:
            out.append(f"[{role}]")
        for part in msg.get("parts") or []:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type", "")
            if ptype == "text":
                out.append(str(part.get("content") or part.get("text") or ""))
            elif ptype == "tool_call":
                name = part.get("name", "")
                args = part.get("arguments")
                out.append(f"<tool_call: {name}>")
                if args is not None:
                    out.append(_json.dumps(args, indent=2, default=str))
            elif ptype == "tool_call_response":
                out.append(f"<tool_result: {part.get('id','')}>")
                out.append(str(part.get("response") or part.get("content") or ""))
            else:
                out.append(_json.dumps(part, default=str))
        if not msg.get("parts"):
            content = msg.get("content")
            if isinstance(content, str):
                out.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict):
                        out.append(
                            str(c.get("text") or c.get("content") or _json.dumps(c, default=str))
                        )
                    else:
                        out.append(str(c))
            elif content is not None:
                out.append(str(content))
    return "\n".join(s for s in out if s)


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


def _install_otlp_export(provider, endpoint: str) -> bool:
    """Opt-in: add an OTLP HTTP exporter so spans ship to a collector.

    Returns True if the exporter was installed, False if it was skipped (for
    example because the optional exporter package is not present). Never raises:
    a failure here must not take down local-first capture.

    The exporter lib (`opentelemetry-exporter-otlp-proto-http`) is an OPTIONAL
    extra. Install it with: pip install witness[otlp].
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "WITNESS_OTLP_ENDPOINT=%s is set but the OTLP exporter is not "
            "installed; skipping remote export. Install it with "
            "'pip install witness[otlp]' (opentelemetry-exporter-otlp-proto-http).",
            endpoint,
        )
        return False

    try:
        exporter = OTLPSpanExporter(endpoint=endpoint)
        # Normalize gen_ai.* attributes BEFORE the batch exporter runs so the
        # spans we ship are OTel GenAI semantic-convention compliant.
        provider.add_span_processor(GenAINormalizingSpanProcessor())
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:  # noqa: BLE001 - remote export is best-effort, never fatal
        logger.warning(
            "failed to install OTLP exporter for endpoint %s; continuing "
            "local-first",
            endpoint,
            exc_info=True,
        )
        return False

    logger.info("OTLP span export enabled -> %s", endpoint)
    return True


def init_tracing(app_name: str = "witness", otlp_endpoint: str | None = None) -> None:
    """Install Traceloop + our span processor. Idempotent.

    By default Witness is local-first: spans are captured in-process by the
    WitnessSpanProcessor and a NoopExporter swallows everything else, so nothing
    leaves the machine.

    Remote export is OPT-IN. Pass `otlp_endpoint` or set the environment variable
    `WITNESS_OTLP_ENDPOINT` (e.g. "http://localhost:4318/v1/traces") to also ship
    spans to an OTLP-compatible collector (Jaeger, Grafana Tempo, Honeycomb,
    etc.) via a BatchSpanProcessor. The forwarded spans carry OTel GenAI
    semantic-convention (`gen_ai.*`) attributes.
    """
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
            provider = new_provider

        # Opt-in OTLP export. Explicit arg wins; otherwise read the env var.
        endpoint = otlp_endpoint or os.environ.get("WITNESS_OTLP_ENDPOINT")
        if endpoint and isinstance(provider, TracerProvider):
            _install_otlp_export(provider, endpoint)

        _initialized = True
