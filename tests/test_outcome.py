"""Tests for the outcome predictor (heuristic) and the training harness.

We build :class:`AnalysisContext` objects directly with plain ORM rows (not
persisted) so the tests do not need a database for the heuristic path. The
sklearn training test is skipped when scikit-learn is absent.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import witness.storage as storage
from witness.analysis import base, outcome


def _now():
    return datetime.now(timezone.utc)


def _step(idx: int, action_type: str, *, error=None, latency_ms: int = 10):
    return storage.Step(
        id=idx + 1,
        trace_id="trX",
        idx=idx,
        action_type=action_type,
        action_payload={},
        ts=_now() + timedelta(seconds=idx),
        latency_ms=latency_ms,
        error=error,
    )


def _ctx(trace_status: str, steps, *, trace_error=None, drift=0):
    trace = storage.Trace(
        id="trX",
        task="demo",
        started_at=_now(),
        status=trace_status,
        error=trace_error,
        step_count=len(steps),
    )
    calls_by_step = {s.id: [] for s in steps}
    ctx = base.AnalysisContext(
        trace=trace,
        steps=list(steps),
        llm_calls_by_step=calls_by_step,
        load_dom=lambda step, which: None,
    )
    if drift:
        # Best-effort drift hint the analyzer reads if present.
        ctx._trajectory_findings = [
            storage.Finding(
                trace_id="trX",
                kind=base.KIND_TRAJECTORY,
                title="drift",
                detail="",
            )
            for _ in range(drift)
        ]
    return ctx


def _healthy_ctx():
    steps = [
        _step(0, "navigate"),
        _step(1, "click"),
        _step(2, "type"),
        _step(3, "click"),
        _step(4, "done"),
    ]
    return _ctx("success", steps)


def _failing_ctx():
    steps = [
        _step(0, "navigate", latency_ms=50),
        _step(1, "click", error="not found", latency_ms=80),
        _step(2, "click", error="not found", latency_ms=200),
        _step(3, "click", error="not found", latency_ms=400),
        _step(4, "click", error="timeout", latency_ms=900),
    ]
    return _ctx("error", steps, trace_error="failed to complete")


# --- feature extraction -----------------------------------------------------


def test_extract_features_keys_match_feature_names():
    feats = outcome.extract_features(_healthy_ctx())
    assert set(feats.keys()) == set(outcome.FEATURE_NAMES)


def test_features_healthy_vs_failing():
    healthy = outcome.extract_features(_healthy_ctx())
    failing = outcome.extract_features(_failing_ctx())

    # Healthy run has no errors and ends on a terminal action.
    assert healthy["error_rate"] == 0.0
    assert healthy["had_error"] == 0.0
    assert healthy["last_action_is_terminal"] == 1.0

    # Failing run errors a lot, loops on one action, and never terminates clean.
    assert failing["error_rate"] > 0.0
    assert failing["had_error"] == 1.0
    assert failing["max_action_repeats"] >= 4
    assert failing["repeat_ratio"] > 0.0
    assert failing["last_action_is_terminal"] == 0.0
    assert failing["latency_rising"] == 1.0


def test_features_to_vector_is_ordered_and_dense():
    feats = outcome.extract_features(_healthy_ctx())
    vec = outcome.features_to_vector(feats)
    assert len(vec) == len(outcome.FEATURE_NAMES)
    assert all(isinstance(v, float) for v in vec)


def test_drift_signal_feature_counts():
    ctx = _ctx("running", [_step(0, "click"), _step(1, "click")], drift=2)
    feats = outcome.extract_features(ctx)
    assert feats["drift_signals"] == 2.0


def test_empty_trace_features_do_not_crash():
    ctx = _ctx("running", [])
    feats = outcome.extract_features(ctx)
    assert feats["step_count"] == 0.0
    assert feats["error_rate"] == 0.0


# --- finding / scoring ------------------------------------------------------


def test_analyze_emits_single_trace_level_finding():
    findings = outcome.analyze(_healthy_ctx())
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == outcome.KIND == base.KIND_OUTCOME
    assert f.step_id is None
    assert 0.0 <= f.score <= 1.0
    assert "features" in f.evidence
    assert f.created_at is not None


def test_score_ordering_healthy_beats_failing():
    healthy = outcome.analyze(_healthy_ctx())[0]
    failing = outcome.analyze(_failing_ctx())[0]
    assert healthy.score > failing.score


def test_failing_run_gets_higher_severity():
    healthy = outcome.analyze(_healthy_ctx())[0]
    failing = outcome.analyze(_failing_ctx())[0]
    order = ["info", "low", "medium", "high", "critical"]
    assert order.index(failing.severity) > order.index(healthy.severity)


def test_partial_trace_predicts_without_terminal_action():
    # A short, clean partial run with no terminal action yet.
    steps = [_step(0, "navigate"), _step(1, "click")]
    ctx = _ctx("running", steps)
    f = outcome.analyze(ctx)[0]
    assert 0.0 <= f.score <= 1.0


# --- training harness -------------------------------------------------------


def _point_storage_at(tmp: Path) -> None:
    storage.BASE_DIR = tmp
    storage.TRACES_DIR = tmp / "traces"
    storage.DB_PATH = tmp / "witness.db"
    storage._engine = None


def _seed_labelled_traces():
    """Insert several success and error traces for dataset/training tests."""
    with storage.get_session() as s:
        for i in range(6):
            tid = f"ok{i:02d}"
            s.add(
                storage.Trace(
                    id=tid,
                    task="t",
                    started_at=_now(),
                    status="success",
                )
            )
            for j, at in enumerate(["navigate", "click", "type", "done"]):
                s.add(
                    storage.Step(
                        trace_id=tid,
                        idx=j,
                        action_type=at,
                        action_payload={},
                        ts=_now(),
                        latency_ms=10,
                    )
                )
        for i in range(6):
            tid = f"bad{i:02d}"
            s.add(
                storage.Trace(
                    id=tid,
                    task="t",
                    started_at=_now(),
                    status="error",
                    error="boom",
                )
            )
            for j in range(5):
                s.add(
                    storage.Step(
                        trace_id=tid,
                        idx=j,
                        action_type="click",
                        action_payload={},
                        ts=_now(),
                        latency_ms=100 * (j + 1),
                        error="not found",
                    )
                )
        # An unlabelled (running) trace that must be skipped.
        s.add(
            storage.Trace(
                id="run01",
                task="t",
                started_at=_now(),
                status="running",
            )
        )
        s.commit()


def test_build_dataset_mines_labels(tmp_path):
    from witness.analysis import training

    _point_storage_at(tmp_path)
    storage.init_db()
    _seed_labelled_traces()

    X, y = training.build_dataset()
    assert len(X) == len(y) == 12  # 6 success + 6 error, running skipped
    assert set(y) == {0, 1}
    assert all(len(row) == len(outcome.FEATURE_NAMES) for row in X)


def test_load_model_missing_returns_none(tmp_path):
    from witness.analysis import training

    assert training.load_model(tmp_path / "nope.pkl") is None


def test_train_and_predict_with_sklearn(tmp_path):
    pytest.importorskip("sklearn")
    from witness.analysis import training

    _point_storage_at(tmp_path)
    storage.init_db()
    _seed_labelled_traces()
    training.reset_active_model_cache()

    model_path = tmp_path / "model.pkl"
    model = training.train(model_path)
    assert model is not None
    assert model_path.exists()

    loaded = training.load_model(model_path)
    assert loaded is not None

    healthy_feats = outcome.extract_features(_healthy_ctx())
    failing_feats = outcome.extract_features(_failing_ctx())
    p_healthy = training.predict_proba(loaded, healthy_feats)
    p_failing = training.predict_proba(loaded, failing_feats)
    assert 0.0 <= p_healthy <= 1.0
    assert 0.0 <= p_failing <= 1.0
    assert p_healthy > p_failing

    training.reset_active_model_cache()
