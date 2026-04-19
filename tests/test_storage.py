"""Storage tests — we swap BASE_DIR / DB_PATH at module level to avoid
reloading the module, which would re-register SQLModel tables and blow up."""

from datetime import datetime, timezone
from pathlib import Path

import witness.storage as storage


def _point_storage_at(tmp: Path) -> None:
    storage.BASE_DIR = tmp
    storage.TRACES_DIR = tmp / "traces"
    storage.DB_PATH = tmp / "witness.db"
    storage._engine = None  # force re-create against new path


def test_init_creates_schema(tmp_path):
    _point_storage_at(tmp_path)
    storage.init_db()
    assert storage.DB_PATH.exists()

    with storage.get_session() as s:
        t = storage.Trace(
            id="abc123",
            task="test",
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        s.add(t)
        s.commit()

        loaded = s.get(storage.Trace, "abc123")
        assert loaded is not None
        assert loaded.task == "test"
        assert loaded.status == "running"


def test_trace_dir_creates_subdirs(tmp_path):
    _point_storage_at(tmp_path)
    d = storage.trace_dir("xyz")
    assert (d / "screenshots").is_dir()
    assert (d / "doms").is_dir()
