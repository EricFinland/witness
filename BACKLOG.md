# Backlog

What has shipped and what is still intentionally unbuilt.

## Shipped

- **Share links** - `witness share <trace_id>` uploads a trace to a hosted
  viewer and returns a public URL.
- **Regression / divergence detection** - `witness diff trace_a trace_b`
  compares two runs of the same task step-by-step and surfaces metric deltas and
  the first point of trajectory divergence. The bench harness computes
  divergences across a whole suite.
- **Cost dashboards** - `witness stats` aggregates spend and tokens overall, by
  model, and by day.
- **Intelligence layer** - prompt-injection detection, trajectory health,
  outcome / risk prediction, and PII/secret exfiltration detection, surfaced as
  findings in the viewer, the API, and `witness analyze`. See
  [docs/analysis.md](docs/analysis.md).
- **Reliability harness** - `witness bench` and the WitnessBench reference tasks
  report success rate, flakiness, and metric distributions. See
  [docs/bench.md](docs/bench.md).
- **HTML report exporter + GitHub Action** - `witness report` writes a
  self-contained HTML report, and a reusable workflow posts a trace summary as a
  sticky PR comment.
- **OTLP export** - opt-in span forwarding to Jaeger / Grafana / Honeycomb via
  `WITNESS_OTLP_ENDPOINT`, with GenAI semantic-convention attributes. See
  [docs/otel-export.md](docs/otel-export.md).
- **Schema-first capture adapters** - a versioned canonical schema
  (`witness/schema.py`) plus an adapter registry. browser_use is fully
  instrumented; Playwright detection is scaffolded. See
  [docs/schema.md](docs/schema.md).

## Still unbuilt

### More frameworks

- Playwright agents (detection exists; instrumentation still to wire up).
- Claude in Chrome event stream subscription.
- Stagehand, Skyvern, Manus (as users request).

### Real-time streaming viewer

Watch a running agent step-by-step as it executes.

### OpenAI / Bedrock / Gemini full support

OpenLLMetry already instruments these; the remaining work is pricing entries and
testing.

### Learned outcome model out of the box

The training harness exists behind the optional `[ml]` extra. A shipped,
pre-trained outcome model (and the calibration to back it) is still open.

### Team features

Workspaces, shared dashboards, RBAC. Cloud only, paid.
