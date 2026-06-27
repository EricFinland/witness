"""Trajectory-level analysis: loops, drift, wasted steps, and backtracking.

This analyzer looks at the ordered sequence of steps in a trace and flags
behaviour that suggests the agent is not making progress toward its task:

* Loops / thrash: the same action type (with a similar payload) repeated
  several times in a row while neither the URL nor the DOM changes. This is
  the classic "stuck clicking the same dead button" pattern.
* Task drift: the recent actions and URLs growing lexically distant from the
  trace's stated task. We measure this with a stdlib lexical similarity (token
  Jaccard plus difflib) so there is no heavy dependency. A pluggable
  ``embed_fn`` hook lets a future version swap in embeddings, but the default
  path is pure stdlib.
* Wasted / dead-end steps: a step that errored or produced no observable state
  change (same URL and effectively the same DOM before and after).
* Backtracking: returning to a URL that was already visited earlier.

Each concrete issue is emitted as a step-level Finding. In addition, exactly
one trace-level Finding (``step_id=None``) carries the overall trajectory
health score (0 = bad, 1 = healthy) plus a breakdown in ``evidence``. The
viewer badges the trace from this trace-level finding.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Callable, Optional

from witness import storage
from witness.analysis import base

logger = logging.getLogger("witness")

KIND = base.KIND_TRAJECTORY

# A run of this many identical, state-less actions counts as a loop.
LOOP_MIN_REPEATS = 3
# Window of recent steps used when measuring task drift.
DRIFT_WINDOW = 5
# Below this similarity to the task, a recent window is considered drifting.
DRIFT_SIMILARITY_FLOOR = 0.08

# Optional embedding hook for a future v2. When set it must take a list of
# strings and return a list of equal-length vectors (lists of floats). The
# default trajectory analysis never calls it; lexical similarity is used.
embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None

_WORD_RE = re.compile(r"[a-z0-9]+")
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


def _tokens(text: str) -> set[str]:
    """Lowercased alphanumeric tokens, as a set (for Jaccard)."""
    return set(_WORD_RE.findall((text or "").lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _lexical_similarity(a: str, b: str) -> float:
    """Stdlib lexical similarity in 0..1.

    Blends token Jaccard with difflib's ratio so both shared vocabulary and
    ordering contribute. No external dependency.
    """
    a = a or ""
    b = b or ""
    jac = _jaccard(_tokens(a), _tokens(b))
    seq = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return (jac + seq) / 2.0


def _normalized_text(html: Optional[str]) -> str:
    """Strip tags and collapse whitespace so DOM noise does not mask sameness."""
    if not html:
        return ""
    no_tags = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", no_tags).strip().lower()


def _dom_fingerprint(html: Optional[str]) -> Optional[str]:
    """Cheap DOM fingerprint: length plus a hash of the normalized text.

    Returns None when there is no DOM to fingerprint, so callers can tell
    "unchanged" apart from "unknown".
    """
    if not html:
        return None
    norm = _normalized_text(html)
    digest = hashlib.sha1(norm.encode("utf-8", errors="replace")).hexdigest()
    return f"{len(norm)}:{digest}"


def _payload_signature(payload: dict) -> str:
    """Stable string signature of an action payload for similarity checks."""
    if not payload:
        return ""
    try:
        items = sorted((str(k), str(v)) for k, v in payload.items())
    except Exception:  # pragma: no cover - payloads are plain dicts in practice
        return str(payload)
    return "|".join(f"{k}={v}" for k, v in items)


def _action_text(step: storage.Step) -> str:
    """Human-ish description of an action used for drift measurement."""
    parts = [step.action_type or ""]
    if step.action_payload:
        parts.append(_payload_signature(step.action_payload))
    if step.url:
        parts.append(step.url)
    return " ".join(p for p in parts if p)


def _state_changed(
    step: storage.Step,
    load_dom: Callable[[storage.Step, str], Optional[str]],
    prev_url: Optional[str],
) -> bool:
    """Did this step produce an observable state change?

    True when the URL moved relative to the previous step, or when the DOM
    fingerprint differs between before and after. When no DOM is captured we
    fall back to the URL signal alone, and if there is no signal at all we
    conservatively treat it as "no change" (the step did nothing observable).
    """
    if step.url and step.url != prev_url:
        # A new URL (including the first navigation, where prev_url is None)
        # is a clear observable state change.
        return True
    before = _dom_fingerprint(load_dom(step, "before"))
    after = _dom_fingerprint(load_dom(step, "after"))
    if before is not None and after is not None:
        return before != after
    # No DOM evidence and the URL did not move: no positive signal of change.
    return False


def _severity_for_score(score: float) -> str:
    """Map a 0..1 issue score to a severity bucket."""
    if score >= 0.85:
        return "high"
    if score >= 0.6:
        return "medium"
    if score >= 0.3:
        return "low"
    return "info"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def analyze(ctx: base.AnalysisContext) -> list[storage.Finding]:
    steps = list(ctx.steps)
    findings: list[storage.Finding] = []

    loops: list[dict] = []
    wasted: list[dict] = []
    backtracks: list[dict] = []
    drift_points: list[dict] = []
    # Per-step badness in 0..1, used to pick the worst steps.
    step_badness: dict[int, float] = {}

    def bump(step: storage.Step, amount: float) -> None:
        if step.id is None:
            return
        step_badness[step.id] = min(1.0, step_badness.get(step.id, 0.0) + amount)

    # --- Per-step signals: wasted/dead-end + backtracking ---
    seen_urls: set[str] = set()
    prev_url: Optional[str] = None
    for step in steps:
        changed = _state_changed(step, ctx.load_dom, prev_url)
        errored = bool(step.error)

        if errored or not changed:
            reason = "errored" if errored else "no state change"
            wasted.append(
                {
                    "step_id": step.id,
                    "idx": step.idx,
                    "action_type": step.action_type,
                    "reason": reason,
                    "url": step.url,
                }
            )
            score = 0.7 if errored else 0.4
            bump(step, 0.5 if errored else 0.3)
            findings.append(
                storage.Finding(
                    trace_id=ctx.trace.id,
                    step_id=step.id,
                    kind=KIND,
                    severity=_severity_for_score(score),
                    score=score,
                    title=(
                        "Errored step" if errored else "Wasted step (no state change)"
                    ),
                    detail=(
                        f"Step {step.idx} ({step.action_type}) {reason}."
                        + (f" Error: {step.error}" if errored else "")
                    ),
                    evidence={
                        "idx": step.idx,
                        "action_type": step.action_type,
                        "reason": reason,
                        "url": step.url,
                        "error": step.error,
                    },
                    created_at=_now(),
                )
            )

        if step.url:
            if step.url in seen_urls and step.url != prev_url:
                backtracks.append(
                    {"step_id": step.id, "idx": step.idx, "url": step.url}
                )
                bump(step, 0.3)
                findings.append(
                    storage.Finding(
                        trace_id=ctx.trace.id,
                        step_id=step.id,
                        kind=KIND,
                        severity="low",
                        score=0.35,
                        title="Backtracked to a visited URL",
                        detail=(
                            f"Step {step.idx} returned to a previously visited URL: "
                            f"{step.url}"
                        ),
                        evidence={"idx": step.idx, "url": step.url},
                        created_at=_now(),
                    )
                )
            seen_urls.add(step.url)
            prev_url = step.url

    # --- Loops / thrash: runs of identical state-less actions ---
    run_start = 0
    while run_start < len(steps):
        run_end = run_start + 1
        base_step = steps[run_start]
        base_sig = (base_step.action_type, _payload_signature(base_step.action_payload))
        while run_end < len(steps):
            nxt = steps[run_end]
            nxt_sig = (nxt.action_type, _payload_signature(nxt.action_payload))
            if nxt_sig != base_sig:
                break
            run_end += 1
        run = steps[run_start:run_end]
        if len(run) >= LOOP_MIN_REPEATS:
            # Confirm no state change across the run (URL stays put and DOM
            # fingerprints, where present, are identical).
            urls = {s.url for s in run if s.url}
            fps = [
                _dom_fingerprint(ctx.load_dom(s, "after"))
                for s in run
            ]
            fps = [f for f in fps if f is not None]
            url_static = len(urls) <= 1
            dom_static = len(set(fps)) <= 1 if fps else True
            if url_static and dom_static:
                count = len(run)
                score = min(1.0, 0.5 + 0.15 * (count - LOOP_MIN_REPEATS))
                idxs = [s.idx for s in run]
                loops.append(
                    {
                        "action_type": base_step.action_type,
                        "count": count,
                        "idxs": idxs,
                        "url": next(iter(urls)) if urls else None,
                    }
                )
                for s in run:
                    bump(s, 0.6)
                # Flag the loop on its first step.
                findings.append(
                    storage.Finding(
                        trace_id=ctx.trace.id,
                        step_id=run[0].id,
                        kind=KIND,
                        severity=_severity_for_score(score),
                        score=score,
                        title="Action loop (thrashing)",
                        detail=(
                            f"Action '{base_step.action_type}' repeated {count} times "
                            f"with no state change (steps {idxs[0]}..{idxs[-1]})."
                        ),
                        evidence={
                            "action_type": base_step.action_type,
                            "count": count,
                            "idxs": idxs,
                            "url": next(iter(urls)) if urls else None,
                        },
                        created_at=_now(),
                    )
                )
        run_start = run_end

    # --- Task drift: recent windows growing lexically distant from the task ---
    task = ctx.trace.task or ""
    drift_score = 0.0
    if task and steps:
        window: list[str] = []
        for step in steps:
            window.append(_action_text(step))
            if len(window) > DRIFT_WINDOW:
                window.pop(0)
            window_text = " ".join(window)
            sim = _lexical_similarity(task, window_text)
            if sim < DRIFT_SIMILARITY_FLOOR:
                drift_points.append(
                    {"idx": step.idx, "step_id": step.id, "similarity": round(sim, 4)}
                )
                bump(step, 0.4)
        # Overall drift: how far the final window sits from the task.
        final_window = " ".join(
            _action_text(s) for s in steps[-DRIFT_WINDOW:]
        )
        final_sim = _lexical_similarity(task, final_window)
        drift_score = max(0.0, 1.0 - final_sim)
        if drift_points:
            worst = min(drift_points, key=lambda d: d["similarity"])
            findings.append(
                storage.Finding(
                    trace_id=ctx.trace.id,
                    step_id=worst["step_id"],
                    kind=KIND,
                    severity=_severity_for_score(min(1.0, drift_score)),
                    score=round(min(1.0, drift_score), 4),
                    title="Task drift",
                    detail=(
                        "Recent actions drifted lexically away from the stated task "
                        f"(lowest similarity {worst['similarity']} at step "
                        f"{worst['idx']})."
                    ),
                    evidence={
                        "task": task,
                        "final_similarity": round(final_sim, 4),
                        "points": drift_points,
                    },
                    created_at=_now(),
                )
            )

    # --- Trace-level health rollup ---
    n = max(1, len(steps))
    loop_steps = sum(len(lp["idxs"]) for lp in loops)
    loop_pressure = min(1.0, loop_steps / n)
    wasted_pressure = min(1.0, len(wasted) / n)
    backtrack_pressure = min(1.0, len(backtracks) / n)
    drift_pressure = min(1.0, drift_score)

    # Weighted penalty; loops and drift hurt the most.
    penalty = (
        0.40 * loop_pressure
        + 0.30 * drift_pressure
        + 0.20 * wasted_pressure
        + 0.10 * backtrack_pressure
    )
    health = round(max(0.0, 1.0 - penalty), 4)

    worst_steps = sorted(
        ({"step_id": sid, "badness": round(b, 4)} for sid, b in step_badness.items()),
        key=lambda d: d["badness"],
        reverse=True,
    )[:5]

    if health >= 0.8:
        health_sev = "info"
    elif health >= 0.6:
        health_sev = "low"
    elif health >= 0.4:
        health_sev = "medium"
    else:
        health_sev = "high"

    findings.append(
        storage.Finding(
            trace_id=ctx.trace.id,
            step_id=None,
            kind=KIND,
            severity=health_sev,
            score=health,
            title="Trajectory health",
            detail=(
                f"Overall trajectory health {health:.2f} across {len(steps)} steps "
                f"({len(loops)} loop run(s), {len(wasted)} wasted, "
                f"{len(backtracks)} backtrack(s), drift {drift_score:.2f})."
            ),
            evidence={
                "health": health,
                "loops": loops,
                "drift": {
                    "score": round(drift_score, 4),
                    "points": drift_points,
                },
                "wasted": wasted,
                "backtracks": backtracks,
                "worst_steps": worst_steps,
                "step_count": len(steps),
            },
            created_at=_now(),
        )
    )

    return findings
