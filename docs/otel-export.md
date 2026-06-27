# OpenTelemetry export

Witness is local-first by default. Spans are captured in-process and nothing
leaves your machine. If you already run an observability stack, you can opt in to
forwarding the LLM spans Witness sees to any OTLP-compatible collector (Jaeger,
Grafana Tempo, Honeycomb, and others) without giving up local capture.

---

## Enabling OTLP export

Export is opt-in and controlled by a single environment variable:

```bash
export WITNESS_OTLP_ENDPOINT="http://localhost:4318/v1/traces"
```

When `WITNESS_OTLP_ENDPOINT` is set, Witness adds an OTLP HTTP exporter (via a
batch span processor) alongside the in-process capture. You can also pass the
endpoint explicitly:

```python
from witness.otel_bridge import init_tracing

init_tracing(otlp_endpoint="http://localhost:4318/v1/traces")
```

An explicit argument wins over the environment variable. Export never breaks
local capture: if the endpoint is unreachable or the export fails, Witness logs a
warning and keeps recording locally.

### The optional exporter package

The OTLP exporter library is an optional dependency. Install it with:

```bash
pip install witness[otlp]
```

If `WITNESS_OTLP_ENDPOINT` is set but the exporter package
(`opentelemetry-exporter-otlp-proto-http`) is not installed, Witness logs a
warning and skips remote export rather than failing. Local capture is unaffected.

---

## GenAI semantic conventions

Forwarded spans are normalized to the OpenTelemetry GenAI semantic conventions so
they are convention-compliant in your collector even when the upstream
instrumentation only emitted the older OpenLLMetry `llm.*` attributes. A
normalizing span processor runs before the batch exporter and sets:

| attribute | meaning |
| --- | --- |
| `gen_ai.system` | provider name (`anthropic`, `openai`, `gcp.gen_ai`) |
| `gen_ai.operation.name` | the operation kind (`chat`) |
| `gen_ai.request.model` | requested model id |
| `gen_ai.response.model` | model id returned by the provider |
| `gen_ai.usage.input_tokens` | prompt / input token count |
| `gen_ai.usage.output_tokens` | completion / output token count |

Existing `gen_ai.*` values are preserved; legacy `llm.*` values only fill the
gaps. When the provider is not set upstream, it is inferred from the model id.
Attributes that cannot be determined are omitted rather than written empty.

Reference: <https://opentelemetry.io/docs/specs/semconv/gen-ai/>
