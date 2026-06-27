"""Foundation tests for the analysis layer + versioned schema records.

We repoint storage's module-level paths at a tmp dir (same trick as
test_storage.py) so each test runs against a throwaway DB.
"""

from datetime import datetime, timezone
from pathlib import Path

import witness.storage as storage
from witness import schema
from witness.analysis import base, runner


def _point_storage_at(tmp: Path) -> None:
    storage.BASE_DIR = tmp
    storage.TRACES_DIR = tmp / "traces"
    storage.DB_PATH = tmp / "witness.db"
    storage._engine = None  # force re-create against new path


def _seed_trace(trace_id: str = "tr0001") -> None:
    with storage.get_session() as s:
        s.add(
            storage.Trace(
                id=trace_id,
                task="demo",
                started_at=datetime.now(timezone.utc),
                status="success",
            )
        )
        step = storage.Step(
            trace_id=trace_id,
            idx=0,
            action_type="click",
            action_payload={"selector": "#go"},
            ts=datetime.now(timezone.utc),
        )
        s.add(step)
        s.commit()
        s.refresh(step)
        s.add(
            storage.LLMCall(
                step_id=step.id,
                model="gpt-x",
                prompt="hi",
                response="ok",
                ts=datetime.now(timezone.utc),
            )
        )
        s.commit()


def test_finding_table_round_trips(tmp_path):
    _point_storage_at(tmp_path)
    storage.init_db()
    _seed_trace()

    with storage.get_session() as s:
        f = storage.Finding(
            trace_id="tr0001",
            step_id=None,
            kind=base.KIND_OUTCOME,
            severity="high",
            score=0.9,
            title="bad outcome",
            detail="long detail text",
            evidence={"k": "v", "n": 3},
        )
        s.add(f)
        s.commit()
        s.refresh(f)
        fid = f.id

    with storage.get_session() as s:
        loaded = s.get(storage.Finding, fid)
        assert loaded is not None
        assert loaded.trace_id == "tr0001"
        assert loaded.step_id is None
        assert loaded.kind == "outcome"
        assert loaded.severity == "high"
        assert loaded.score == 0.9
        assert loaded.evidence == {"k": "v", "n": 3}
        assert isinstance(loaded.created_at, datetime)


def test_run_analysis_no_analyzers_returns_empty_and_idempotent(tmp_path, monkeypatch):
    _point_storage_at(tmp_path)
    storage.init_db()
    _seed_trace()

    # Isolate the runner mechanics: with no analyzers registered it finds nothing.
    monkeypatch.setattr(runner, "ANALYZER_MODULES", [])

    first = runner.run_analysis("tr0001")
    assert first == []

    # Re-running is safe and still empty (idempotent).
    second = runner.run_analysis("tr0001")
    assert second == []

    with storage.get_session() as s:
        from sqlmodel import select

        rows = s.exec(
            select(storage.Finding).where(storage.Finding.trace_id == "tr0001")
        ).all()
        assert rows == []


def test_run_analysis_clears_prior_findings(tmp_path, monkeypatch):
    _point_storage_at(tmp_path)
    storage.init_db()
    _seed_trace()

    # With no analyzers registered, re-analysis must wipe any stale findings.
    monkeypatch.setattr(runner, "ANALYZER_MODULES", [])

    # Manually insert a stale finding, then confirm re-analysis wipes it
    # (no analyzers produce replacements here).
    with storage.get_session() as s:
        s.add(
            storage.Finding(
                trace_id="tr0001",
                kind=base.KIND_TRAJECTORY,
                title="stale",
                detail="",
            )
        )
        s.commit()

    result = runner.run_analysis("tr0001")
    assert result == []

    with storage.get_session() as s:
        from sqlmodel import select

        rows = s.exec(
            select(storage.Finding).where(storage.Finding.trace_id == "tr0001")
        ).all()
        assert rows == []


def test_run_analysis_unknown_trace(tmp_path):
    _point_storage_at(tmp_path)
    storage.init_db()
    assert runner.run_analysis("nope99") == []


def test_run_all(tmp_path, monkeypatch):
    _point_storage_at(tmp_path)
    storage.init_db()
    _seed_trace("tr0001")
    _seed_trace("tr0002")
    # Isolate runner mechanics from analyzer output.
    monkeypatch.setattr(runner, "ANALYZER_MODULES", [])
    counts = runner.run_all()
    assert counts == {"tr0001": 0, "tr0002": 0}


def test_schema_records_build_from_storage_rows(tmp_path):
    _point_storage_at(tmp_path)
    storage.init_db()
    _seed_trace()

    with storage.get_session() as s:
        from sqlmodel import select

        trace = s.get(storage.Trace, "tr0001")
        step = s.exec(
            select(storage.Step).where(storage.Step.trace_id == "tr0001")
        ).first()
        call = s.exec(select(storage.LLMCall)).first()
        finding = storage.Finding(
            trace_id="tr0001",
            kind=base.KIND_EXFILTRATION,
            title="t",
            detail="d",
            evidence={"a": 1},
        )
        s.add(finding)
        s.commit()
        s.refresh(finding)

        tr = schema.TraceRecord.from_orm_obj(trace)
        sr = schema.StepRecord.from_orm_obj(step)
        cr = schema.LLMCallRecord.from_orm_obj(call)
        fr = schema.FindingRecord.from_orm_obj(finding)

    assert schema.SCHEMA_VERSION == "1.0"
    assert tr.id == "tr0001"
    assert tr.task == "demo"
    assert sr.action_type == "click"
    assert sr.action_payload == {"selector": "#go"}
    assert cr.model == "gpt-x"
    assert fr.kind == "exfiltration"
    assert fr.evidence == {"a": 1}


def test_load_dom_reads_blob(tmp_path):
    _point_storage_at(tmp_path)
    storage.init_db()

    trace_id = "tr0003"
    with storage.get_session() as s:
        s.add(
            storage.Trace(
                id=trace_id,
                task="dom",
                started_at=datetime.now(timezone.utc),
                status="success",
            )
        )
        step = storage.Step(
            trace_id=trace_id,
            idx=0,
            action_type="navigate",
            ts=datetime.now(timezone.utc),
            dom_before_path="doms/0_before.html",
        )
        s.add(step)
        s.commit()
        s.refresh(step)
        step_id = step.id

    d = storage.trace_dir(trace_id)
    (d / "doms" / "0_before.html").write_text("<html>hi</html>", encoding="utf-8")

    load_dom = runner._make_load_dom(trace_id)
    with storage.get_session() as s:
        step = s.get(storage.Step, step_id)
        assert load_dom(step, "before") == "<html>hi</html>"
        assert load_dom(step, "after") is None
        assert load_dom(step, "bogus") is None
