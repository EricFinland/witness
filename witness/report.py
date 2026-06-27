"""Self-contained HTML report + markdown summary for a single trace.

Pure stdlib string templating (no jinja). Reuses the cost/step grouping
patterns from :mod:`witness.diff` and :mod:`witness.stats` but stays
dependency-light so it can run anywhere the package is installed.

Public surface:

* :func:`write_report` -- render a single-file HTML report to disk.
* :func:`write_summary_markdown` -- a compact markdown trace summary suitable
  for a PR comment (used by the GitHub Action).
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from sqlmodel import select

from witness import storage

log = logging.getLogger("witness")

# Severity ordering and badge colors. Higher is worse.
_SEVERITY_ORDER = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
_SEVERITY_COLOR = {
    "info": "#6b7280",
    "low": "#2563eb",
    "medium": "#d97706",
    "high": "#dc2626",
    "critical": "#7f1d1d",
}

# Human labels for finding kinds.
_KIND_LABEL = {
    "prompt_injection": "Prompt injection",
    "trajectory": "Trajectory",
    "exfiltration": "Exfiltration",
    "outcome": "Outcome",
}


# ── data loading ──────────────────────────────────────────────────────────────


class _TraceBundle:
    """Everything needed to render a report, loaded in one session."""

    def __init__(
        self,
        trace: storage.Trace,
        steps: list[storage.Step],
        calls_by_step: dict[int, list[storage.LLMCall]],
        findings: list[storage.Finding],
    ) -> None:
        self.trace = trace
        self.steps = steps
        self.calls_by_step = calls_by_step
        self.findings = findings


def _load_bundle(trace_id: str) -> _TraceBundle:
    """Load a trace, its steps, LLM calls, and findings.

    Raises LookupError if the trace is not found.
    """
    with storage.get_session() as s:
        trace = s.get(storage.Trace, trace_id)
        if trace is None:
            raise LookupError(f"Trace {trace_id!r} not found")

        steps = list(
            s.exec(
                select(storage.Step)
                .where(storage.Step.trace_id == trace_id)
                .order_by(storage.Step.idx)
            ).all()
        )

        calls_by_step: dict[int, list[storage.LLMCall]] = {}
        step_ids = [st.id for st in steps if st.id is not None]
        if step_ids:
            calls = s.exec(
                select(storage.LLMCall).where(storage.LLMCall.step_id.in_(step_ids))
            ).all()
            for c in calls:
                calls_by_step.setdefault(c.step_id, []).append(c)
            for lst in calls_by_step.values():
                lst.sort(key=lambda c: (c.ts, c.id or 0))

        findings = list(
            s.exec(
                select(storage.Finding).where(storage.Finding.trace_id == trace_id)
            ).all()
        )

    # Sort findings worst-first so the most important shows on top.
    findings.sort(
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 0), f.score),
        reverse=True,
    )
    return _TraceBundle(trace, steps, calls_by_step, findings)


# ── small formatting helpers ──────────────────────────────────────────────────


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _fmt_cost(value: Optional[float]) -> str:
    return f"${(value or 0.0):.4f}"


def _fmt_int(value: Optional[int]) -> str:
    return f"{(value or 0):,}"


def _fmt_dt(value: Optional[datetime]) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _duration_str(trace: storage.Trace) -> str:
    if trace.started_at is None or trace.ended_at is None:
        return "-"
    start = trace.started_at
    end = trace.ended_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    secs = (end - start).total_seconds()
    if secs < 0:
        return "-"
    return f"{secs:.1f}s"


def _trajectory_health(findings: list[storage.Finding]) -> Optional[str]:
    """Return a short health label if a trajectory finding exists."""
    for f in findings:
        if f.kind == "trajectory":
            sev = f.severity or "info"
            return f"{sev} ({f.score:.2f})"
    return None


# ── HTML rendering ────────────────────────────────────────────────────────────

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  margin: 0; padding: 2rem; line-height: 1.5;
  color: #111827; background: #f9fafb;
}
h1 { font-size: 1.5rem; margin: 0 0 0.25rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 0.75rem; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.35rem; }
.subtle { color: #6b7280; font-size: 0.9rem; }
.cards { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 1rem 0; }
.card {
  background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
  padding: 0.75rem 1rem; min-width: 8rem;
}
.card .label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: #6b7280; }
.card .value { font-size: 1.2rem; font-weight: 600; margin-top: 0.15rem; }
table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #f1f5f9; vertical-align: top; font-size: 0.9rem; }
th { background: #f3f4f6; font-weight: 600; }
tr:last-child td { border-bottom: none; }
.badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px; color: #fff; font-size: 0.75rem; font-weight: 600; }
.status-success { color: #166534; }
.status-error { color: #991b1b; }
.status-running { color: #92400e; }
.finding { background: #fff; border: 1px solid #e5e7eb; border-left-width: 4px; border-radius: 8px; padding: 0.85rem 1rem; margin-bottom: 0.75rem; }
.finding .title { font-weight: 600; }
.finding .meta { font-size: 0.8rem; color: #6b7280; margin: 0.2rem 0 0.4rem; }
.finding .detail { white-space: pre-wrap; font-size: 0.9rem; }
.finding pre { background: #f3f4f6; padding: 0.5rem; border-radius: 6px; overflow-x: auto; font-size: 0.8rem; margin: 0.5rem 0 0; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.empty { color: #6b7280; font-style: italic; }
.payload { max-width: 32rem; overflow-x: auto; }
.payload pre { margin: 0; font-size: 0.8rem; }
"""


def _render_header(b: _TraceBundle) -> str:
    t = b.trace
    status_cls = f"status-{_esc(t.status)}"
    health = _trajectory_health(b.findings)
    cards = [
        ("Model", _esc(t.model or "unknown")),
        ("Status", f'<span class="{status_cls}">{_esc(t.status)}</span>'),
        ("Cost", _fmt_cost(t.total_cost_usd)),
        ("Tokens", _fmt_int(t.total_tokens)),
        ("Steps", _fmt_int(t.step_count)),
        ("Duration", _esc(_duration_str(t))),
    ]
    if health is not None:
        cards.append(("Trajectory health", _esc(health)))
    card_html = "\n".join(
        f'<div class="card"><div class="label">{label}</div>'
        f'<div class="value">{value}</div></div>'
        for label, value in cards
    )
    return (
        f"<h1>{_esc(t.task)}</h1>\n"
        f'<div class="subtle">trace <span class="mono">{_esc(t.id)}</span> '
        f"started {_esc(_fmt_dt(t.started_at))}</div>\n"
        f'<div class="cards">{card_html}</div>'
    )


def _render_findings(b: _TraceBundle) -> str:
    out = ["<h2>Findings</h2>"]
    if not b.findings:
        out.append('<p class="empty">No findings.</p>')
        return "\n".join(out)
    for f in b.findings:
        sev = f.severity or "info"
        color = _SEVERITY_COLOR.get(sev, "#6b7280")
        kind_label = _KIND_LABEL.get(f.kind, f.kind)
        step_note = f" - step {f.step_id}" if f.step_id is not None else " - trace-level"
        evidence_html = ""
        if f.evidence:
            import json

            evidence_html = (
                "<pre>" + _esc(json.dumps(f.evidence, indent=2, default=str)) + "</pre>"
            )
        out.append(
            f'<div class="finding" style="border-left-color: {color}">'
            f'<div class="title">'
            f'<span class="badge" style="background: {color}">{_esc(sev)}</span> '
            f"{_esc(f.title or kind_label)}</div>"
            f'<div class="meta">{_esc(kind_label)}{_esc(step_note)} '
            f"&middot; score {f.score:.2f}</div>"
            f'<div class="detail">{_esc(f.detail)}</div>'
            f"{evidence_html}</div>"
        )
    return "\n".join(out)


def _render_timeline(b: _TraceBundle) -> str:
    import json

    out = ["<h2>Step timeline</h2>"]
    if not b.steps:
        out.append('<p class="empty">No steps recorded.</p>')
        return "\n".join(out)
    rows = [
        "<table><thead><tr>"
        "<th>#</th><th>Action</th><th>URL</th><th>Latency</th>"
        "<th>Payload</th><th>Error</th>"
        "</tr></thead><tbody>"
    ]
    for st in b.steps:
        payload_str = ""
        if st.action_payload:
            payload_str = (
                '<div class="payload"><pre>'
                + _esc(json.dumps(st.action_payload, indent=2, default=str))
                + "</pre></div>"
            )
        err = _esc(st.error) if st.error else ""
        rows.append(
            "<tr>"
            f"<td>{_esc(st.idx)}</td>"
            f'<td class="mono">{_esc(st.action_type)}</td>'
            f'<td class="mono">{_esc(st.url or "")}</td>'
            f"<td>{_fmt_int(st.latency_ms)} ms</td>"
            f"<td>{payload_str}</td>"
            f'<td class="status-error">{err}</td>'
            "</tr>"
        )
    rows.append("</tbody></table>")
    out.append("\n".join(rows))
    return "\n".join(out)


def _render_llm_calls(b: _TraceBundle) -> str:
    out = ["<h2>LLM calls</h2>"]
    total_calls = sum(len(v) for v in b.calls_by_step.values())
    if total_calls == 0:
        out.append('<p class="empty">No LLM calls recorded.</p>')
        return "\n".join(out)
    rows = [
        "<table><thead><tr>"
        "<th>Step</th><th>Model</th><th>Prompt tok</th><th>Completion tok</th>"
        "<th>Cost</th><th>Latency</th>"
        "</tr></thead><tbody>"
    ]
    for st in b.steps:
        if st.id is None:
            continue
        for c in b.calls_by_step.get(st.id, []):
            rows.append(
                "<tr>"
                f"<td>{_esc(st.idx)}</td>"
                f'<td class="mono">{_esc(c.model)}</td>'
                f"<td>{_fmt_int(c.prompt_tokens)}</td>"
                f"<td>{_fmt_int(c.completion_tokens)}</td>"
                f"<td>{_fmt_cost(c.cost_usd)}</td>"
                f"<td>{_fmt_int(c.latency_ms)} ms</td>"
                "</tr>"
            )
    rows.append("</tbody></table>")
    out.append("\n".join(rows))
    return "\n".join(out)


def render_html(trace_id: str) -> str:
    """Render the full self-contained HTML report for a trace as a string."""
    b = _load_bundle(trace_id)
    generated = _fmt_dt(datetime.now(timezone.utc))
    body = "\n".join(
        [
            _render_header(b),
            _render_findings(b),
            _render_timeline(b),
            _render_llm_calls(b),
            f'<p class="subtle" style="margin-top:2rem">Generated {_esc(generated)} '
            "by witness report.</p>",
        ]
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>witness report {_esc(b.trace.id)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


def _default_out_path(trace_id: str) -> Path:
    return storage.trace_dir(trace_id) / "report.html"


def write_report(
    trace_id: str,
    out_path: Union[str, Path, None] = None,
    *,
    out: Union[str, Path, None] = None,
) -> Path:
    """Render the HTML report for ``trace_id`` and write it to disk.

    Returns the path written. ``out`` is accepted as an alias for ``out_path``
    so the CLI can pass either. When neither is given the report lands at
    ``<trace_dir>/report.html``.
    """
    target = out_path if out_path is not None else out
    if target is None:
        dest = _default_out_path(trace_id)
    else:
        dest = Path(target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = render_html(trace_id)
    dest.write_text(content, encoding="utf-8")
    log.info("wrote report for %s to %s", trace_id, dest)
    return dest


# ── markdown summary (for PR comments) ────────────────────────────────────────


def write_summary_markdown(trace_id: str) -> str:
    """Return a compact markdown summary of a trace, for a PR comment."""
    b = _load_bundle(trace_id)
    t = b.trace
    status_icon = {"success": "PASS", "error": "FAIL", "running": "RUNNING"}.get(
        t.status, t.status
    )

    lines = [
        f"### witness trace `{t.id}`",
        "",
        f"**Task:** {t.task}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Status | {status_icon} |",
        f"| Model | {t.model or 'unknown'} |",
        f"| Cost | {_fmt_cost(t.total_cost_usd)} |",
        f"| Tokens | {_fmt_int(t.total_tokens)} |",
        f"| Steps | {_fmt_int(t.step_count)} |",
        f"| Duration | {_duration_str(t)} |",
    ]
    health = _trajectory_health(b.findings)
    if health is not None:
        lines.append(f"| Trajectory health | {health} |")
    lines.append("")

    if b.findings:
        lines.append("**Findings:**")
        lines.append("")
        for f in b.findings:
            kind_label = _KIND_LABEL.get(f.kind, f.kind)
            title = f.title or kind_label
            lines.append(
                f"- **{f.severity}** ({kind_label}, score {f.score:.2f}): {title}"
            )
    else:
        lines.append("_No findings._")
    lines.append("")
    return "\n".join(lines)
