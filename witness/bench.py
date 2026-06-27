"""Reliability / eval harness.

Runs a task N times (optionally across model variants) and reports reliability
metrics: success rate, step-count distribution, cost distribution, latency
distribution, variance/flakiness, plus a trajectory-divergence diff showing where
two runs of the same task diverged.

The heavy lifting is split so it is testable without launching a browser:

* ``summarize(traces)`` is a pure function over already-captured Trace rows.
* ``run_bench(make_agent, task, n, models)`` launches agents, instruments them,
  captures real traces, then delegates to ``summarize``.

``run_bench`` also accepts the CLI calling convention
(``run_bench(suite=..., json_out=...)``) and in that mode returns an int exit
code instead of a BenchReport, so ``witness bench`` works without edits to cli.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from witness import diff as diff_mod
from witness import storage

# Trajectory analysis is built in parallel; import it defensively.
try:  # pragma: no cover - exercised indirectly
    from witness.analysis import trajectory as _trajectory_mod
except Exception:  # noqa: BLE001
    _trajectory_mod = None  # type: ignore[assignment]

log = logging.getLogger("witness")


# --- data model --------------------------------------------------------------


@dataclass
class RunRow:
    """One run of the task (one captured trace)."""

    trace_id: str
    model: Optional[str]
    status: str  # "success" | "error" | "running"
    success: bool
    step_count: int
    cost_usd: float
    tokens: int
    latency_ms: int
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Distribution:
    """Summary statistics for a numeric metric across runs."""

    count: int = 0
    min: float = 0.0
    max: float = 0.0
    mean: float = 0.0
    median: float = 0.0
    stdev: float = 0.0  # population stdev (0 for a single value)
    cv: float = 0.0  # coefficient of variation = stdev / mean (flakiness proxy)
    values: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _distribution(values: Sequence[float]) -> Distribution:
    vals = [float(v) for v in values]
    if not vals:
        return Distribution()
    n = len(vals)
    mean = statistics.fmean(vals)
    median = statistics.median(vals)
    # Population stdev; 0 when only one sample.
    stdev = statistics.pstdev(vals) if n > 1 else 0.0
    cv = (stdev / mean) if mean else 0.0
    return Distribution(
        count=n,
        min=min(vals),
        max=max(vals),
        mean=mean,
        median=median,
        stdev=stdev,
        cv=cv,
        values=vals,
    )


@dataclass
class Divergence:
    """Where two runs of the same task first diverged (step-sequence diff)."""

    trace_a: str
    trace_b: str
    first_divergence_idx: Optional[int]  # None means the two runs are identical
    matched_steps: int  # leading steps that were equal
    total_pairs: int
    diverged: bool
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BenchReport:
    """Aggregate reliability report for a benchmarked task."""

    task: str
    n: int
    models: list[str] = field(default_factory=list)
    runs: list[RunRow] = field(default_factory=list)

    success_count: int = 0
    error_count: int = 0
    success_rate: float = 0.0

    steps: Distribution = field(default_factory=Distribution)
    cost: Distribution = field(default_factory=Distribution)
    latency: Distribution = field(default_factory=Distribution)
    tokens: Distribution = field(default_factory=Distribution)

    # Per-model success rate, keyed by model name (or "" / None coalesced).
    success_rate_by_model: dict[str, float] = field(default_factory=dict)

    # Flakiness: a task is flaky when it neither always passes nor always fails.
    flaky: bool = False
    flakiness: float = 0.0  # 0 = deterministic, 0.5 when exactly half of runs pass

    divergences: list[Divergence] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "n": self.n,
            "models": list(self.models),
            "runs": [r.to_dict() for r in self.runs],
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate": self.success_rate,
            "steps": self.steps.to_dict(),
            "cost": self.cost.to_dict(),
            "latency": self.latency.to_dict(),
            "tokens": self.tokens.to_dict(),
            "success_rate_by_model": dict(self.success_rate_by_model),
            "flaky": self.flaky,
            "flakiness": self.flakiness,
            "divergences": [d.to_dict() for d in self.divergences],
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Bench report: {self.task}")
        lines.append("")
        lines.append(f"- Runs: {self.n}")
        if self.models:
            lines.append(f"- Models: {', '.join(self.models)}")
        lines.append(
            f"- Success rate: {self.success_rate:.0%} "
            f"({self.success_count}/{self.n})"
        )
        lines.append(f"- Flaky: {'yes' if self.flaky else 'no'} "
                     f"(flakiness {self.flakiness:.2f})")
        lines.append("")
        lines.append("## Distributions")
        lines.append("")
        lines.append("| metric | min | mean | median | max | stdev | cv |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for name, d in (
            ("steps", self.steps),
            ("cost_usd", self.cost),
            ("latency_ms", self.latency),
            ("tokens", self.tokens),
        ):
            lines.append(
                f"| {name} | {d.min:g} | {d.mean:g} | {d.median:g} | "
                f"{d.max:g} | {d.stdev:g} | {d.cv:.2f} |"
            )
        if self.success_rate_by_model:
            lines.append("")
            lines.append("## Success rate by model")
            lines.append("")
            lines.append("| model | success rate |")
            lines.append("| --- | --- |")
            for model, rate in self.success_rate_by_model.items():
                lines.append(f"| {model or '(unknown)'} | {rate:.0%} |")
        lines.append("")
        lines.append("## Runs")
        lines.append("")
        lines.append("| trace | model | status | steps | cost_usd | latency_ms |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in self.runs:
            lines.append(
                f"| {r.trace_id} | {r.model or '(unknown)'} | {r.status} | "
                f"{r.step_count} | {r.cost_usd:g} | {r.latency_ms} |"
            )
        if self.divergences:
            lines.append("")
            lines.append("## Trajectory divergences")
            lines.append("")
            for d in self.divergences:
                lines.append(f"- {d.summary}")
        return "\n".join(lines) + "\n"


# --- core (pure) -------------------------------------------------------------


def _is_success(trace: storage.Trace) -> bool:
    return str(getattr(trace, "status", "")) == "success"


def summarize(traces: Sequence[storage.Trace], *, task: str | None = None) -> BenchReport:
    """Compute a BenchReport from already-captured Trace rows.

    This is a pure function: it does not touch the database or launch browsers.
    Trajectory divergences are computed between consecutive runs that share a
    model (falling back to all-pairs-by-order) using ``witness.diff``.
    """
    rows: list[RunRow] = []
    models_seen: list[str] = []
    for t in traces:
        model = getattr(t, "model", None)
        if model is not None and model not in models_seen:
            models_seen.append(model)
        rows.append(
            RunRow(
                trace_id=str(getattr(t, "id", "")),
                model=model,
                status=str(getattr(t, "status", "")),
                success=_is_success(t),
                step_count=int(getattr(t, "step_count", 0) or 0),
                cost_usd=float(getattr(t, "total_cost_usd", 0.0) or 0.0),
                tokens=int(getattr(t, "total_tokens", 0) or 0),
                latency_ms=int(getattr(t, "total_latency_ms", 0) or 0),
                error=getattr(t, "error", None),
            )
        )

    n = len(rows)
    success_count = sum(1 for r in rows if r.success)
    error_count = sum(1 for r in rows if r.status == "error")
    success_rate = (success_count / n) if n else 0.0

    steps = _distribution([r.step_count for r in rows])
    cost = _distribution([r.cost_usd for r in rows])
    latency = _distribution([r.latency_ms for r in rows])
    tokens = _distribution([r.tokens for r in rows])

    # Per-model success rate.
    by_model: dict[str, list[bool]] = {}
    for r in rows:
        by_model.setdefault(r.model or "", []).append(r.success)
    success_rate_by_model = {
        m: (sum(1 for s in vals if s) / len(vals)) if vals else 0.0
        for m, vals in by_model.items()
    }

    # Flakiness: Bernoulli variance p*(1-p) scaled to peak at 0.5 when exactly
    # half the runs pass, and 0 when the task is deterministic (all pass / all fail).
    p = success_rate
    flakiness = (2.0 * p * (1.0 - p)) if n > 1 else 0.0
    flaky = n > 1 and 0 < success_count < n

    report = BenchReport(
        task=task if task is not None else _common_task(traces),
        n=n,
        models=models_seen,
        runs=rows,
        success_count=success_count,
        error_count=error_count,
        success_rate=success_rate,
        steps=steps,
        cost=cost,
        latency=latency,
        tokens=tokens,
        success_rate_by_model=success_rate_by_model,
        flaky=flaky,
        flakiness=flakiness,
        divergences=_compute_divergences(traces),
    )
    return report


def _common_task(traces: Sequence[storage.Trace]) -> str:
    for t in traces:
        task = getattr(t, "task", None)
        if task:
            return str(task)
    return ""


def _compute_divergences(traces: Sequence[storage.Trace]) -> list[Divergence]:
    """Diff consecutive runs to find where trajectories diverged.

    Uses ``witness.diff`` directly on in-memory Step sequences so it works even
    when the rows were never persisted (tests build them in a session, the diff
    here does not require a second DB round-trip beyond what diff_traces needs).
    """
    ids = [str(getattr(t, "id", "")) for t in traces if getattr(t, "id", None)]
    out: list[Divergence] = []
    for a_id, b_id in zip(ids, ids[1:]):
        try:
            result = diff_mod.diff_traces(a_id, b_id)
        except Exception as e:  # noqa: BLE001
            log.debug("witness.bench: diff_traces(%s,%s) failed: %r", a_id, b_id, e)
            continue
        out.append(_divergence_from_diff(a_id, b_id, result))
    return out


def _divergence_from_diff(a_id: str, b_id: str, result: "diff_mod.DiffResult") -> Divergence:
    matched = 0
    first_div: Optional[int] = None
    diverged = False
    for i, pair in enumerate(result.pairs):
        is_match = pair.kind == "equal" and not pair.payload_changed
        if is_match:
            if not diverged:
                matched += 1
            continue
        diverged = True
        if first_div is None:
            first_div = i
    total = len(result.pairs)
    if not diverged:
        summary = f"{a_id} vs {b_id}: identical trajectory ({total} steps)"
    else:
        a_kind = b_kind = "(none)"
        if first_div is not None and first_div < total:
            p = result.pairs[first_div]
            a_kind = p.step_a.action_type if p.step_a is not None else "(none)"
            b_kind = p.step_b.action_type if p.step_b is not None else "(none)"
        summary = (
            f"{a_id} vs {b_id}: diverged at step {first_div} "
            f"(A={a_kind}, B={b_kind}); {matched} leading steps matched"
        )
    return Divergence(
        trace_a=a_id,
        trace_b=b_id,
        first_divergence_idx=first_div,
        matched_steps=matched,
        total_pairs=total,
        diverged=diverged,
        summary=summary,
    )


# --- WitnessBench reference tasks --------------------------------------------


_BUILTIN_REFERENCE_TASKS = [
    {"name": "hn_top_story", "task": "Find the top story on Hacker News and report its title."},
    {"name": "form_fill", "task": "Fill out the contact form with sample data and submit it."},
    {"name": "multi_tab", "task": "Open two tabs and compare information across them."},
]


def _bench_examples_dir() -> Path:
    # witness/bench.py -> repo root is parents[1]; tasks live at examples/bench.
    return Path(__file__).resolve().parents[1] / "examples" / "bench"


def load_reference_tasks() -> list[dict[str, Any]]:
    """Return the WitnessBench reference task specs.

    Reads task files from ``examples/bench`` if that directory exists (one entry
    per ``.py``/``.txt``/``.json`` file), otherwise returns a small built-in list.
    Each entry has at least ``name`` and a ``path`` (when file-backed) or
    ``task`` (for built-ins).
    """
    d = _bench_examples_dir()
    if d.is_dir():
        tasks: list[dict[str, Any]] = []
        for p in sorted(d.iterdir()):
            if p.name.startswith("_") or p.name.startswith("."):
                continue
            if p.suffix.lower() not in {".py", ".txt", ".json", ".yaml", ".yml"}:
                continue
            entry: dict[str, Any] = {"name": p.stem, "path": str(p)}
            if p.suffix.lower() == ".json":
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("task"):
                        entry["task"] = str(data["task"])
                except Exception:  # noqa: BLE001
                    pass
            tasks.append(entry)
        if tasks:
            return tasks
    return [dict(t) for t in _BUILTIN_REFERENCE_TASKS]


# --- run (impure) ------------------------------------------------------------


def _load_traces_by_id(trace_ids: Sequence[str]) -> list[storage.Trace]:
    out: list[storage.Trace] = []
    with storage.get_session() as s:
        for tid in trace_ids:
            t = s.get(storage.Trace, tid)
            if t is not None:
                out.append(t)
    return out


async def _run_one(make_agent: Callable[[], Any], task: str) -> Optional[str]:
    """Launch one instrumented agent run and return its trace id (or None)."""
    from witness.sdk import instrument

    agent = make_agent()
    instrument(agent)
    trace_id = getattr(agent, "_witness_trace_id", None)
    try:
        run = getattr(agent, "run", None)
        if run is not None:
            result = run()
            if asyncio.iscoroutine(result):
                await result
    except Exception as e:  # noqa: BLE001
        # The wrapped run() already finalized the trace status to "error".
        log.debug("witness.bench: run raised %r", e)
    return trace_id


def _run_bench_full(
    make_agent: Callable[[], Any],
    task: str,
    n: int,
    models: Optional[list[str]] = None,
) -> BenchReport:
    """Launch agents and produce a BenchReport. Requires a browser-capable agent."""
    trace_ids: list[str] = []

    async def _drive() -> None:
        variants = models or [None]  # type: ignore[list-item]
        for _model in variants:
            for _ in range(n):
                tid = await _run_one(make_agent, task)
                if tid:
                    trace_ids.append(tid)

    asyncio.run(_drive())
    traces = _load_traces_by_id(trace_ids)
    report = summarize(traces, task=task)
    report.n = len(traces)
    if models:
        report.models = list(models)
    return report


def _run_bench_cli(suite: Optional[str], json_out: bool) -> int:
    """CLI entry point. Summarizes already-captured traces for `suite` (a task
    substring filter) and prints a report. Returns a process exit code."""
    from sqlmodel import select

    with storage.get_session() as s:
        stmt = select(storage.Trace)
        traces = list(s.exec(stmt).all())

    if suite:
        needle = suite.lower()
        traces = [t for t in traces if needle in (t.task or "").lower()]

    if not traces:
        if json_out:
            print(json.dumps({"error": "no traces", "suite": suite}))
        else:
            print("No matching traces to benchmark.")
        return 1

    report = summarize(traces, task=suite or _common_task(traces))
    if json_out:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(report.to_markdown())
    return 0


def run_bench(*args: Any, **kwargs: Any):
    """Run a reliability benchmark.

    Two calling conventions are supported:

    * Programmatic (returns a ``BenchReport``)::

          run_bench(make_agent, task, n, models=None)

    * CLI (returns an int exit code), used by ``witness bench``::

          run_bench(suite=..., json_out=...)
    """
    # CLI convention: suite/json_out keywords (and no make_agent).
    if "suite" in kwargs or "json_out" in kwargs:
        return _run_bench_cli(kwargs.get("suite"), bool(kwargs.get("json_out", False)))

    # Programmatic convention.
    if args:
        make_agent = args[0]
        task = args[1] if len(args) > 1 else kwargs.get("task", "")
        n = args[2] if len(args) > 2 else kwargs.get("n", 1)
        models = args[3] if len(args) > 3 else kwargs.get("models")
    else:
        make_agent = kwargs.get("make_agent")
        task = kwargs.get("task", "")
        n = kwargs.get("n", 1)
        models = kwargs.get("models")

    if make_agent is None:
        # Fall back to CLI behavior (e.g. `witness bench` with no agent factory).
        return _run_bench_cli(task or None, bool(kwargs.get("json_out", False)))

    return _run_bench_full(make_agent, str(task), int(n), models)
