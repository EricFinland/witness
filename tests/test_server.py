"""FastAPI endpoint smoke tests.

We populate a fake trace directly (no Browser Use required) and then hit
every route through TestClient.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import witness.storage as storage


def _point_storage(tmp_path) -> None:
    storage.BASE_DIR = tmp_path
    storage.TRACES_DIR = tmp_path / "traces"
    storage.DB_PATH = tmp_path / "witness.db"
    storage._engine = None


@pytest.fixture
def seeded(tmp_path):
    _point_storage(tmp_path)
    storage.init_db()

    tid = "abc123def456"
    tdir = storage.trace_dir(tid)
    (tdir / "screenshots" / "0000_shot_after.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 8)

    with storage.get_session() as s:
        s.add(
            storage.Trace(
                id=tid,
                task="test task",
                model="claude-sonnet-4-5",
                started_at=datetime.now(timezone.utc),
                status="success",
                total_cost_usd=0.12,
                total_tokens=1234,
                step_count=1,
            )
        )
        s.commit()
        step = storage.Step(
            trace_id=tid,
            idx=0,
            action_type="done",
            action_payload={"success": True},
            ts=datetime.now(timezone.utc),
            latency_ms=500,
            shot_after_path="screenshots/0000_shot_after.png",
        )
        s.add(step)
        s.commit()
        s.refresh(step)
        s.add(
            storage.LLMCall(
                step_id=step.id,
                model="claude-sonnet-4-5",
                prompt_tokens=1000,
                completion_tokens=234,
                cost_usd=0.12,
                latency_ms=420,
                prompt="hi",
                response="done",
                ts=datetime.now(timezone.utc),
            )
        )
        s.commit()

    # Import app AFTER storage is pointed at tmp so the app reads our DB.
    from witness.server import create_app

    return TestClient(create_app()), tid


def test_health(seeded):
    client, _ = seeded
    assert client.get("/api/health").json() == {"ok": True}


def test_list_traces(seeded):
    client, tid = seeded
    rows = client.get("/api/traces").json()
    assert len(rows) == 1
    assert rows[0]["id"] == tid
    assert rows[0]["status"] == "success"
    assert rows[0]["step_count"] == 1


def test_get_trace_detail_joins_steps_and_calls(seeded):
    client, tid = seeded
    d = client.get(f"/api/traces/{tid}").json()
    assert d["id"] == tid
    assert len(d["steps"]) == 1
    step = d["steps"][0]
    assert step["action_type"] == "done"
    assert len(step["llm_calls"]) == 1
    assert step["llm_calls"][0]["cost_usd"] == pytest.approx(0.12)


def test_get_missing_trace_404(seeded):
    client, _ = seeded
    assert client.get("/api/traces/does_not_exist").status_code == 404


def test_blob_serves_bytes(seeded):
    client, tid = seeded
    r = client.get(f"/api/traces/{tid}/blobs/screenshots/0000_shot_after.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_blob_rejects_traversal(seeded):
    client, tid = seeded
    # Starlette normalizes "..", but even if a raw path got through our
    # handler would reject it at resolve()+relative_to(). The URL normalization
    # returns 404 — that's still safe.
    r = client.get(f"/api/traces/{tid}/blobs/..%2Fsecrets")
    assert r.status_code in (400, 404)


def test_spa_fallback_serves_index_html(seeded):
    client, tid = seeded
    r = client.get(f"/traces/{tid}")
    # Whether or not _viewer_dist is built, the handler should never leak the
    # API 404 JSON for non-api routes.
    assert r.status_code == 200
    body = r.text
    assert "<html" in body.lower() or "not built" in body.lower()
