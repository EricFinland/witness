"""Tests for witness.bench — reliability metrics over captured traces.

All tests operate on synthetic Trace/Step rows. No browser is launched.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import witness.storage as storage
from witness import bench


def _point_storage(tmp_path):
    storage.BASE_DIR = tmp_path
    storage.TRACES_DIR = tmp_path / "traces"
    storage.DB_PATH = tmp_path / "witness.db"
    storage._engine = None


@pytest.fixture
def db(tmp_path):
    _point_storage(tmp_path)
    storage.init_db()
    return tmp_path


def _make_trace(
    s,
    trace_id: str,
    action_types: list[str],
    *,
    status: str = "success",
    model: str | None = "test-model",
    cost: float = 0.0,
    tokens: int = 0,
    latency_ms: int | None = None,
) -> list[storage.Step]:
    now = datetime.now(timezone.utc)
    if latency_ms is None:
        latency_ms = len(action_types) * 1000
    t = storage.Trace(
        id=trace_id,
        task="reliability-task",
        model=model,
        started_at=now,
        status=status,
        step_count=len(action_types),
        total_cost_usd=cost,
        total_tokens=tokens,
        total_latency_ms=latency_ms,
    )
    s.add(t)
    s.commit()
    steps = []
    for idx, action_type in enumerate(action_types):
        step = storage.Step(
            trace_id=trace_id,
            idx=idx,
            action_type=action_type,
            action_payload={},
            ts=now,
            latency_ms=1000,
        )
        s.add(step)
        s.commit()
        s.refresh(step)
        steps.append(step)
    return steps


def _all_traces(s):
    from sqlmodel import select

    return list(s.exec(select(storage.Trace)).all())


# --- success rate ------------------------------------------------------------


def test_success_rate_and_counts(db):
    with storage.get_session() as s:
        _make_trace(s, "t1", ["go_to_url", "click"], status="success")
        _make_trace(s, "t2", ["go_to_url", "click"], status="success")
        _make_trace(s, "t3", ["go_to_url"], status="error")
        _make_trace(s, "t4", ["go_to_url", "click"], status="error")
        traces = _all_traces(s)

    report = bench.summarize(traces)
    assert report.n == 4
    assert report.success_count == 2
    assert report.error_count == 2
    assert report.success_rate == pytest.approx(0.5)


def test_all_success_not_flaky(db):
    with storage.get_session() as s:
        for i in range(5):
            _make_trace(s, f"s{i}", ["go_to_url", "click"], status="success")
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert report.success_rate == pytest.approx(1.0)
    assert report.flaky is False
    assert report.flakiness == pytest.approx(0.0)


def test_half_pass_is_flaky(db):
    with storage.get_session() as s:
        _make_trace(s, "p1", ["a", "b"], status="success")
        _make_trace(s, "p2", ["a", "b"], status="success")
        _make_trace(s, "f1", ["a"], status="error")
        _make_trace(s, "f2", ["a"], status="error")
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert report.flaky is True
    # Flakiness peaks at 0.5 when exactly half pass.
    assert report.flakiness == pytest.approx(0.5)


# --- distributions -----------------------------------------------------------


def test_step_count_distribution(db):
    with storage.get_session() as s:
        _make_trace(s, "d1", ["a", "b", "c"])  # 3 steps
        _make_trace(s, "d2", ["a", "b", "c", "d", "e"])  # 5 steps
        _make_trace(s, "d3", ["a", "b", "c", "d"])  # 4 steps
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert report.steps.count == 3
    assert report.steps.min == 3
    assert report.steps.max == 5
    assert report.steps.mean == pytest.approx(4.0)
    assert report.steps.median == pytest.approx(4.0)
    assert sorted(report.steps.values) == [3.0, 4.0, 5.0]


def test_cost_distribution_and_variance(db):
    with storage.get_session() as s:
        _make_trace(s, "c1", ["a"], cost=0.10)
        _make_trace(s, "c2", ["a"], cost=0.20)
        _make_trace(s, "c3", ["a"], cost=0.30)
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert report.cost.mean == pytest.approx(0.20)
    assert report.cost.min == pytest.approx(0.10)
    assert report.cost.max == pytest.approx(0.30)
    # Population stdev of {0.1,0.2,0.3} is sqrt(2/3)*0.1 ~= 0.08165.
    assert report.cost.stdev == pytest.approx(0.0816496, rel=1e-3)
    assert report.cost.cv == pytest.approx(report.cost.stdev / 0.20, rel=1e-6)


def test_latency_distribution(db):
    with storage.get_session() as s:
        _make_trace(s, "l1", ["a"], latency_ms=1000)
        _make_trace(s, "l2", ["a"], latency_ms=3000)
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert report.latency.min == 1000
    assert report.latency.max == 3000
    assert report.latency.mean == pytest.approx(2000.0)


def test_single_run_has_zero_variance(db):
    with storage.get_session() as s:
        _make_trace(s, "only", ["a", "b"], cost=0.5)
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert report.steps.stdev == pytest.approx(0.0)
    assert report.cost.cv == pytest.approx(0.0)
    assert report.flaky is False


def test_empty_traces(db):
    report = bench.summarize([])
    assert report.n == 0
    assert report.success_rate == 0.0
    assert report.steps.count == 0
    assert report.flaky is False


# --- per-model ---------------------------------------------------------------


def test_success_rate_by_model(db):
    with storage.get_session() as s:
        _make_trace(s, "ma1", ["a"], status="success", model="model-a")
        _make_trace(s, "ma2", ["a"], status="error", model="model-a")
        _make_trace(s, "mb1", ["a"], status="success", model="model-b")
        _make_trace(s, "mb2", ["a"], status="success", model="model-b")
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert set(report.models) == {"model-a", "model-b"}
    assert report.success_rate_by_model["model-a"] == pytest.approx(0.5)
    assert report.success_rate_by_model["model-b"] == pytest.approx(1.0)


# --- divergence --------------------------------------------------------------


def test_divergence_identical_runs(db):
    with storage.get_session() as s:
        _make_trace(s, "id1", ["go_to_url", "click", "extract"])
        _make_trace(s, "id2", ["go_to_url", "click", "extract"])
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert len(report.divergences) == 1
    d = report.divergences[0]
    assert d.diverged is False
    assert d.first_divergence_idx is None
    assert "identical" in d.summary


def test_divergence_detects_first_split(db):
    with storage.get_session() as s:
        _make_trace(s, "v1", ["go_to_url", "click", "extract"])
        _make_trace(s, "v2", ["go_to_url", "scroll", "extract"])
        traces = _all_traces(s)
    report = bench.summarize(traces)
    assert len(report.divergences) == 1
    d = report.divergences[0]
    assert d.diverged is True
    # First step matched (go_to_url); divergence at the second step.
    assert d.matched_steps == 1
    assert d.first_divergence_idx == 1


# --- serialization -----------------------------------------------------------


def test_to_dict_and_markdown(db):
    with storage.get_session() as s:
        _make_trace(s, "r1", ["a", "b"], status="success", cost=0.1)
        _make_trace(s, "r2", ["a", "c"], status="error", cost=0.2)
        traces = _all_traces(s)
    report = bench.summarize(traces)

    d = report.to_dict()
    assert d["n"] == 2
    assert d["success_count"] == 1
    assert "steps" in d and d["steps"]["count"] == 2
    assert isinstance(d["runs"], list) and len(d["runs"]) == 2

    md = report.to_markdown()
    assert "# Bench report" in md
    assert "Success rate" in md
    assert "r1" in md and "r2" in md


# --- reference tasks ---------------------------------------------------------


def test_load_reference_tasks_returns_list():
    tasks = bench.load_reference_tasks()
    assert isinstance(tasks, list)
    assert tasks, "expected at least one reference task"
    for entry in tasks:
        assert "name" in entry
        assert "path" in entry or "task" in entry


# --- CLI convention ----------------------------------------------------------


def test_run_bench_cli_convention(db, capsys):
    with storage.get_session() as s:
        _make_trace(s, "cli1", ["a", "b"], status="success")
        _make_trace(s, "cli2", ["a", "b"], status="success")

    code = bench.run_bench(suite="reliability", json_out=True)
    assert code == 0
    out = capsys.readouterr().out
    assert '"success_count": 2' in out


def test_run_bench_cli_no_traces(db, capsys):
    code = bench.run_bench(suite="nope", json_out=False)
    assert code == 1
