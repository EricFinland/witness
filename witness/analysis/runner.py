"""Analysis orchestration: build a context, run every analyzer, persist findings.

``run_analysis`` is idempotent: it deletes any prior findings for the trace
before inserting the fresh set, so re-running never duplicates rows.
"""

from __future__ import annotations

import importlib
import logging
from typing import Optional

from sqlmodel import select

from witness import storage
from witness.analysis.base import AnalysisContext

logger = logging.getLogger("witness")

ANALYZER_MODULES = [
    "witness.analysis.injection",
    "witness.analysis.trajectory",
    "witness.analysis.exfil",
    "witness.analysis.outcome",
]


def _make_load_dom(trace_id: str):
    """Return a load_dom callable bound to one trace.

    Reads the captured HTML blob for a step from
    ``storage.TRACES_DIR / trace_id / <dom_before_path|dom_after_path>``.
    Returns None when there is no captured DOM or the file is missing.
    """

    trace_root = storage.TRACES_DIR / trace_id

    def load_dom(step: storage.Step, which: str) -> Optional[str]:
        if which == "before":
            rel = step.dom_before_path
        elif which == "after":
            rel = step.dom_after_path
        else:
            return None
        if not rel:
            return None
        path = trace_root / rel
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            logger.debug("load_dom: could not read %s", path, exc_info=True)
            return None

    return load_dom


def _build_context(session, trace: storage.Trace) -> AnalysisContext:
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
    return AnalysisContext(
        trace=trace,
        steps=list(steps),
        llm_calls_by_step=calls_by_step,
        load_dom=_make_load_dom(trace.id),
    )


def _collect_findings(ctx: AnalysisContext) -> list[storage.Finding]:
    """Run every available analyzer. Missing or failing analyzers are skipped."""
    findings: list[storage.Finding] = []
    for mod_name in ANALYZER_MODULES:
        try:
            module = importlib.import_module(mod_name)
        except ImportError:
            logger.debug("analyzer module not available: %s", mod_name)
            continue
        analyze = getattr(module, "analyze", None)
        if analyze is None:
            logger.debug("analyzer module has no analyze(): %s", mod_name)
            continue
        try:
            result = analyze(ctx) or []
        except Exception:  # one bad analyzer must not break the rest
            logger.exception("analyzer failed: %s", mod_name)
            continue
        findings.extend(result)
    return findings


def run_analysis(trace_id: str) -> list[storage.Finding]:
    """Analyze one trace and return the freshly persisted findings.

    Returns an empty list if the trace does not exist or no analyzer produced
    a finding.
    """
    storage.init_db()
    with storage.get_session() as session:
        trace = session.get(storage.Trace, trace_id)
        if trace is None:
            logger.debug("run_analysis: trace not found: %s", trace_id)
            return []

        ctx = _build_context(session, trace)
        findings = _collect_findings(ctx)

        # Idempotent: clear any prior findings for this trace first.
        existing = session.exec(
            select(storage.Finding).where(storage.Finding.trace_id == trace_id)
        ).all()
        for row in existing:
            session.delete(row)

        for f in findings:
            f.id = None  # ensure a fresh insert
            f.trace_id = trace_id
            session.add(f)
        session.commit()
        for f in findings:
            session.refresh(f)
        # Detach so callers can use the objects after the session closes.
        for f in findings:
            session.expunge(f)
        return findings


def run_all() -> dict:
    """Analyze every trace. Returns ``{trace_id: finding_count}``."""
    storage.init_db()
    with storage.get_session() as session:
        trace_ids = [t.id for t in session.exec(select(storage.Trace)).all()]
    return {tid: len(run_analysis(tid)) for tid in trace_ids}
