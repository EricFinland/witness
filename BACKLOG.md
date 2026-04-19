# Backlog

Features intentionally not built for v0. In rough priority order.

## Share links

`witness share <trace_id>` → uploads trace to hosted viewer, returns a public URL.
Closed-source hosted service, free tier with sensible limits.

## More frameworks

- Playwright agents (via Agent class detection)
- Claude in Chrome event stream subscription
- Stagehand, Skyvern, Manus (as users request)

## Regression detection

`witness diff trace_a trace_b` — compare two runs of the same task.
Eventually: scheduled runs with alerts on drift.

## Cost dashboards

Aggregate views: spend per day, per model, per task pattern.

## Real-time streaming viewer

Watch a running agent step-by-step as it executes.

## OpenAI / Bedrock / Gemini full support

OpenLLMetry already instruments these; we just need pricing entries and testing.

## Team features

Workspaces, shared dashboards, RBAC — cloud only, paid.
