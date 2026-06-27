"""Canonical, versioned capture schema.

These pydantic v2 records mirror the storage SQLModel tables one-to-one and are
the stable serialization format used by adapters and export. Bump
``SCHEMA_VERSION`` whenever the shape changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from witness import storage

SCHEMA_VERSION = "1.0"


class LLMCallRecord(BaseModel):
    id: Optional[int] = None
    step_id: int
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    prompt: str = ""
    response: str = ""
    ts: datetime

    @classmethod
    def from_orm_obj(cls, row: storage.LLMCall) -> "LLMCallRecord":
        return cls.model_validate(row, from_attributes=True)


class StepRecord(BaseModel):
    id: Optional[int] = None
    trace_id: str
    idx: int
    action_type: str
    action_payload: dict = {}
    ts: datetime
    latency_ms: int = 0
    error: Optional[str] = None
    url: Optional[str] = None
    dom_before_path: Optional[str] = None
    dom_after_path: Optional[str] = None
    shot_before_path: Optional[str] = None
    shot_after_path: Optional[str] = None

    @classmethod
    def from_orm_obj(cls, row: storage.Step) -> "StepRecord":
        return cls.model_validate(row, from_attributes=True)


class FindingRecord(BaseModel):
    id: Optional[int] = None
    trace_id: str
    step_id: Optional[int] = None
    kind: str
    severity: str = "info"
    score: float = 0.0
    title: str = ""
    detail: str = ""
    evidence: dict = {}
    created_at: datetime

    @classmethod
    def from_orm_obj(cls, row: storage.Finding) -> "FindingRecord":
        return cls.model_validate(row, from_attributes=True)


class TraceRecord(BaseModel):
    id: str
    task: str
    model: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: str = "running"
    error: Optional[str] = None
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    total_latency_ms: int = 0
    step_count: int = 0

    @classmethod
    def from_orm_obj(cls, row: storage.Trace) -> "TraceRecord":
        return cls.model_validate(row, from_attributes=True)
