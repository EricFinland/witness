"""Tests for the trajectory analyzer.

We build AnalysisContext directly with synthetic step sequences (no DB needed),
covering a healthy run, a looping run, and a drifting run. The trace-level
health finding is the one the viewer badges, so its score ordering is the key
assertion: healthy > drifting and healthy > looping.
"""

from datetime import datetime, timezone

from witness import storage
from witness.analysis import base, trajectory


def _step(idx, action_type, payload=None, url=None, error=None):
    s = storage.Step(
        id=idx + 1,
        trace_id="tr",
        idx=idx,
        action_type=action_type,
        action_payload=payload or {},
        ts=datetime.now(timezone.utc),
        url=url,
        error=error,
    )
    return s


def _ctx(task, steps, dom=None):
    """Build a context. ``dom`` maps (step_id, which) -> html string."""
    dom = dom or {}

    def load_dom(step, which):
        return dom.get((step.id, which))

    trace = storage.Trace(
        id="tr",
        task=task,
        started_at=datetime.now(timezone.utc),
        status="success",
    )
    return base.AnalysisContext(
        trace=trace,
        steps=steps,
        llm_calls_by_step={},
        load_dom=load_dom,
    )


def _health(findings):
    """Pull the single trace-level health finding's score."""
    trace_level = [f for f in findings if f.step_id is None]
    assert len(trace_level) == 1, "expected exactly one trace-level finding"
    assert trace_level[0].title == "Trajectory health"
    return trace_level[0].score, trace_level[0]


def _healthy_run():
    task = "search for blue running shoes and add them to the cart"
    steps = [
        _step(0, "navigate", {"url": "https://shop.test/"}, url="https://shop.test/"),
        _step(
            1,
            "type",
            {"selector": "#search", "text": "blue running shoes"},
            url="https://shop.test/search?q=blue+running+shoes",
        ),
        _step(
            2,
            "click",
            {"selector": ".product-blue-shoes"},
            url="https://shop.test/product/blue-running-shoes",
        ),
        _step(
            3,
            "click",
            {"selector": "#add-to-cart"},
            url="https://shop.test/cart",
        ),
    ]
    return _ctx(task, steps)


def _looping_run():
    task = "search for blue running shoes and add them to the cart"
    # Same action+payload repeated, URL never changes, DOM identical.
    url = "https://shop.test/"
    steps = [_step(i, "click", {"selector": "#go"}, url=url) for i in range(5)]
    dom = {}
    for s in steps:
        dom[(s.id, "before")] = "<html><body>same page</body></html>"
        dom[(s.id, "after")] = "<html><body>same page</body></html>"
    return _ctx(task, steps, dom)


def _drifting_run():
    task = "search for blue running shoes and add them to the cart"
    # Actions wander to totally unrelated territory; each step changes URL so
    # they are not wasted/looping, only drifting.
    steps = [
        _step(0, "navigate", {"url": "https://news.test/"}, url="https://news.test/a"),
        _step(
            1,
            "click",
            {"selector": "#politics"},
            url="https://news.test/politics/election",
        ),
        _step(
            2,
            "click",
            {"selector": "#weather"},
            url="https://weather.test/forecast/tomorrow",
        ),
        _step(
            3,
            "click",
            {"selector": "#sports"},
            url="https://sports.test/scores/baseball",
        ),
        _step(
            4,
            "click",
            {"selector": "#stocks"},
            url="https://finance.test/markets/nasdaq",
        ),
    ]
    return _ctx(task, steps)


def test_healthy_run_scores_high():
    findings = trajectory.analyze(_healthy_run())
    score, _ = _health(findings)
    assert score >= 0.7


def test_loop_detected_and_flagged():
    ctx = _looping_run()
    findings = trajectory.analyze(ctx)
    health_score, health = _health(findings)

    # A loop must be detected and recorded in the rollup evidence.
    assert health.evidence["loops"], "expected at least one loop run"
    loop = health.evidence["loops"][0]
    assert loop["count"] == 5
    assert loop["action_type"] == "click"

    # There must be an explicit step-level loop finding.
    loop_findings = [
        f for f in findings if f.step_id is not None and f.title == "Action loop (thrashing)"
    ]
    assert loop_findings

    # The worst step should be one of the looping steps.
    worst = health.evidence["worst_steps"]
    assert worst
    looping_step_ids = {s.id for s in ctx.steps}
    assert worst[0]["step_id"] in looping_step_ids


def test_drift_detected_and_flagged():
    ctx = _drifting_run()
    findings = trajectory.analyze(ctx)
    health_score, health = _health(findings)

    assert health.evidence["drift"]["score"] > 0.5

    drift_findings = [
        f for f in findings if f.step_id is not None and f.title == "Task drift"
    ]
    assert drift_findings


def test_health_score_ordering():
    healthy, _ = _health(trajectory.analyze(_healthy_run()))
    looping, _ = _health(trajectory.analyze(_looping_run()))
    drifting, _ = _health(trajectory.analyze(_drifting_run()))

    # Healthy run is strictly healthier than both pathological runs.
    assert healthy > looping
    assert healthy > drifting


def test_exactly_one_trace_level_finding():
    for ctx_factory in (_healthy_run, _looping_run, _drifting_run):
        findings = trajectory.analyze(ctx_factory())
        trace_level = [f for f in findings if f.step_id is None]
        assert len(trace_level) == 1
        assert trace_level[0].kind == base.KIND_TRAJECTORY
        assert 0.0 <= trace_level[0].score <= 1.0


def test_wasted_step_flagged():
    task = "do a thing"
    # An errored step plus a no-change step (same URL, identical DOM).
    url = "https://x.test/"
    steps = [
        _step(0, "navigate", {"url": url}, url=url),
        _step(1, "click", {"selector": "#broken"}, url=url, error="element not found"),
    ]
    dom = {
        (steps[1].id, "before"): "<html>same</html>",
        (steps[1].id, "after"): "<html>same</html>",
    }
    ctx = _ctx(task, steps, dom)
    findings = trajectory.analyze(ctx)
    _, health = _health(findings)
    assert health.evidence["wasted"], "expected wasted steps recorded"
    errored = [
        f for f in findings if f.step_id is not None and f.title == "Errored step"
    ]
    assert errored
