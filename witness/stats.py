"""Aggregate cost statistics across all traces — no Rich/Typer/FastAPI imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from witness import storage


@dataclass
class ModelStat:
    model: str
    trace_count: int
    total_cost_usd: float
    total_tokens: int
    avg_cost_usd: float


@dataclass
class DayStat:
    date: str  # "YYYY-MM-DD" in UTC
    trace_count: int
    total_cost_usd: float
    total_tokens: int


@dataclass
class StatsResult:
    trace_count: int
    success_count: int
    error_count: int
    total_cost_usd: float
    total_tokens: int
    avg_cost_usd: float
    avg_tokens: float
    by_model: list[ModelStat] = field(default_factory=list)
    by_day: list[DayStat] = field(default_factory=list)


def compute_stats(days: int = 30) -> StatsResult:
    """Aggregate spend across all traces.

    by_day is filtered to the last `days` days; totals cover all time.
    """
    with storage.get_session() as s:
        traces = list(s.exec(select(storage.Trace)).all())

    if not traces:
        return StatsResult(
            trace_count=0, success_count=0, error_count=0,
            total_cost_usd=0.0, total_tokens=0,
            avg_cost_usd=0.0, avg_tokens=0.0,
        )

    trace_count = len(traces)
    success_count = sum(1 for t in traces if t.status == "success")
    error_count = sum(1 for t in traces if t.status == "error")
    total_cost = sum(t.total_cost_usd or 0.0 for t in traces)
    total_tokens = sum(t.total_tokens or 0 for t in traces)
    avg_cost = total_cost / trace_count
    avg_tokens = total_tokens / trace_count

    # Group by model — sorted by cost desc
    model_acc: dict[str, dict] = {}
    for t in traces:
        key = t.model or "unknown"
        if key not in model_acc:
            model_acc[key] = {"trace_count": 0, "total_cost_usd": 0.0, "total_tokens": 0}
        model_acc[key]["trace_count"] += 1
        model_acc[key]["total_cost_usd"] += t.total_cost_usd or 0.0
        model_acc[key]["total_tokens"] += t.total_tokens or 0
    by_model = sorted(
        [
            ModelStat(
                model=k,
                trace_count=v["trace_count"],
                total_cost_usd=v["total_cost_usd"],
                total_tokens=v["total_tokens"],
                avg_cost_usd=v["total_cost_usd"] / v["trace_count"] if v["trace_count"] else 0.0,
            )
            for k, v in model_acc.items()
        ],
        key=lambda x: x.total_cost_usd,
        reverse=True,
    )

    # Group by day — recent `days` only, sorted newest first
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    day_acc: dict[str, dict] = {}
    for t in traces:
        ts = t.started_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        date_str = ts.strftime("%Y-%m-%d")
        if date_str not in day_acc:
            day_acc[date_str] = {"trace_count": 0, "total_cost_usd": 0.0, "total_tokens": 0}
        day_acc[date_str]["trace_count"] += 1
        day_acc[date_str]["total_cost_usd"] += t.total_cost_usd or 0.0
        day_acc[date_str]["total_tokens"] += t.total_tokens or 0
    by_day = sorted(
        [DayStat(date=k, **v) for k, v in day_acc.items()],
        key=lambda x: x.date,
        reverse=True,
    )

    return StatsResult(
        trace_count=trace_count,
        success_count=success_count,
        error_count=error_count,
        total_cost_usd=total_cost,
        total_tokens=total_tokens,
        avg_cost_usd=avg_cost,
        avg_tokens=avg_tokens,
        by_model=by_model,
        by_day=by_day,
    )
