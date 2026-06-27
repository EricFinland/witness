"""Outcome prediction: estimate whether a (possibly partial) run will succeed.

This is the early-stop / risk-signal port of the "proof predictor" idea: rather
than waiting for an agent run to finish (and pay for) a doomed trajectory, we
score the run-so-far and surface a single trace-level :class:`storage.Finding`
that predicts success probability. A low probability is itself the risk signal,
so a low ``score`` carries a high ``severity`` ("likely to fail") and lets a
caller decide to stop early.

The default predictor here is a transparent heuristic over cheap features:

* step count relative to a typical run,
* error rate across steps,
* repeated / looping actions,
* rising latency (a stall or thrash signal),
* presence of trajectory-drift findings (if a prior analysis pass produced any),
* the type of the last action taken.

``extract_features`` is exposed separately so the supervised training harness in
:mod:`witness.analysis.training` can reuse the exact same feature view to learn
a real classifier from labelled runs.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from witness import storage
from witness.analysis import base

logger = logging.getLogger("witness")

KIND = base.KIND_OUTCOME

# A "typical" healthy run length. Used only to normalise the step-count feature;
# the heuristic does not hard-fail long runs, it just treats very long ones as a
# mild risk signal (agents that wander tend to take more steps).
TYPICAL_STEP_COUNT = 8

# Ordered feature names. The training harness relies on this exact ordering to
# turn the feature dict into a dense vector, so keep it stable.
FEATURE_NAMES = [
    "step_count",
    "step_count_ratio",
    "error_rate",
    "had_error",
    "repeat_ratio",
    "max_action_repeats",
    "latency_rising",
    "drift_signals",
    "last_action_is_terminal",
    "llm_call_count",
]

# Action types that usually mean the agent reached a deliberate end state.
_TERMINAL_ACTIONS = {"done", "finish", "complete", "submit", "answer", "stop"}


def _latency_rising(latencies: list[int]) -> float:
    """Return 1.0 if step latency trends upward across the run, else 0.0.

    Compares the mean latency of the first half of the run to the second half.
    A rising trend often means the agent is thrashing (retries, growing context)
    and correlates with eventual failure. Needs at least four steps to judge.
    """
    n = len(latencies)
    if n < 4:
        return 0.0
    mid = n // 2
    first = latencies[:mid]
    second = latencies[mid:]
    if not first or not second:
        return 0.0
    first_avg = sum(first) / len(first)
    second_avg = sum(second) / len(second)
    if first_avg <= 0:
        return 1.0 if second_avg > 0 else 0.0
    return 1.0 if second_avg > first_avg * 1.25 else 0.0


def _drift_signal_count(ctx: base.AnalysisContext) -> int:
    """Count trajectory-drift signals visible from the context.

    The outcome analyzer cannot assume a prior trajectory pass has run, so this
    looks for a best-effort, side-effect-free hint: a ``_trajectory_findings``
    attribute the runner or a caller may attach to the context. Absent that, it
    returns 0. This keeps the analyzer self-contained while still benefiting
    from drift information when it is available.
    """
    extra = getattr(ctx, "_trajectory_findings", None)
    if not extra:
        return 0
    try:
        return sum(1 for f in extra if getattr(f, "kind", None) == base.KIND_TRAJECTORY)
    except TypeError:
        return 0


def extract_features(ctx: base.AnalysisContext) -> dict:
    """Compute the numeric feature view for one (possibly partial) trace.

    Returns a plain ``dict`` keyed by the names in :data:`FEATURE_NAMES`. The
    same function feeds both the heuristic predictor and the ML training
    harness, so the two never disagree about what a "feature" is.
    """
    steps = list(ctx.steps)
    step_count = len(steps)

    error_steps = sum(1 for s in steps if s.error)
    error_rate = (error_steps / step_count) if step_count else 0.0
    trace_had_error = 1.0 if (ctx.trace.error or error_steps) else 0.0

    action_types = [s.action_type for s in steps]
    counts = Counter(action_types)
    max_action_repeats = max(counts.values()) if counts else 0
    # Fraction of steps that are duplicates of an action already seen.
    distinct = len(counts)
    repeat_ratio = ((step_count - distinct) / step_count) if step_count else 0.0

    latencies = [int(s.latency_ms or 0) for s in steps]
    latency_rising = _latency_rising(latencies)

    drift_signals = float(_drift_signal_count(ctx))

    last_action = action_types[-1] if action_types else ""
    last_action_is_terminal = (
        1.0 if last_action.lower() in _TERMINAL_ACTIONS else 0.0
    )

    llm_call_count = sum(len(v) for v in ctx.llm_calls_by_step.values())

    return {
        "step_count": float(step_count),
        "step_count_ratio": float(step_count) / float(TYPICAL_STEP_COUNT),
        "error_rate": float(error_rate),
        "had_error": float(trace_had_error),
        "repeat_ratio": float(repeat_ratio),
        "max_action_repeats": float(max_action_repeats),
        "latency_rising": float(latency_rising),
        "drift_signals": float(drift_signals),
        "last_action_is_terminal": float(last_action_is_terminal),
        "llm_call_count": float(llm_call_count),
    }


def features_to_vector(features: dict) -> list[float]:
    """Flatten a feature dict into a dense vector in :data:`FEATURE_NAMES` order."""
    return [float(features.get(name, 0.0)) for name in FEATURE_NAMES]


def _heuristic_success_probability(features: dict) -> float:
    """Map features to a success probability in ``[0.0, 1.0]``.

    Starts from an optimistic prior and subtracts penalties for each risk
    signal, then adds a bonus when the run cleanly reached a terminal action.
    The weights are deliberately simple and explainable; the ML harness exists
    for when a learned model is wanted.
    """
    prob = 0.85

    # Errors are the strongest negative signal.
    prob -= 0.45 * features["error_rate"]
    if features["had_error"] and features["error_rate"] == 0.0:
        # Trace-level error with no per-step error attribution still counts.
        prob -= 0.30

    # Looping / repeated actions: an agent stuck doing the same thing.
    prob -= 0.25 * features["repeat_ratio"]
    if features["max_action_repeats"] >= 4:
        prob -= 0.10

    # Rising latency suggests thrash.
    prob -= 0.10 * features["latency_rising"]

    # Trajectory drift signals each chip away at confidence.
    prob -= 0.10 * min(features["drift_signals"], 3.0)

    # Wandering: many more steps than a typical run.
    if features["step_count_ratio"] > 2.0:
        prob -= 0.15
    elif features["step_count_ratio"] > 1.5:
        prob -= 0.05

    # A clean terminal action is a positive signal.
    if features["last_action_is_terminal"] and features["error_rate"] == 0.0:
        prob += 0.10

    return max(0.0, min(1.0, prob))


def _severity_for(prob: float) -> str:
    """Lower success probability => higher risk severity."""
    if prob < 0.25:
        return "critical"
    if prob < 0.45:
        return "high"
    if prob < 0.65:
        return "medium"
    if prob < 0.85:
        return "low"
    return "info"


def predict(ctx: base.AnalysisContext) -> tuple[float, dict]:
    """Return ``(success_probability, features)`` for a trace.

    Tries a trained model first if one is available via the training harness;
    falls back to the transparent heuristic otherwise. Kept separate from
    :func:`analyze` so other code (an early-stop loop, the bench harness) can
    ask for just the number.
    """
    features = extract_features(ctx)
    prob = None
    try:
        from witness.analysis import training

        model = training.get_active_model()
        if model is not None:
            prob = training.predict_proba(model, features)
    except Exception:  # a model problem must never break prediction
        logger.debug("outcome: trained model unavailable, using heuristic", exc_info=True)
        prob = None
    if prob is None:
        prob = _heuristic_success_probability(features)
    return float(prob), features


def analyze(ctx: base.AnalysisContext) -> list[storage.Finding]:
    """Emit one trace-level outcome Finding predicting success probability."""
    prob, features = predict(ctx)
    severity = _severity_for(prob)

    if prob >= 0.65:
        title = f"Run likely to succeed ({prob:.0%} confidence)"
    elif prob >= 0.45:
        title = f"Run outcome uncertain ({prob:.0%} success estimate)"
    else:
        title = f"Run likely to fail ({prob:.0%} success estimate)"

    reasons = []
    if features["error_rate"] > 0:
        reasons.append(f"{features['error_rate']:.0%} of steps errored")
    if features["had_error"] and features["error_rate"] == 0.0:
        reasons.append("trace-level error recorded")
    if features["repeat_ratio"] > 0.3:
        reasons.append("repeated/looping actions")
    if features["latency_rising"]:
        reasons.append("step latency rising")
    if features["drift_signals"]:
        reasons.append(f"{int(features['drift_signals'])} trajectory-drift signal(s)")
    if features["step_count_ratio"] > 1.5:
        reasons.append("run longer than typical")
    if features["last_action_is_terminal"] and features["error_rate"] == 0.0:
        reasons.append("reached a clean terminal action")
    detail = (
        "Predicted success probability "
        f"{prob:.2f}. "
        + ("Signals: " + ", ".join(reasons) + "." if reasons else "No strong risk signals.")
    )

    finding = storage.Finding(
        trace_id=ctx.trace.id,
        step_id=None,
        kind=KIND,
        severity=severity,
        score=prob,
        title=title,
        detail=detail,
        evidence={"features": features, "success_probability": prob},
        created_at=datetime.now(timezone.utc),
    )
    return [finding]
