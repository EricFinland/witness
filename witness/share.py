"""`witness share <trace_id>` — upload a trace to a hosted viewer.

Flow:
  1. Load the trace + steps + LLM calls + blobs from local storage.
  2. Redact DOMs / prompts / responses / URLs.
  3. Prompt for consent on first use (recorded in config.toml so it's one-time).
  4. POST metadata + base64-encoded blobs to `{share_endpoint}/upload`.
  5. Print the public URL + deletion token.

The server is whatever is running at `share_endpoint` — default
`https://api.usewitness.dev`, overridable via `WITNESS_UPLOAD_ENDPOINT`
env var or `share_endpoint` in config.toml. Self-hosters point it at
their own instance.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from sqlmodel import select

from witness import config, redact, storage

console = Console()

WARNING_TEXT = (
    "[bold yellow]This uploads the DOM, screenshots, and LLM prompts from this "
    "trace to {endpoint}.\nAnyone with the resulting link can view them. "
    "Continue?[/bold yellow] [dim](y/N)[/dim]"
)


# Rough bound: Supabase free-tier transfers + Vercel Fn body limit.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


@dataclass
class _TracePackage:
    trace: dict
    steps: list[dict]
    llm_calls: list[dict]
    blobs: dict[str, bytes]  # rel_path → bytes


def run(trace_id: str, *, yes: bool = False, endpoint: Optional[str] = None) -> int:
    """CLI entry. Returns exit code."""
    storage.init_db()

    cfg = config.load()
    endpoint = endpoint or os.environ.get("WITNESS_UPLOAD_ENDPOINT") or cfg.share_endpoint

    pkg = _load(trace_id)
    if pkg is None:
        console.print(f"[red]Trace {trace_id!r} not found.[/red]")
        return 1

    size = _package_size(pkg)
    if size > MAX_UPLOAD_BYTES:
        console.print(
            f"[red]Trace is {size / 1024 / 1024:.1f} MB, over the "
            f"{MAX_UPLOAD_BYTES / 1024 / 1024:.0f} MB upload limit.[/red]"
        )
        return 2

    if not cfg.share_consent and not yes:
        console.print(WARNING_TEXT.format(endpoint=endpoint))
        ans = input().strip().lower()
        if ans not in ("y", "yes"):
            console.print("[dim]aborted.[/dim]")
            return 3
        config.record_share_consent()

    console.print(f"[dim]redacting…[/dim]")
    _redact_in_place(pkg)

    console.print(f"[dim]uploading {size / 1024:.0f} KB to {endpoint} …[/dim]")
    try:
        result = _upload(endpoint, pkg)
    except httpx.HTTPError as e:
        console.print(f"[red]Upload failed:[/red] {e}")
        return 4

    console.print()
    console.print(f"[bold green]  {result['url']}[/bold green]")
    console.print(f"[dim]  deletion token: {result['deletion_token']}[/dim]")
    console.print(
        f"[dim]  expires:        "
        f"{result.get('expires_at', 'in 30 days')}[/dim]"
    )
    console.print()
    console.print(
        "[dim]Save the deletion token somewhere safe. "
        "Run `witness unshare <deletion_token>` to remove before expiry.[/dim]"
    )

    _remember_share(trace_id, result)
    return 0


# --- helpers ----------------------------------------------------------------


def _load(trace_id: str) -> Optional[_TracePackage]:
    with storage.get_session() as s:
        t = s.get(storage.Trace, trace_id)
        if t is None:
            return None

        steps_rows = s.exec(
            select(storage.Step)
            .where(storage.Step.trace_id == trace_id)
            .order_by(storage.Step.idx)
        ).all()
        step_ids = [st.id for st in steps_rows if st.id is not None]
        calls_rows = []
        if step_ids:
            calls_rows = s.exec(
                select(storage.LLMCall).where(storage.LLMCall.step_id.in_(step_ids))
            ).all()

        trace = {
            "id": t.id,
            "task": t.task,
            "model": t.model,
            "status": t.status,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "ended_at": t.ended_at.isoformat() if t.ended_at else None,
            "total_cost_usd": float(t.total_cost_usd),
            "total_tokens": t.total_tokens,
            "total_latency_ms": t.total_latency_ms,
            "step_count": t.step_count,
            "error": t.error,
        }
        steps = [_step_to_dict(st) for st in steps_rows]
        calls = [_call_to_dict(c) for c in calls_rows]

    blobs = _load_blobs(trace_id, steps)
    return _TracePackage(trace=trace, steps=steps, llm_calls=calls, blobs=blobs)


def _step_to_dict(st: storage.Step) -> dict:
    return {
        "id": st.id,
        "idx": st.idx,
        "action_type": st.action_type,
        "action_payload": st.action_payload or {},
        "ts": st.ts.isoformat() if st.ts else None,
        "latency_ms": st.latency_ms,
        "error": st.error,
        "url": st.url,
        "dom_before_path": st.dom_before_path,
        "dom_after_path": st.dom_after_path,
        "shot_before_path": st.shot_before_path,
        "shot_after_path": st.shot_after_path,
    }


def _call_to_dict(c: storage.LLMCall) -> dict:
    return {
        "id": c.id,
        "step_id": c.step_id,
        "model": c.model,
        "prompt_tokens": c.prompt_tokens,
        "completion_tokens": c.completion_tokens,
        "cost_usd": float(c.cost_usd),
        "latency_ms": c.latency_ms,
        "prompt": c.prompt,
        "response": c.response,
        "ts": c.ts.isoformat() if c.ts else None,
    }


def _load_blobs(trace_id: str, steps: list[dict]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    tdir = storage.TRACES_DIR / trace_id
    for st in steps:
        for key in ("dom_before_path", "dom_after_path", "shot_before_path", "shot_after_path"):
            rel = st.get(key)
            if not rel:
                continue
            full = tdir / rel
            if full.is_file():
                out[rel] = full.read_bytes()
    return out


def _package_size(pkg: _TracePackage) -> int:
    return sum(len(b) for b in pkg.blobs.values()) + 4096  # rough JSON overhead


def _redact_in_place(pkg: _TracePackage) -> None:
    pkg.trace["task"] = redact.redact_text(pkg.trace["task"]) or ""
    pkg.trace["error"] = redact.redact_text(pkg.trace.get("error"))

    for st in pkg.steps:
        st["url"] = redact.sanitize_url(st.get("url"))
        st["error"] = redact.redact_text(st.get("error"))
        # action_payload can contain URLs, typed text, etc.
        st["action_payload"] = _redact_json(st.get("action_payload") or {})

    for c in pkg.llm_calls:
        c["prompt"] = redact.redact_text(c.get("prompt")) or ""
        c["response"] = redact.redact_text(c.get("response")) or ""

    # Redact DOM blobs (keep screenshots verbatim — they're pixels, can't grep).
    redacted_blobs: dict[str, bytes] = {}
    for rel, b in pkg.blobs.items():
        if rel.endswith(".html"):
            try:
                text = b.decode("utf-8", errors="replace")
                text = redact.redact_text(text) or text
                redacted_blobs[rel] = text.encode("utf-8")
            except Exception:
                redacted_blobs[rel] = b
        else:
            redacted_blobs[rel] = b
    pkg.blobs = redacted_blobs


def _redact_json(obj):
    """Walk a JSON-ish structure, redacting strings and sanitizing URLs."""
    if isinstance(obj, dict):
        return {k: _redact_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_json(x) for x in obj]
    if isinstance(obj, str):
        if obj.startswith("http://") or obj.startswith("https://"):
            return redact.sanitize_url(obj)
        return redact.redact_text(obj)
    return obj


def _upload(endpoint: str, pkg: _TracePackage) -> dict:
    body = {
        "trace": pkg.trace,
        "steps": pkg.steps,
        "llm_calls": pkg.llm_calls,
        "blobs": {rel: base64.b64encode(data).decode("ascii") for rel, data in pkg.blobs.items()},
    }
    url = endpoint.rstrip("/") + "/upload"
    with httpx.Client(timeout=60.0) as c:
        r = c.post(url, json=body, headers={"User-Agent": f"witness-cli/0.0.1"})
        r.raise_for_status()
        return r.json()


def _remember_share(local_id: str, result: dict) -> None:
    """Append a line to ~/.witness/shares.jsonl so the user can find the token later."""
    log = storage.BASE_DIR / "shares.jsonl"
    storage._ensure_dirs()
    entry = {
        "local_trace_id": local_id,
        "remote_id": result.get("id"),
        "deletion_token": result.get("deletion_token"),
        "url": result.get("url"),
        "expires_at": result.get("expires_at"),
    }
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
