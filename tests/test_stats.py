"""Tests for witness stats — aggregate cost reporting."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import witness.storage as storage
from witness.cli import app
from typer.testing import CliRunner


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


def _make_trace(s, trace_id: str, *, model: str = "claude-sonnet-4-5",
                cost: float = 0.01, tokens: int = 500,
                status: str = "success", days_ago: int = 0) -> None:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    s.add(storage.Trace(
        id=trace_id,
        task=f"task-{trace_id}",
        model=model,
        started_at=ts,
        ended_at=ts,
        status=status,
        total_cost_usd=cost,
        total_tokens=tokens,
        total_latency_ms=1000,
        step_count=1,
    ))
    s.commit()


# ── compute_stats() ──────────────────────────────────────────────────────────

def test_stats_empty_db(db):
    import witness.stats as stats_mod
    result = stats_mod.compute_stats()
    assert result.trace_count == 0
    assert result.total_cost_usd == 0.0
    assert result.total_tokens == 0
    assert result.by_model == []
    assert result.by_day == []


def test_stats_totals(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", cost=0.01, tokens=100)
        _make_trace(s, "bbbbbbbbbbbb", cost=0.03, tokens=300)

    import witness.stats as stats_mod
    result = stats_mod.compute_stats()

    assert result.trace_count == 2
    assert result.total_cost_usd == pytest.approx(0.04)
    assert result.total_tokens == 400
    assert result.avg_cost_usd == pytest.approx(0.02)
    assert result.avg_tokens == pytest.approx(200.0)


def test_stats_success_error_counts(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", status="success")
        _make_trace(s, "bbbbbbbbbbbb", status="success")
        _make_trace(s, "cccccccccccc", status="error")

    import witness.stats as stats_mod
    result = stats_mod.compute_stats()

    assert result.success_count == 2
    assert result.error_count == 1


def test_stats_by_model_sorted_by_cost(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", model="gpt-4o", cost=0.005)
        _make_trace(s, "bbbbbbbbbbbb", model="claude-sonnet-4-5", cost=0.02)
        _make_trace(s, "cccccccccccc", model="claude-sonnet-4-5", cost=0.01)

    import witness.stats as stats_mod
    result = stats_mod.compute_stats()

    assert len(result.by_model) == 2
    # sorted by cost desc
    assert result.by_model[0].model == "claude-sonnet-4-5"
    assert result.by_model[0].total_cost_usd == pytest.approx(0.03)
    assert result.by_model[0].trace_count == 2
    assert result.by_model[1].model == "gpt-4o"
    assert result.by_model[1].total_cost_usd == pytest.approx(0.005)


def test_stats_by_day_recent_only(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", cost=0.01, days_ago=1)   # yesterday — in window
        _make_trace(s, "bbbbbbbbbbbb", cost=0.02, days_ago=2)   # 2 days ago — in window
        _make_trace(s, "cccccccccccc", cost=0.05, days_ago=40)  # 40 days ago — excluded

    import witness.stats as stats_mod
    result = stats_mod.compute_stats(days=7)

    assert len(result.by_day) == 2
    # totals include all traces (by_day is filtered but totals are not)
    assert result.trace_count == 3
    assert result.total_cost_usd == pytest.approx(0.08)
    # by_day only has the recent 2
    day_costs = sum(d.total_cost_usd for d in result.by_day)
    assert day_costs == pytest.approx(0.03)


def test_stats_by_day_sorted_newest_first(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", days_ago=3)
        _make_trace(s, "bbbbbbbbbbbb", days_ago=1)
        _make_trace(s, "cccccccccccc", days_ago=2)

    import witness.stats as stats_mod
    result = stats_mod.compute_stats(days=7)

    dates = [d.date for d in result.by_day]
    assert dates == sorted(dates, reverse=True)


def test_stats_unknown_model_grouped_as_unknown(db):
    now = datetime.now(timezone.utc)
    with storage.get_session() as s:
        s.add(storage.Trace(
            id="aaaaaaaaaaaa", task="t", model=None,
            started_at=now, status="success",
            total_cost_usd=0.01, total_tokens=100, step_count=1,
        ))
        s.commit()

    import witness.stats as stats_mod
    result = stats_mod.compute_stats()

    assert len(result.by_model) == 1
    assert result.by_model[0].model == "unknown"


# ── API endpoint ──────────────────────────────────────────────────────────────

def test_stats_api_returns_structure(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", cost=0.01, tokens=100)

    from fastapi.testclient import TestClient
    from witness.server import create_app
    client = TestClient(create_app())

    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert "totals" in data
    assert "by_model" in data
    assert "by_day" in data
    assert data["totals"]["trace_count"] == 1
    assert data["totals"]["total_cost_usd"] == pytest.approx(0.01)


def test_stats_api_days_param(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", days_ago=1)   # recent
        _make_trace(s, "bbbbbbbbbbbb", days_ago=40)  # old

    from fastapi.testclient import TestClient
    from witness.server import create_app
    client = TestClient(create_app())

    r = client.get("/api/stats?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data["by_day"]) == 1  # only the recent trace


# ── CLI command ───────────────────────────────────────────────────────────────

def test_stats_cli_basic_output(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", cost=0.01, tokens=100, model="claude-sonnet-4-5")

    runner = CliRunner()
    result = runner.invoke(app, ["stats"])

    assert result.exit_code == 0
    assert "claude-sonnet-4-5" in result.output
    assert "0.01" in result.output


def test_stats_cli_empty_db(db):
    runner = CliRunner()
    result = runner.invoke(app, ["stats"])

    assert result.exit_code == 0
    assert "No traces" in result.output


def test_stats_cli_json_flag(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", cost=0.02, tokens=200)

    runner = CliRunner()
    result = runner.invoke(app, ["stats", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["totals"]["trace_count"] == 1
    assert "by_model" in data
    assert "by_day" in data
