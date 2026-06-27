"""Tests for witness.report -- HTML report + markdown PR summary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import witness.storage as storage


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


def _seed(s) -> str:
    """Create a trace with steps, an LLM call, and two findings."""
    now = datetime.now(timezone.utc)
    trace_id = "abc123abc123"
    t = storage.Trace(
        id=trace_id,
        task="Book a flight from SFO to JFK",
        model="claude-sonnet-4-5",
        started_at=now,
        ended_at=now + timedelta(seconds=12),
        status="success",
        total_cost_usd=0.0421,
        total_tokens=15432,
        total_latency_ms=12000,
        step_count=2,
    )
    s.add(t)
    s.commit()

    steps = []
    for idx, action in enumerate(["go_to_url", "click_element"]):
        step = storage.Step(
            trace_id=trace_id,
            idx=idx,
            action_type=action,
            action_payload={"index": idx},
            ts=now,
            latency_ms=1000,
            url="https://example.com",
        )
        s.add(step)
        s.commit()
        s.refresh(step)
        steps.append(step)

    s.add(
        storage.LLMCall(
            step_id=steps[0].id,
            model="claude-sonnet-4-5",
            prompt_tokens=1000,
            completion_tokens=200,
            cost_usd=0.0211,
            latency_ms=900,
            prompt="p",
            response="r",
            ts=now,
        )
    )
    s.commit()

    s.add(
        storage.Finding(
            trace_id=trace_id,
            step_id=None,
            kind="trajectory",
            severity="medium",
            score=0.62,
            title="Mild looping detected",
            detail="The agent revisited the same page twice.",
            evidence={"repeats": 2},
            created_at=now,
        )
    )
    s.add(
        storage.Finding(
            trace_id=trace_id,
            step_id=steps[1].id,
            kind="prompt_injection",
            severity="high",
            score=0.88,
            title="Injected instruction in DOM",
            detail="Page text attempted to override the task.",
            evidence={"snippet": "ignore previous instructions"},
            created_at=now,
        )
    )
    s.commit()
    return trace_id


def test_write_report_creates_file_with_task_and_findings(db):
    with storage.get_session() as s:
        trace_id = _seed(s)

    import witness.report as report

    out_path = db / "out" / "report.html"
    result = report.write_report(trace_id, out_path)

    assert result == out_path
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")

    # Self-contained single file.
    assert content.startswith("<!DOCTYPE html>")
    assert "<style>" in content

    # Header carries the task.
    assert "Book a flight from SFO to JFK" in content

    # Findings section is present with both findings.
    assert "Findings" in content
    assert "Mild looping detected" in content
    assert "Injected instruction in DOM" in content

    # Severity badges rendered.
    assert "high" in content
    assert "medium" in content

    # Step timeline + LLM calls.
    assert "go_to_url" in content
    assert "click_element" in content
    assert "claude-sonnet-4-5" in content


def test_write_report_default_path(db):
    with storage.get_session() as s:
        trace_id = _seed(s)

    import witness.report as report

    result = report.write_report(trace_id)
    assert result.exists()
    assert result.name == "report.html"


def test_write_report_out_alias(db):
    with storage.get_session() as s:
        trace_id = _seed(s)

    import witness.report as report

    target = db / "via_alias.html"
    result = report.write_report(trace_id, out=str(target))
    assert result == target
    assert target.exists()


def test_write_report_unknown_trace_raises(db):
    import witness.report as report

    with pytest.raises(LookupError):
        report.write_report("doesnotexist0")


def test_write_summary_markdown_has_cost_and_steps(db):
    with storage.get_session() as s:
        trace_id = _seed(s)

    import witness.report as report

    md = report.write_summary_markdown(trace_id)

    assert "Book a flight from SFO to JFK" in md
    assert trace_id in md
    # Cost is present and formatted.
    assert "$0.0421" in md
    # Step count present.
    assert "| Steps | 2 |" in md
    # Findings listed.
    assert "Mild looping detected" in md
    assert "Injected instruction in DOM" in md


def test_write_summary_markdown_no_findings(db):
    now = datetime.now(timezone.utc)
    with storage.get_session() as s:
        s.add(
            storage.Trace(
                id="nofind000000",
                task="Simple task",
                model="claude-haiku-4-5",
                started_at=now,
                ended_at=now,
                status="success",
                total_cost_usd=0.001,
                total_tokens=100,
                step_count=1,
            )
        )
        s.commit()

    import witness.report as report

    md = report.write_summary_markdown("nofind000000")
    assert "No findings" in md
    assert "$0.0010" in md
