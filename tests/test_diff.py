"""Tests for witness diff — step-sequence regression detection."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

import witness.storage as storage
from witness.cli import app


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


def _make_trace(s, trace_id: str, action_types: list[str], *, status: str = "success") -> list[storage.Step]:
    """Create a Trace + Steps in session s. Returns the Step list."""
    now = datetime.now(timezone.utc)
    t = storage.Trace(
        id=trace_id,
        task=f"task-{trace_id}",
        started_at=now,
        status=status,
        step_count=len(action_types),
        total_cost_usd=0.0,
        total_tokens=0,
        total_latency_ms=len(action_types) * 1000,
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


# ── pure diff logic ──────────────────────────────────────────────────────────

def test_diff_identical_traces(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", ["go_to_url", "click_element"])
        _make_trace(s, "bbbbbbbbbbbb", ["go_to_url", "click_element"])

    import witness.diff as diff_mod
    result = diff_mod.diff_traces("aaaaaaaaaaaa", "bbbbbbbbbbbb")

    assert len(result.pairs) == 2
    assert all(p.kind == "equal" for p in result.pairs)
    assert result.step_count_delta == 0
    assert result.cost_delta == pytest.approx(0.0)


def test_diff_extra_steps_in_b(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", ["go_to_url", "click_element"])
        _make_trace(s, "bbbbbbbbbbbb", ["go_to_url", "scroll_down", "click_element"])

    import witness.diff as diff_mod
    result = diff_mod.diff_traces("aaaaaaaaaaaa", "bbbbbbbbbbbb")

    kinds = [p.kind for p in result.pairs]
    assert "insert" in kinds
    assert result.step_count_delta == 1


def test_diff_missing_steps_in_b(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", ["go_to_url", "scroll_down", "click_element"])
        _make_trace(s, "bbbbbbbbbbbb", ["go_to_url", "click_element"])

    import witness.diff as diff_mod
    result = diff_mod.diff_traces("aaaaaaaaaaaa", "bbbbbbbbbbbb")

    kinds = [p.kind for p in result.pairs]
    assert "delete" in kinds
    assert result.step_count_delta == -1


def test_diff_changed_action_type(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", ["go_to_url", "type_text"])
        _make_trace(s, "bbbbbbbbbbbb", ["go_to_url", "click_element"])

    import witness.diff as diff_mod
    result = diff_mod.diff_traces("aaaaaaaaaaaa", "bbbbbbbbbbbb")

    kinds = [p.kind for p in result.pairs]
    assert "replace" in kinds


def test_diff_payload_changed_flag(db):
    now = datetime.now(timezone.utc)
    with storage.get_session() as s:
        for tid, payload in [("aaaaaaaaaaaa", {"index": 5}), ("bbbbbbbbbbbb", {"index": 7})]:
            s.add(storage.Trace(id=tid, task="t", started_at=now, status="success", step_count=1))
            s.commit()
            s.add(storage.Step(
                trace_id=tid, idx=0, action_type="click_element_by_index",
                action_payload=payload, ts=now, latency_ms=0,
            ))
            s.commit()

    import witness.diff as diff_mod
    result = diff_mod.diff_traces("aaaaaaaaaaaa", "bbbbbbbbbbbb")

    assert len(result.pairs) == 1
    assert result.pairs[0].kind == "equal"
    assert result.pairs[0].payload_changed is True


def test_diff_trace_not_found_raises(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", ["go_to_url"])

    import witness.diff as diff_mod
    with pytest.raises(LookupError, match="notfound00000"):
        diff_mod.diff_traces("aaaaaaaaaaaa", "notfound00000")


def test_diff_cost_summed_from_llm_calls(db):
    now = datetime.now(timezone.utc)
    with storage.get_session() as s:
        for tid, cost in [("aaaaaaaaaaaa", 0.01), ("bbbbbbbbbbbb", 0.03)]:
            s.add(storage.Trace(id=tid, task="t", started_at=now, status="success", step_count=1))
            s.commit()
            step = storage.Step(trace_id=tid, idx=0, action_type="click", action_payload={}, ts=now, latency_ms=0)
            s.add(step)
            s.commit()
            s.refresh(step)
            s.add(storage.LLMCall(
                step_id=step.id, model="claude-sonnet-4-5",
                prompt_tokens=100, completion_tokens=50, cost_usd=cost,
                latency_ms=50, prompt="p", response="r", ts=now,
            ))
            s.commit()

    import witness.diff as diff_mod
    result = diff_mod.diff_traces("aaaaaaaaaaaa", "bbbbbbbbbbbb")

    assert result.pairs[0].cost_a == pytest.approx(0.01)
    assert result.pairs[0].cost_b == pytest.approx(0.03)


# ── CLI text output ───────────────────────────────────────────────────────────

def test_diff_cli_text_output(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", ["go_to_url"])
        _make_trace(s, "bbbbbbbbbbbb", ["go_to_url", "scroll_down"])

    runner = CliRunner()
    result = runner.invoke(app, ["diff", "aaaaaaaaaaaa", "bbbbbbbbbbbb"])

    assert result.exit_code == 0
    assert "go_to_url" in result.output
    assert "scroll_down" in result.output
    assert "+" in result.output  # scroll_down is only in B, should show as inserted


def test_diff_cli_trace_not_found(db):
    runner = CliRunner()
    result = runner.invoke(app, ["diff", "doesnotexist1", "doesnotexist2"])

    assert result.exit_code == 1


def test_diff_cli_json_output(db):
    with storage.get_session() as s:
        _make_trace(s, "aaaaaaaaaaaa", ["go_to_url"])
        _make_trace(s, "bbbbbbbbbbbb", ["go_to_url"])

    runner = CliRunner()
    result = runner.invoke(app, ["diff", "aaaaaaaaaaaa", "bbbbbbbbbbbb", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["trace_a"]["id"] == "aaaaaaaaaaaa"
    assert data["trace_b"]["id"] == "bbbbbbbbbbbb"
    assert data["pairs"][0]["kind"] == "equal"
    assert "summary" in data
    assert "cost_a" in data["pairs"][0]
    assert "cost_b" in data["pairs"][0]
    assert "payload_changed" in data["pairs"][0]
