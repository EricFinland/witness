"""SQLModel schema + session helpers. Data lives at ~/.witness by default."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import Column, Text
from sqlmodel import JSON, Field, Session, SQLModel, create_engine


def _base_dir() -> Path:
    env = os.environ.get("WITNESS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".witness"


BASE_DIR = _base_dir()
TRACES_DIR = BASE_DIR / "traces"
DB_PATH = BASE_DIR / "witness.db"


class Trace(SQLModel, table=True):
    id: str = Field(primary_key=True, max_length=12)
    task: str
    model: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: str = "running"  # "running" | "success" | "error"
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    total_latency_ms: int = 0
    step_count: int = 0


class Step(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    trace_id: str = Field(foreign_key="trace.id", index=True)
    idx: int
    action_type: str
    action_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    ts: datetime
    latency_ms: int = 0
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    url: Optional[str] = None
    dom_before_path: Optional[str] = None
    dom_after_path: Optional[str] = None
    shot_before_path: Optional[str] = None
    shot_after_path: Optional[str] = None


class LLMCall(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    step_id: int = Field(foreign_key="step.id", index=True)
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    prompt: str = Field(default="", sa_column=Column(Text))
    response: str = Field(default="", sa_column=Column(Text))
    ts: datetime


_engine = None


def _ensure_dirs() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    TRACES_DIR.mkdir(parents=True, exist_ok=True)


def get_engine():
    global _engine
    if _engine is None:
        _ensure_dirs()
        _engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
        SQLModel.metadata.create_all(_engine)
    return _engine


def init_db() -> None:
    get_engine()


def get_session() -> Session:
    return Session(get_engine())


def trace_dir(trace_id: str) -> Path:
    d = TRACES_DIR / trace_id
    (d / "screenshots").mkdir(parents=True, exist_ok=True)
    (d / "doms").mkdir(parents=True, exist_ok=True)
    return d
