"""Typer CLI: `witness view`, `witness ls`, `witness rm`."""

from __future__ import annotations

import json as _json
import shutil
import sys
import threading
import time
import webbrowser
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import select

from witness import config, share as share_mod, storage

app = typer.Typer(
    help="Witness — local-first observability for browser agents.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def view(
    port: int = typer.Option(7842, help="Port to bind the viewer on."),
    host: str = typer.Option("127.0.0.1", help="Host to bind on."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open browser on start."),
) -> None:
    """Launch the Witness viewer."""
    import uvicorn

    storage.init_db()
    config.write_default_if_missing()
    url = f"http://{host}:{port}"
    console.print(f"[bold]Witness[/bold] viewer at [cyan]{url}[/cyan]")
    console.print(
        f"[dim]Data: {storage.BASE_DIR}   ·   Telemetry: "
        f"{'on' if config.load().telemetry else 'off'}[/dim]"
    )

    if open_browser:
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("witness.server:app", host=host, port=port, log_level="warning")


@app.command("ls")
def list_traces(limit: int = typer.Option(20, help="Max traces to show.")) -> None:
    """List recent traces."""
    storage.init_db()
    table = Table(title=None, show_header=True, header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Started")
    table.add_column("Status")
    table.add_column("Steps", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Model")
    table.add_column("Task")

    with storage.get_session() as s:
        rows = s.exec(
            select(storage.Trace).order_by(storage.Trace.started_at.desc()).limit(limit)
        ).all()
        if not rows:
            console.print("[dim]No traces yet. Run an instrumented agent first.[/dim]")
            return
        for r in rows:
            status_color = {"success": "green", "error": "red", "running": "yellow"}.get(
                r.status, "white"
            )
            table.add_row(
                r.id,
                r.started_at.strftime("%Y-%m-%d %H:%M"),
                f"[{status_color}]{r.status}[/{status_color}]",
                str(r.step_count),
                f"${r.total_cost_usd:.4f}",
                (r.model or "-")[:24],
                (r.task or "")[:60],
            )

    console.print(table)


@app.command()
def rm(
    trace_id: Optional[str] = typer.Argument(None, help="Trace id, or omit with --all."),
    all_: bool = typer.Option(False, "--all", help="Delete every trace."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
) -> None:
    """Delete a trace (or every trace with --all)."""
    storage.init_db()

    if all_:
        if not force and not typer.confirm("Delete ALL traces? This cannot be undone."):
            raise typer.Abort()
        _delete_all()
        console.print("[green]All traces deleted.[/green]")
        return

    if not trace_id:
        console.print("[red]Pass a trace id or use --all.[/red]")
        raise typer.Exit(1)

    if not _delete_one(trace_id):
        console.print(f"[red]Trace {trace_id!r} not found.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Deleted {trace_id}[/green]")


def _delete_one(trace_id: str) -> bool:
    with storage.get_session() as s:
        t = s.get(storage.Trace, trace_id)
        if t is None:
            return False
        step_ids = [
            r.id for r in s.exec(select(storage.Step).where(storage.Step.trace_id == trace_id)).all()
            if r.id is not None
        ]
        if step_ids:
            for call in s.exec(
                select(storage.LLMCall).where(storage.LLMCall.step_id.in_(step_ids))
            ).all():
                s.delete(call)
            for step in s.exec(
                select(storage.Step).where(storage.Step.trace_id == trace_id)
            ).all():
                s.delete(step)
        s.delete(t)
        s.commit()
    blob_dir = storage.TRACES_DIR / trace_id
    if blob_dir.exists():
        shutil.rmtree(blob_dir, ignore_errors=True)
    return True


def _delete_all() -> None:
    with storage.get_session() as s:
        for call in s.exec(select(storage.LLMCall)).all():
            s.delete(call)
        for step in s.exec(select(storage.Step)).all():
            s.delete(step)
        for t in s.exec(select(storage.Trace)).all():
            s.delete(t)
        s.commit()
    if storage.TRACES_DIR.exists():
        shutil.rmtree(storage.TRACES_DIR, ignore_errors=True)
        storage.TRACES_DIR.mkdir(parents=True, exist_ok=True)


@app.command()
def share(
    trace_id: str = typer.Argument(..., help="Local trace id (see `witness ls`)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the first-time confirmation."),
    endpoint: Optional[str] = typer.Option(
        None, "--endpoint", help="Override upload endpoint. Also WITNESS_UPLOAD_ENDPOINT env var."
    ),
) -> None:
    """Upload a trace to a hosted viewer and print a public URL."""
    code = share_mod.run(trace_id, yes=yes, endpoint=endpoint)
    if code != 0:
        raise typer.Exit(code)


@app.command()
def diff(
    trace_a: str = typer.Argument(..., help="Baseline trace id (see `witness ls`)."),
    trace_b: str = typer.Argument(..., help="Comparison trace id."),
    json_out: bool = typer.Option(False, "--json", help="Output diff as JSON for scripting."),
) -> None:
    """Compare two traces step-by-step and surface regressions."""
    from witness.diff import diff_traces

    storage.init_db()
    try:
        result = diff_traces(trace_a, trace_b)
    except LookupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if json_out:
        _render_diff_json(result)
    else:
        _render_diff_text(result)


def _render_diff_text(result) -> None:
    from rich.rule import Rule

    ta, tb = result.trace_a, result.trace_b

    # ── header ────────────────────────────────────────────────────────────────
    console.print()
    for label, t in [("A", ta), ("B", tb)]:
        status_color = {"success": "green", "error": "red", "running": "yellow"}.get(
            t.status, "white"
        )
        console.print(
            f"  [bold]{label}[/bold]  [cyan]{t.id}[/cyan]"
            f"  {t.started_at.strftime('%Y-%m-%d %H:%M')}"
            f"  [{status_color}]{t.status}[/{status_color}]"
            f"  [dim]{(t.task or '')[:72]}[/dim]"
        )
    console.print()

    # ── summary table ─────────────────────────────────────────────────────────
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Metric", style="dim")
    table.add_column("A", justify="right")
    table.add_column("B", justify="right")
    table.add_column("Δ", justify="right")

    def _delta(val: float | int, fmt_fn=None) -> str:
        if val == 0:
            return "[dim]—[/dim]"
        color = "green" if val < 0 else "red"
        prefix = "+" if val > 0 else ""
        s = fmt_fn(val) if fmt_fn else str(val)
        return f"[{color}]{prefix}{s}[/{color}]"

    table.add_row(
        "Steps",
        str(ta.step_count),
        str(tb.step_count),
        _delta(result.step_count_delta),
    )
    table.add_row(
        "Cost",
        f"${ta.total_cost_usd:.4f}",
        f"${tb.total_cost_usd:.4f}",
        _delta(result.cost_delta, fmt_fn=lambda x: f"${abs(x):.4f}"),
    )
    table.add_row(
        "Tokens",
        str(ta.total_tokens),
        str(tb.total_tokens),
        _delta(result.token_delta),
    )
    table.add_row(
        "Latency",
        f"{ta.total_latency_ms // 1000}s",
        f"{tb.total_latency_ms // 1000}s",
        _delta(result.latency_delta_ms // 1000 if result.latency_delta_ms else 0),
    )
    if ta.status != tb.status:
        sc_a = {"success": "green", "error": "red", "running": "yellow"}.get(ta.status, "white")
        sc_b = {"success": "green", "error": "red", "running": "yellow"}.get(tb.status, "white")
        table.add_row(
            "Status",
            f"[{sc_a}]{ta.status}[/{sc_a}]",
            f"[{sc_b}]{tb.status}[/{sc_b}]",
            "[yellow]changed[/yellow]",
        )

    console.print(table)
    console.print()

    # ── step sequence ─────────────────────────────────────────────────────────
    console.print("[bold]Step Sequence[/bold]")
    console.print(Rule(style="dim"))

    b_idx = 0
    for pair in result.pairs:
        if pair.kind == "equal":
            action = pair.step_a.action_type
            note = "  [dim][payload changed][/dim]" if pair.payload_changed else ""
            console.print(f"  [dim]{b_idx:>3}[/dim]  [dim]=[/dim]  {action}{note}")
            b_idx += 1
        elif pair.kind == "replace":
            console.print(
                f"  [dim]{b_idx:>3}[/dim]  [yellow]~[/yellow]"
                f"  [dim]{pair.step_a.action_type}[/dim] [dim]→[/dim] [yellow]{pair.step_b.action_type}[/yellow]"
            )
            b_idx += 1
        elif pair.kind == "delete":
            console.print(
                f"  [dim]   [/dim]  [red]-[/red]  [red]{pair.step_a.action_type}[/red]"
                f"  [dim](only in A)[/dim]"
            )
        else:  # insert
            console.print(
                f"  [dim]{b_idx:>3}[/dim]  [green]+[/green]  [green]{pair.step_b.action_type}[/green]"
                f"  [dim](only in B)[/dim]"
            )
            b_idx += 1

    console.print()


def _render_diff_json(result) -> None:
    def _trace_dict(t) -> dict:
        return {
            "id": t.id,
            "task": t.task,
            "status": t.status,
            "started_at": t.started_at.isoformat(),
            "step_count": t.step_count,
            "total_cost_usd": t.total_cost_usd,
            "total_tokens": t.total_tokens,
            "total_latency_ms": t.total_latency_ms,
        }

    def _pair_dict(p) -> dict:
        d: dict = {"kind": p.kind}
        if p.step_a is not None:
            d["action_type_a"] = p.step_a.action_type
            d["idx_a"] = p.step_a.idx
            d["cost_a"] = p.cost_a
        if p.step_b is not None:
            d["action_type_b"] = p.step_b.action_type
            d["idx_b"] = p.step_b.idx
            d["cost_b"] = p.cost_b
        if p.kind == "equal":
            d["payload_changed"] = p.payload_changed
        return d

    output = {
        "trace_a": _trace_dict(result.trace_a),
        "trace_b": _trace_dict(result.trace_b),
        "summary": {
            "step_count_delta": result.step_count_delta,
            "cost_delta": round(result.cost_delta, 6),
            "token_delta": result.token_delta,
            "latency_delta_ms": result.latency_delta_ms,
        },
        "pairs": [_pair_dict(p) for p in result.pairs],
    }
    console.print(_json.dumps(output, indent=2))


@app.command("config")
def config_cmd() -> None:
    """Show current config and its path."""
    storage.init_db()
    path = config.write_default_if_missing()
    cfg = config.load()
    console.print(f"[bold]config[/bold] {path}")
    console.print(f"  telemetry = [cyan]{cfg.telemetry}[/cyan]")
    console.print("[dim]Edit the file to change settings.[/dim]")


if __name__ == "__main__":
    app()
