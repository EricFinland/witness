"""FastAPI backend for the Witness viewer.

Three data endpoints + blob file server + SPA fallback.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import select

from witness import storage

# --- response models ---------------------------------------------------------


class TraceSummary(BaseModel):
    id: str
    task: str
    model: Optional[str]
    status: str
    started_at: datetime
    ended_at: Optional[datetime]
    total_cost_usd: float
    total_tokens: int
    total_latency_ms: int
    step_count: int


class LLMCallOut(BaseModel):
    id: int
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    prompt: str
    response: str
    ts: datetime


class StepOut(BaseModel):
    id: int
    idx: int
    action_type: str
    action_payload: dict
    ts: datetime
    latency_ms: int
    error: Optional[str]
    url: Optional[str]
    dom_before_path: Optional[str]
    dom_after_path: Optional[str]
    shot_before_path: Optional[str]
    shot_after_path: Optional[str]
    llm_calls: list[LLMCallOut]


class TraceDetail(TraceSummary):
    error: Optional[str]
    steps: list[StepOut]


# --- app ---------------------------------------------------------------------


def _viewer_dist_dir() -> Path:
    return Path(__file__).parent / "_viewer_dist"


def create_app() -> FastAPI:
    app = FastAPI(title="Witness", version="0.0.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/traces", response_model=list[TraceSummary])
    def list_traces() -> list[TraceSummary]:
        with storage.get_session() as s:
            rows = s.exec(
                select(storage.Trace).order_by(storage.Trace.started_at.desc())
            ).all()
            return [TraceSummary.model_validate(r, from_attributes=True) for r in rows]

    @app.get("/api/traces/{trace_id}", response_model=TraceDetail)
    def get_trace(trace_id: str) -> TraceDetail:
        with storage.get_session() as s:
            t = s.get(storage.Trace, trace_id)
            if t is None:
                raise HTTPException(404, "trace not found")
            steps = s.exec(
                select(storage.Step)
                .where(storage.Step.trace_id == trace_id)
                .order_by(storage.Step.idx)
            ).all()
            step_ids = [st.id for st in steps if st.id is not None]
            calls_by_step: dict[int, list[storage.LLMCall]] = {sid: [] for sid in step_ids}
            if step_ids:
                calls = s.exec(
                    select(storage.LLMCall).where(storage.LLMCall.step_id.in_(step_ids))
                ).all()
                for c in calls:
                    calls_by_step.setdefault(c.step_id, []).append(c)

            out_steps: list[StepOut] = []
            for st in steps:
                out_steps.append(
                    StepOut(
                        id=st.id or 0,
                        idx=st.idx,
                        action_type=st.action_type,
                        action_payload=st.action_payload or {},
                        ts=st.ts,
                        latency_ms=st.latency_ms,
                        error=st.error,
                        url=st.url,
                        dom_before_path=st.dom_before_path,
                        dom_after_path=st.dom_after_path,
                        shot_before_path=st.shot_before_path,
                        shot_after_path=st.shot_after_path,
                        llm_calls=[
                            LLMCallOut.model_validate(c, from_attributes=True)
                            for c in calls_by_step.get(st.id or -1, [])
                        ],
                    )
                )
            return TraceDetail(
                id=t.id,
                task=t.task,
                model=t.model,
                status=t.status,
                started_at=t.started_at,
                ended_at=t.ended_at,
                total_cost_usd=t.total_cost_usd,
                total_tokens=t.total_tokens,
                total_latency_ms=t.total_latency_ms,
                step_count=t.step_count,
                error=t.error,
                steps=out_steps,
            )

    @app.get("/api/traces/{trace_id}/blobs/{path:path}")
    def get_blob(trace_id: str, path: str):
        # Strict: path must be relative & under the trace dir. Reject absolute or
        # parent-traversal components before touching the filesystem.
        if ".." in path.split("/") or path.startswith("/") or ":" in path:
            raise HTTPException(400, "invalid path")
        trace_root = storage.TRACES_DIR / trace_id
        target = (trace_root / path).resolve()
        try:
            target.relative_to(trace_root.resolve())
        except ValueError:
            raise HTTPException(400, "path escapes trace dir")
        if not target.exists() or not target.is_file():
            raise HTTPException(404, "blob not found")
        return FileResponse(target)

    # --- SPA fallback: serve the built viewer ---
    dist = _viewer_dist_dir()
    index_html = dist / "index.html"

    if dist.exists() and index_html.exists():
        # Serve built assets. /assets/* is hashed, /index.html is the SPA shell.
        assets_dir = dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):
            # API paths above have already been matched. Everything else is the SPA.
            # But first, look for a real static file at this path (e.g. /favicon.ico).
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                return FileResponse(str(index_html))
            if full_path and candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(index_html))
    else:
        @app.get("/")
        def _missing_viewer() -> Response:
            return Response(
                "<h1>Witness viewer not built</h1>"
                "<p>Run <code>scripts/build_viewer.sh</code> to build it.</p>",
                media_type="text/html",
            )

    return app


app = create_app()
