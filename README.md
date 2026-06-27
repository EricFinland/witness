<div align="center">

# Witness

[![PyPI version](https://img.shields.io/pypi/v/usewitness.svg)](https://pypi.org/project/usewitness/)
[![Python versions](https://img.shields.io/pypi/pyversions/usewitness.svg)](https://pypi.org/project/usewitness/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/EricFinland/witness/blob/main/LICENSE)
[![Twitter Follow](https://img.shields.io/twitter/follow/usewitness?style=social)](https://twitter.com/usewitness)

**See what your browser agent actually did.**

Local-first observability for browser agents. DOM diffs, screenshot scrubbing, LLM cost per step, action replay — all in a viewer that lives on your machine.

![demo](docs/demo.gif)

</div>

---

## Install

```bash
pip install usewitness
playwright install chromium
```

```python
import asyncio
import witness
from browser_use import Agent
from browser_use.llm import ChatAnthropic

async def main():
    agent = Agent(
        task="Find the top story on Hacker News and return its title.",
        llm=ChatAnthropic(model="claude-sonnet-4-5"),
    )
    witness.instrument(agent)
    await agent.run()

asyncio.run(main())
```

```bash
witness view
```

Opens a viewer at `localhost:7842` with every step your agent took.

---

## Why this exists

Browser agents fail. A lot. Browser Use, Claude in Chrome, Operator, Skyvern — they all crash on step 7 of a 12-step workflow and leave you squinting at a stack trace that tells you nothing about *why*.

You need the DOM at the moment it broke. The screenshot before and after the click that went wrong. The exact prompt Claude saw when it picked the wrong button. The token cost of the retry loop that spiraled.

Witness captures all of it, automatically, with one line of code.

---

## What it captures

For every step an agent takes:

- **DOM before and after** — rendered as a side-by-side diff with line-level highlighting
- **Screenshots before and after** — with a compare slider so you can see what changed visually
- **The action** — structured JSON showing exactly what the agent tried to do
- **LLM calls** — model, tokens, cost, latency, full prompt and response
- **Errors** — captured with the step they occurred on, not buried in a trace

Everything is stored locally in SQLite + flat files under `~/.witness/`. Nothing leaves your machine.

---

## Intelligence layer

Capture tells you what the agent did. The intelligence layer tells you whether it
was safe and on-track. Run `witness analyze <trace_id>` (or `--all`) and the
findings show up in the viewer, over the API, and in the terminal.

- **Prompt-injection detection** flags indirect prompt injection: it fires on the
  conjunction of injection-shaped content in the captured DOM (instruction text
  aimed at an agent, hidden or offscreen text, exfiltration-shaped URLs) and a
  deviating next action that obeys the injection or drifts off task. Benign pages
  do not produce false highs.
- **Trajectory health** scores the step sequence for loops/thrash, task drift
  (stdlib lexical similarity, no embeddings), wasted/dead-end steps, and
  backtracking, then rolls them into one health badge per trace.
- **Outcome / risk prediction** estimates the probability a (possibly partial)
  run will succeed using a transparent heuristic over cheap features, with an
  optional ML training harness behind the `[ml]` extra to learn a real classifier
  from labelled runs.
- **PII / secret exfiltration detection** catches credentials and PII typed into
  page fields and DOM-to-action carry-over, with every matched value masked in
  the stored evidence.

Findings are stored as a versioned `Finding` model (kind, severity, score, title,
detail, evidence) and served at `GET /api/traces/{id}/findings` and `POST
/api/traces/{id}/analyze`. Details in [`docs/analysis.md`](docs/analysis.md).

Around the analyzers:

- **Schema-first capture adapters** - a single versioned capture schema
  (`witness/schema.py`, `SCHEMA_VERSION`) plus an adapter registry under
  `witness/adapters/` so new frameworks plug in without touching storage, the
  analyzers, or the viewer. browser_use is fully instrumented; Playwright is
  scaffolded. See [`docs/schema.md`](docs/schema.md).
- **Reliability harness** - `witness bench` and the WitnessBench reference tasks
  run a task repeatedly and report success rate, flakiness, metric distributions,
  and trajectory divergence. See [`docs/bench.md`](docs/bench.md).
- **HTML report exporter + GitHub Action** - `witness report` writes a
  self-contained HTML report for a trace, and a reusable workflow posts a trace
  summary as a sticky PR comment.
- **OTLP export** - set `WITNESS_OTLP_ENDPOINT` to forward LLM spans to Jaeger,
  Grafana, or Honeycomb with OpenTelemetry GenAI semantic-convention attributes,
  while local capture keeps working. See [`docs/otel-export.md`](docs/otel-export.md).

---

## Screenshots

![trace list](docs/screenshots/01_list.png)
*Every run, with cost and step count at a glance.*

![DOM diff](docs/screenshots/03_detail_dom.png)
*Real side-by-side DOM diff — see exactly what the page did.*

![LLM call detail](docs/screenshots/06_detail_llm_modal.png)
*Full prompt and response for every LLM call. With cost.*

---

## How it works

Witness wraps your agent's `step()` method with a thin instrumentation layer. Before each step it snapshots the page (DOM + screenshot); after each step it records the action taken, the latency, and any error. LLM calls are captured via [OpenLLMetry](https://github.com/traceloop/openllmetry)'s OpenTelemetry instrumentation of the Anthropic and OpenAI SDKs — Witness attaches them to the correct step using a `ContextVar`.

Storage is SQLite (metadata) plus flat files (DOMs and screenshots) under `~/.witness/`. The viewer is a static Vite bundle served by a local FastAPI server. No cloud, no auth, no phone-home.

Claude Sonnet 4.5 and Opus 4.5 pricing are built in, as well as OpenAI GPT-4o, GPT-4.1, and the other common models. Unknown models log at $0 and raise a warning so you notice.

---

## Examples

Four recipes in [`examples/`](examples/) to copy from:

- [`hn_top_story.py`](examples/hn_top_story.py) — a simple successful run
- [`form_fill.py`](examples/form_fill.py) — contact form with multiple inputs
- [`multi_tab.py`](examples/multi_tab.py) — agent working across two tabs
- [`intentional_failure.py`](examples/intentional_failure.py) — a deliberately failing run, to show what error traces look like

---

## CLI

```bash
witness view              # open the viewer at localhost:7842
witness ls                # list recent traces in the terminal
witness rm <trace_id>     # delete a single trace
witness rm --all          # delete every trace (asks first)
witness analyze <id>      # run the analyzers and store findings (--all for every trace)
witness bench [suite]     # reliability report across captured runs
witness report <id>       # write a self-contained HTML report for a trace
witness diff a b          # compare two traces step-by-step
witness stats             # aggregate cost and token usage
witness share <id>        # upload a trace to a hosted viewer
witness config            # show config path and current settings
```

Telemetry is off by default and there is no toggle to turn it on. Witness never makes a network request you didn't ask for.

---

## Roadmap

Short list of what's coming, roughly in order:

- **More frameworks** - finish Playwright instrumentation, then Claude in Chrome event streams
- **Real-time streaming viewer** - watch a running agent step-by-step as it executes
- **Pre-trained outcome model** - ship a learned outcome predictor, not just the heuristic and training harness
- **OpenAI / Bedrock / Gemini** - full pricing and testing for the providers OpenLLMetry already instruments

Full backlog: [`BACKLOG.md`](BACKLOG.md)

---

## Contributing

The SDK and viewer are both small enough to read in an afternoon. Good first issues are labeled [`good first issue`](https://github.com/EricFinland/witness/issues?q=label%3A%22good+first+issue%22) — currently around action-type icons, additional model pricing entries, and Playwright-agent instrumentation.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for dev setup.

---

## License

MIT. See [`LICENSE`](LICENSE).

Built by [Eric Catalano](https://github.com/EricFinland).
