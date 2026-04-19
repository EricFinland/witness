"""SDK-level tests that don't require a real browser_use Agent.

We construct a minimal fake Agent and verify that `instrument()` wires up
step capture, trace lifecycle, and action extraction correctly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlmodel import select

import witness
import witness.storage as storage


def _point_storage(tmp_path) -> None:
    storage.BASE_DIR = tmp_path
    storage.TRACES_DIR = tmp_path / "traces"
    storage.DB_PATH = tmp_path / "witness.db"
    storage._engine = None


class FakeAction:
    """Stand-in for a Browser Use ActionModel."""

    def __init__(self, kind: str, payload: dict):
        self._kind = kind
        self._payload = payload

    def model_dump(self, exclude_none: bool = True) -> dict:
        return {self._kind: self._payload}


class FakeAgent:
    def __init__(self, task: str):
        self.task = task
        self.llm = SimpleNamespace(model="claude-sonnet-4-5")
        self.browser_session = None  # no browser in unit tests
        self.state = SimpleNamespace(last_model_output=None, last_result=None)
        self._calls = 0

    async def step(self, step_info=None):
        # Pretend the LLM just picked an action.
        self._calls += 1
        self.state.last_model_output = SimpleNamespace(
            action=[FakeAction("click_element_by_index", {"index": self._calls})]
        )

    async def run(self):
        for _ in range(3):
            await self.step()


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    _point_storage(tmp_path)
    yield


def test_instrument_creates_trace_row():
    agent = FakeAgent("my test task")
    witness.instrument(agent)
    tid = agent._witness_trace_id
    assert tid and len(tid) == 12

    with storage.get_session() as s:
        t = s.get(storage.Trace, tid)
        assert t is not None
        assert t.task == "my test task"
        assert t.status == "running"


def test_instrument_is_idempotent():
    agent = FakeAgent("task")
    witness.instrument(agent)
    first_id = agent._witness_trace_id
    witness.instrument(agent)  # second call must not create a new trace
    assert agent._witness_trace_id == first_id


def test_step_writes_step_row_and_extracts_action():
    agent = FakeAgent("task")
    witness.instrument(agent)
    asyncio.run(agent.step())
    with storage.get_session() as s:
        steps = s.exec(select(storage.Step).where(storage.Step.trace_id == agent._witness_trace_id)).all()
        assert len(steps) == 1
        assert steps[0].action_type == "click_element_by_index"
        assert steps[0].action_payload == {"index": 1}


def test_run_finalizes_trace_as_success():
    agent = FakeAgent("task")
    witness.instrument(agent)
    asyncio.run(agent.run())
    with storage.get_session() as s:
        t = s.get(storage.Trace, agent._witness_trace_id)
        assert t.status == "success"
        assert t.ended_at is not None
        assert t.step_count == 3


def test_run_finalizes_trace_as_error_on_exception():
    class Boom(FakeAgent):
        async def step(self, step_info=None):
            raise RuntimeError("agent exploded")

    agent = Boom("task")
    witness.instrument(agent)
    with pytest.raises(RuntimeError):
        asyncio.run(agent.run())
    with storage.get_session() as s:
        t = s.get(storage.Trace, agent._witness_trace_id)
        assert t.status == "error"
        assert t.error is not None
        assert "exploded" in t.error
