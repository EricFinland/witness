"""Adapter-layer tests.

Cover adapter detection, the SDK dispatcher, schema-record building, and the
best-effort depth-capture helpers. All hermetic: storage is pointed at a temp
dir and no real browser is ever launched.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import witness
import witness.storage as storage
from witness import adapters
from witness.adapters.base import BaseAdapter, CaptureAdapter
from witness.adapters.browser_use import (
    BrowserUseAdapter,
    _DepthCapture,
    _persist_depth_blobs,
    _safe_axtree,
)
from witness.adapters.playwright import PlaywrightAdapter
from witness.schema import SCHEMA_VERSION, StepRecord, TraceRecord


def _point_storage(tmp_path) -> None:
    storage.BASE_DIR = tmp_path
    storage.TRACES_DIR = tmp_path / "traces"
    storage.DB_PATH = tmp_path / "witness.db"
    storage._engine = None


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path):
    _point_storage(tmp_path)
    yield


class FakeAction:
    """Stand-in for a Browser Use ActionModel."""

    def __init__(self, kind: str, payload: dict):
        self._kind = kind
        self._payload = payload

    def model_dump(self, exclude_none: bool = True) -> dict:
        return {self._kind: self._payload}


class FakeAgent:
    """Looks enough like a browser_use.Agent for detection + instrumentation."""

    def __init__(self, task: str):
        self.task = task
        self.llm = SimpleNamespace(model="claude-sonnet-4-5")
        self.browser_session = None
        self.state = SimpleNamespace(last_model_output=None, last_result=None)
        self._calls = 0

    async def step(self, step_info=None):
        self._calls += 1
        self.state.last_model_output = SimpleNamespace(
            action=[FakeAction("click_element_by_index", {"index": self._calls})]
        )

    async def run(self):
        for _ in range(3):
            await self.step()


# --- detection ---------------------------------------------------------------


def test_browser_use_adapter_detects_agent():
    adapter = BrowserUseAdapter()
    assert adapter.detect(FakeAgent("do a thing")) is True


def test_browser_use_adapter_rejects_non_matching_object():
    adapter = BrowserUseAdapter()
    # Has task but no step/run.
    assert adapter.detect(SimpleNamespace(task="x")) is False
    # Plain object, no task.
    assert adapter.detect(SimpleNamespace(foo=1)) is False
    assert adapter.detect(None) is False
    assert adapter.detect("not an agent") is False


def test_playwright_adapter_detects_page_but_not_agent():
    adapter = PlaywrightAdapter()
    fake_page = SimpleNamespace(
        goto=lambda *a, **k: None,
        click=lambda *a, **k: None,
        context=object(),
    )
    assert adapter.detect(fake_page) is True
    # A browser_use agent (has task) must NOT be claimed by the playwright adapter.
    assert adapter.detect(FakeAgent("task")) is False


def test_playwright_adapter_instrument_is_not_implemented():
    adapter = PlaywrightAdapter()
    fake_page = SimpleNamespace(
        goto=lambda *a, **k: None,
        click=lambda *a, **k: None,
        context=object(),
    )
    with pytest.raises(NotImplementedError):
        adapter.instrument(fake_page)


def test_find_adapter_picks_browser_use_first():
    adapter = adapters.find_adapter(FakeAgent("task"))
    assert isinstance(adapter, BrowserUseAdapter)


def test_find_adapter_returns_none_for_unknown():
    assert adapters.find_adapter(SimpleNamespace(foo=1)) is None


def test_registry_entries_satisfy_protocol():
    assert adapters.ADAPTERS
    for adapter in adapters.ADAPTERS:
        assert isinstance(adapter, CaptureAdapter)


def test_base_adapter_instrument_raises():
    with pytest.raises(NotImplementedError):
        BaseAdapter().instrument(object())


# --- dispatcher / instrument -------------------------------------------------


def test_instrument_creates_trace_row():
    agent = FakeAgent("my adapter task")
    witness.instrument(agent)
    tid = agent._witness_trace_id
    assert tid and len(tid) == 12
    assert agent._witness_schema_version == SCHEMA_VERSION

    with storage.get_session() as s:
        t = s.get(storage.Trace, tid)
        assert t is not None
        assert t.task == "my adapter task"
        assert t.status == "running"


def test_instrument_rejects_unknown_object():
    with pytest.raises(TypeError):
        witness.instrument(SimpleNamespace(foo=1))


def test_instrument_via_adapter_writes_step_and_finalizes_run():
    agent = FakeAgent("task")
    witness.instrument(agent)
    asyncio.run(agent.run())
    with storage.get_session() as s:
        t = s.get(storage.Trace, agent._witness_trace_id)
        assert t.status == "success"
        assert t.step_count == 3
        # Schema records build from the persisted rows.
        TraceRecord.from_orm_obj(t)


def test_step_extracts_action_via_adapter():
    agent = FakeAgent("task")
    witness.instrument(agent)
    asyncio.run(agent.step())
    from sqlmodel import select

    with storage.get_session() as s:
        steps = s.exec(
            select(storage.Step).where(
                storage.Step.trace_id == agent._witness_trace_id
            )
        ).all()
        assert len(steps) == 1
        assert steps[0].action_type == "click_element_by_index"
        assert steps[0].action_payload["index"] == 1
        StepRecord.from_orm_obj(steps[0])


# --- schema record building --------------------------------------------------


def test_trace_record_builds_with_current_schema_version():
    assert isinstance(SCHEMA_VERSION, str) and SCHEMA_VERSION
    agent = FakeAgent("schema task")
    witness.instrument(agent)
    with storage.get_session() as s:
        t = s.get(storage.Trace, agent._witness_trace_id)
        rec = TraceRecord.from_orm_obj(t)
        assert rec.id == agent._witness_trace_id
        assert rec.task == "schema task"


# --- depth capture (B2/B3), all guarded --------------------------------------


def test_depth_capture_no_op_when_page_has_no_events():
    # A page object with no `on` listener: capture stays inactive, returns None.
    cap = _DepthCapture(SimpleNamespace())
    asyncio.run(cap.start())
    assert asyncio.run(cap.collect_network()) is None
    assert asyncio.run(cap.collect_console()) is None


def test_depth_capture_records_events_when_page_emits():
    handlers: dict[str, list] = {}

    class FakePage:
        def on(self, event, cb):
            handlers.setdefault(event, []).append(cb)

    page = FakePage()
    cap = _DepthCapture(page)
    asyncio.run(cap.start())

    # Simulate playwright firing events.
    for cb in handlers.get("request", []):
        cb(SimpleNamespace(url="https://x/a", method="GET", resource_type="document"))
    for cb in handlers.get("console", []):
        cb(SimpleNamespace(type="error", text="boom"))
    for cb in handlers.get("pageerror", []):
        cb("ReferenceError: x is not defined")

    net = asyncio.run(cap.collect_network())
    con = asyncio.run(cap.collect_console())
    assert net == [
        {
            "url": "https://x/a",
            "method": "GET",
            "resource_type": "document",
            "failed": False,
        }
    ]
    assert {"type": "error", "text": "boom"} in con
    assert any(c["type"] == "pageerror" for c in con)


def test_depth_capture_stop_detaches_all_listeners():
    # The leak fix: every listener registered by start() must be removed by
    # stop() so they do not accumulate across steps on the long-lived page.
    handlers: dict[str, list] = {}

    class FakePage:
        def on(self, event, cb):
            handlers.setdefault(event, []).append(cb)

        def remove_listener(self, event, cb):
            handlers.get(event, []).remove(cb)

    page = FakePage()
    cap = _DepthCapture(page)
    asyncio.run(cap.start())
    total_registered = sum(len(v) for v in handlers.values())
    assert total_registered == 4

    asyncio.run(cap.stop())
    assert sum(len(v) for v in handlers.values()) == 0


def test_depth_capture_no_listener_growth_across_many_steps():
    # Simulate a long run: a fresh capture per step on the SAME page must not
    # leave listeners behind after each step's stop().
    handlers: dict[str, list] = {}

    class FakePage:
        def on(self, event, cb):
            handlers.setdefault(event, []).append(cb)

        def off(self, event, cb):
            handlers.get(event, []).remove(cb)

    page = FakePage()
    for _ in range(50):
        cap = _DepthCapture(page)
        asyncio.run(cap.start())
        asyncio.run(cap.collect_network())
        asyncio.run(cap.collect_console())
        asyncio.run(cap.stop())
    assert sum(len(v) for v in handlers.values()) == 0


def test_depth_capture_stop_guarded_when_no_remover():
    # A page that only supports on() (no remove_listener/off) must not crash.
    class FakePage:
        def on(self, event, cb):
            pass

    cap = _DepthCapture(FakePage())
    asyncio.run(cap.start())
    asyncio.run(cap.stop())  # must not raise


def test_safe_axtree_returns_none_for_bare_page():
    assert asyncio.run(_safe_axtree(SimpleNamespace())) is None
    assert asyncio.run(_safe_axtree(None)) is None


def test_safe_axtree_uses_accessibility_snapshot():
    class FakeAccessibility:
        async def snapshot(self):
            return {"role": "WebArea", "children": []}

    page = SimpleNamespace(accessibility=FakeAccessibility())
    tree = asyncio.run(_safe_axtree(page))
    assert tree == {"role": "WebArea", "children": []}


def test_persist_depth_blobs_writes_files(tmp_path):
    trace_dir = tmp_path / "traces" / "abc"
    trace_dir.mkdir(parents=True)
    paths = _persist_depth_blobs(
        trace_dir,
        0,
        network=[{"url": "https://x", "method": "GET"}],
        console=[{"type": "log", "text": "hi"}],
        axtree={"role": "WebArea"},
    )
    assert set(paths) == {"network", "console", "axtree"}
    for key, rel in paths.items():
        full = trace_dir / rel
        assert full.exists()
        json.loads(full.read_text(encoding="utf-8"))


def test_persist_depth_blobs_skips_none(tmp_path):
    trace_dir = tmp_path / "traces" / "abc"
    trace_dir.mkdir(parents=True)
    paths = _persist_depth_blobs(
        trace_dir, 0, network=None, console=None, axtree=None
    )
    assert paths == {}
