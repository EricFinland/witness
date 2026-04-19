"""Share-command tests. HTTP is mocked; we're testing the client-side contract:

- only sends data we actually have
- redacts DOM blobs + prompts + URLs
- honors the first-time consent prompt
- respects WITNESS_UPLOAD_ENDPOINT
- records the share in ~/.witness/shares.jsonl
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

import witness.config as config_mod
import witness.share as share_mod
import witness.storage as storage


def _point_storage(tmp_path) -> None:
    storage.BASE_DIR = tmp_path
    storage.TRACES_DIR = tmp_path / "traces"
    storage.DB_PATH = tmp_path / "witness.db"
    storage._engine = None
    config_mod.reset_cache()


@pytest.fixture
def seeded(tmp_path):
    _point_storage(tmp_path)
    storage.init_db()
    tid = "trace1234567"
    tdir = storage.trace_dir(tid)
    # One HTML blob containing an Anthropic key — redaction must kick in.
    leak = "<html><body>hi ada@example.com sk-ant-api03-abcdefghijklmnopqrst</body></html>"
    (tdir / "doms" / "0000_dom_after.html").write_text(leak, encoding="utf-8")
    (tdir / "screenshots" / "0000_shot_after.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"xx")

    with storage.get_session() as s:
        s.add(storage.Trace(
            id=tid, task="leak sk-ant-api03-deaddeaddeaddeaddeaddeaddeaddead",
            started_at=datetime.now(timezone.utc), status="success",
            step_count=1, total_cost_usd=0.01,
        ))
        s.commit()
        st = storage.Step(
            trace_id=tid, idx=0, action_type="done", action_payload={"success": True},
            ts=datetime.now(timezone.utc), latency_ms=100,
            url="https://x.io/login?password=hunter2",
            dom_after_path="doms/0000_dom_after.html",
            shot_after_path="screenshots/0000_shot_after.png",
        )
        s.add(st)
        s.commit()
        s.refresh(st)
        s.add(storage.LLMCall(
            step_id=st.id, model="claude-sonnet-4-5",
            prompt_tokens=10, completion_tokens=5, cost_usd=0.01, latency_ms=100,
            prompt="Authorization: Bearer sk-ant-api03-verysecretkeyvalueherexx",
            response="ok", ts=datetime.now(timezone.utc),
        ))
        s.commit()

    # Pre-grant consent so share doesn't prompt in tests.
    config_mod.record_share_consent()
    return tid


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


def _fake_post(captured: dict):
    def _post(self, url, json=None, headers=None, **kw):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return _FakeResponse({
            "id": "abc123def456",
            "deletion_token": "delete-me-xyz",
            "url": "https://usewitness.dev/t/abc123def456",
            "expires_at": "2026-05-19T00:00:00Z",
        })
    return _post


def test_share_uploads_and_redacts(seeded, capsys):
    captured: dict = {}
    with patch.object(httpx.Client, "post", _fake_post(captured)):
        code = share_mod.run(seeded)
    assert code == 0

    body = captured["body"]
    assert captured["url"] == "https://api.usewitness.dev/upload"

    # Trace task scrubbed
    assert "sk-ant" not in body["trace"]["task"]
    assert "[REDACTED]" in body["trace"]["task"]

    # Step URL password query arg redacted
    assert "hunter2" not in body["steps"][0]["url"]

    # LLM prompt bearer token / key redacted
    prompt = body["llm_calls"][0]["prompt"]
    assert "sk-ant" not in prompt
    assert "verysecretkey" not in prompt

    # HTML blob redacted (email + key gone)
    blob_b64 = body["blobs"]["doms/0000_dom_after.html"]
    decoded = base64.b64decode(blob_b64).decode("utf-8")
    assert "ada@example.com" not in decoded
    assert "sk-ant" not in decoded

    # PNG blob passes through untouched
    shot_b64 = body["blobs"]["screenshots/0000_shot_after.png"]
    assert base64.b64decode(shot_b64).startswith(b"\x89PNG")


def test_share_records_in_jsonl(seeded):
    with patch.object(httpx.Client, "post", _fake_post({})):
        share_mod.run(seeded)
    log = storage.BASE_DIR / "shares.jsonl"
    assert log.exists()
    line = log.read_text(encoding="utf-8").strip().splitlines()[0]
    entry = json.loads(line)
    assert entry["local_trace_id"] == seeded
    assert entry["deletion_token"] == "delete-me-xyz"
    assert entry["url"].startswith("https://usewitness.dev/t/")


def test_share_missing_trace_returns_error(tmp_path):
    _point_storage(tmp_path)
    storage.init_db()
    config_mod.record_share_consent()
    assert share_mod.run("doesnotexist") == 1


def test_endpoint_override_env(seeded, monkeypatch):
    monkeypatch.setenv("WITNESS_UPLOAD_ENDPOINT", "https://my-self-host.example/api")
    captured: dict = {}
    with patch.object(httpx.Client, "post", _fake_post(captured)):
        share_mod.run(seeded)
    assert captured["url"] == "https://my-self-host.example/api/upload"


def test_endpoint_override_flag(seeded):
    captured: dict = {}
    with patch.object(httpx.Client, "post", _fake_post(captured)):
        share_mod.run(seeded, endpoint="https://other.dev")
    assert captured["url"] == "https://other.dev/upload"


def test_consent_prompt_on_first_share(tmp_path, monkeypatch):
    _point_storage(tmp_path)
    storage.init_db()
    # Set up a minimal trace
    tdir = storage.trace_dir("c0n5ent12345")
    with storage.get_session() as s:
        s.add(storage.Trace(
            id="c0n5ent12345", task="t",
            started_at=datetime.now(timezone.utc), status="success", step_count=0,
        ))
        s.commit()

    # consent is false (default); answer "n" and verify we bail out cleanly
    monkeypatch.setattr("builtins.input", lambda: "n")
    code = share_mod.run("c0n5ent12345")
    assert code == 3  # user declined
