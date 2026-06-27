"""Supervised training harness for the outcome predictor.

The default outcome predictor in :mod:`witness.analysis.outcome` is a hand-tuned
heuristic that needs zero ML dependencies. This module is the optional upgrade
path: it mines labels from already-captured runs and trains a real classifier
over the very same feature view (:func:`outcome.extract_features`), so once a
model is trained the predictor can use it transparently.

Labels come straight from the capture data: a :class:`storage.Trace` whose
``status`` is ``"success"`` is a positive example (label 1) and one whose
``status`` is ``"error"`` is a negative example (label 0). Traces still
``"running"`` are unlabelled and skipped.

scikit-learn is an OPTIONAL import. The heuristic path never needs it; only
:func:`train` (and loading a model trained with it) does. Install the extra
with::

    pip install usewitness[ml]

If scikit-learn is missing, :func:`train` raises a clear, actionable error and
:func:`load_model` returns ``None`` so callers fall back to the heuristic.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Optional

from sqlmodel import select

from witness import storage
from witness.analysis import base, outcome

logger = logging.getLogger("witness")

_ML_HINT = (
    "scikit-learn is required to train or load an outcome model. "
    "Install it with: pip install usewitness[ml]"
)

# Where the active model lives by default. Kept under the witness data dir so it
# travels with the rest of the capture data.
DEFAULT_MODEL_PATH = storage.BASE_DIR / "models" / "outcome.pkl"

# Process-level cache so prediction does not re-read/unpickle on every call.
_active_model = None
_active_model_loaded = False


def _require_sklearn():
    """Import scikit-learn or raise a clear install hint."""
    try:
        import sklearn  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without sklearn
        raise RuntimeError(_ML_HINT) from exc
    return sklearn


def build_dataset(trace_ids: Optional[list[str]] = None) -> tuple[list[list[float]], list[int]]:
    """Mine ``(X, y)`` training data from captured runs.

    Each finished trace becomes one example: the feature vector from
    :func:`outcome.extract_features` (flattened via
    :func:`outcome.features_to_vector`) paired with a binary label
    (1 = success, 0 = error). Running/unlabelled traces are skipped.

    No ML dependency is needed to build the dataset; only :func:`train` needs
    scikit-learn. Returns parallel lists ``(X, y)``.
    """
    storage.init_db()
    X: list[list[float]] = []
    y: list[int] = []
    with storage.get_session() as session:
        stmt = select(storage.Trace)
        if trace_ids:
            stmt = stmt.where(storage.Trace.id.in_(trace_ids))
        traces = session.exec(stmt).all()
        for trace in traces:
            if trace.status == "success":
                label = 1
            elif trace.status == "error":
                label = 0
            else:
                continue  # unlabelled (still running)
            ctx = _build_context(session, trace)
            features = outcome.extract_features(ctx)
            X.append(outcome.features_to_vector(features))
            y.append(label)
    return X, y


def _build_context(session, trace: storage.Trace) -> base.AnalysisContext:
    """Construct an AnalysisContext for a trace without importing the runner.

    Kept local so the training harness has no dependency on the runner's
    private helpers.
    """
    steps = session.exec(
        select(storage.Step)
        .where(storage.Step.trace_id == trace.id)
        .order_by(storage.Step.idx)
    ).all()
    step_ids = [st.id for st in steps if st.id is not None]
    calls_by_step: dict[int, list[storage.LLMCall]] = {sid: [] for sid in step_ids}
    if step_ids:
        calls = session.exec(
            select(storage.LLMCall).where(storage.LLMCall.step_id.in_(step_ids))
        ).all()
        for c in calls:
            calls_by_step.setdefault(c.step_id, []).append(c)

    def _load_dom(step: storage.Step, which: str):
        return None  # training does not need DOM blobs

    return base.AnalysisContext(
        trace=trace,
        steps=list(steps),
        llm_calls_by_step=calls_by_step,
        load_dom=_load_dom,
    )


def train(model_out_path: str | Path = DEFAULT_MODEL_PATH, trace_ids: Optional[list[str]] = None):
    """Train a logistic-regression classifier and persist it to ``model_out_path``.

    Requires scikit-learn (``pip install usewitness[ml]``). Mines the dataset
    via :func:`build_dataset`, fits a small pipeline, pickles it to disk, and
    returns the fitted model. Raises ``RuntimeError`` if scikit-learn is absent
    or ``ValueError`` if there are not at least two classes to learn from.
    """
    _require_sklearn()
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    X, y = build_dataset(trace_ids=trace_ids)
    if not X:
        raise ValueError("no labelled traces available to train on")
    if len(set(y)) < 2:
        raise ValueError(
            "need both success and error traces to train; found a single class"
        )

    model = Pipeline(
        steps=[
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000)),
        ]
    )
    model.fit(X, y)

    out = Path(model_out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        pickle.dump({"feature_names": outcome.FEATURE_NAMES, "model": model}, fh)
    logger.info("outcome model trained on %d traces -> %s", len(X), out)

    # Refresh the active-model cache so subsequent predictions pick it up.
    global _active_model, _active_model_loaded
    _active_model = model
    _active_model_loaded = True
    return model


def load_model(path: str | Path = DEFAULT_MODEL_PATH):
    """Load a pickled model from ``path``.

    Returns the fitted estimator, or ``None`` if the file is missing or
    scikit-learn is not installed (so callers fall back to the heuristic).
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        _require_sklearn()
    except RuntimeError:
        logger.debug("load_model: scikit-learn missing; cannot load %s", p)
        return None
    try:
        with p.open("rb") as fh:
            payload = pickle.load(fh)
    except (OSError, pickle.UnpicklingError, EOFError):
        logger.debug("load_model: could not read model at %s", p, exc_info=True)
        return None
    if isinstance(payload, dict):
        return payload.get("model")
    return payload


def get_active_model():
    """Return the cached active model, loading it from disk once if present.

    Used by :func:`outcome.predict` to opportunistically use a trained model.
    Returns ``None`` when no model has been trained, which keeps the heuristic
    as the default path.
    """
    global _active_model, _active_model_loaded
    if not _active_model_loaded:
        _active_model = load_model(DEFAULT_MODEL_PATH)
        _active_model_loaded = True
    return _active_model


def predict_proba(model, features: dict) -> float:
    """Return the success probability (class 1) from a trained model.

    Accepts the feature dict produced by :func:`outcome.extract_features` and
    flattens it in the canonical order before asking the model.
    """
    vector = outcome.features_to_vector(features)
    proba = model.predict_proba([vector])[0]
    # Class order follows model.classes_; map probability of label 1.
    classes = list(getattr(model, "classes_", [0, 1]))
    if 1 in classes:
        return float(proba[classes.index(1)])
    return float(proba[-1])


def reset_active_model_cache() -> None:
    """Clear the in-process model cache (useful for tests)."""
    global _active_model, _active_model_loaded
    _active_model = None
    _active_model_loaded = False
