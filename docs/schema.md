# Schema and capture adapters

Witness has a single canonical, versioned capture schema and a small adapter
layer that maps each agent framework onto it. New frameworks plug in without
touching the storage tables, the analyzers, the viewer, or the export path.

---

## SCHEMA_VERSION

`witness/schema.py` defines the stable serialization format used by adapters and
export. The records are pydantic v2 models that mirror the storage tables
one-to-one:

- `TraceRecord` - one agent run.
- `StepRecord` - one step within a run (action, timing, error, DOM and
  screenshot paths).
- `LLMCallRecord` - one model call attached to a step.
- `FindingRecord` - one analysis finding attached to a trace or step.

```python
from witness.schema import SCHEMA_VERSION
```

`SCHEMA_VERSION` is currently `"1.0"`. Bump it whenever the shape of any record
changes, so consumers of exported data can tell which fields to expect. Each
record exposes a `from_orm_obj` classmethod that converts the matching storage
row into the canonical record.

---

## The adapter pattern

A capture adapter recognizes a specific agent framework and attaches Witness
tracing to one of its agents. `witness.instrument(agent)` is a thin dispatcher:
it walks the adapter registry and delegates to the first adapter whose `detect`
returns true.

An adapter is any object satisfying the `CaptureAdapter` protocol in
`witness/adapters/base.py`:

```python
class CaptureAdapter(Protocol):
    name: str
    def detect(self, agent) -> bool: ...
    def instrument(self, agent) -> agent: ...
```

- `name` is a short identifier used for logging.
- `detect(agent)` is a cheap, side-effect-free duck-typing check that returns
  true when this adapter knows how to handle `agent`.
- `instrument(agent)` attaches tracing and returns the same agent for chaining.
  It must be idempotent: a second call is a no-op.

Adapters do not have to subclass anything (matching the protocol shape is
enough), but the bundled ones subclass `BaseAdapter` for the shared boilerplate.

### Bundled adapters

The registry lives in `witness/adapters/__init__.py` as `ADAPTERS`, walked in
order, so more specific adapters come before more general ones:

- `BrowserUseAdapter` - full instrumentation for `browser_use.Agent`. Wraps the
  agent's `step()` seam, snapshots the DOM and screenshot before and after each
  step, records the action, latency, and any error, and best-effort captures
  extra depth (network requests, console and JS errors, accessibility tree) via
  the page's CDP session when the running version exposes it.
- `PlaywrightAdapter` - scaffolding for a raw Playwright `Page`. Detection works
  today; `instrument` is intentionally not implemented yet because a Playwright
  script has no single `step` seam, so it fails loudly rather than silently
  swallowing traces.

---

## How to add a framework

1. Create a module under `witness/adapters/` with a class that subclasses
   `BaseAdapter` (or implements the `CaptureAdapter` protocol directly).
2. Implement `detect(agent)` as a cheap duck-typing check. Be specific enough
   that it does not shadow another adapter. For example, the Playwright adapter
   excludes objects that have a `task` attribute so it never claims a browser_use
   agent.
3. Implement `instrument(agent)`. Find the framework's per-step seam, and around
   each step:
   - call `witness.otel_bridge.set_active_step(step_id)` before the step and
     `reset_active_step(token)` after, so LLM spans are correlated to the right
     step;
   - snapshot the DOM and screenshot before and after;
   - record the action type, payload, latency, URL, and any error as a
     `StepRecord`-shaped step.
   Keep every extra capture fully guarded so a missing capability never breaks a
   step. Make `instrument` idempotent.
4. Register the adapter by adding an instance to `ADAPTERS` in
   `witness/adapters/__init__.py`, placing more specific adapters earlier.

Because every framework lands in the same canonical schema, the analyzers, the
viewer, the OTLP export, and the report exporter all work unchanged for a new
framework.
