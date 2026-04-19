"""Typer CLI: `witness view`, `witness ls`, `witness rm`."""

from __future__ import annotations

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
