"""Config defaults, round-trip, and malformed-file tolerance."""

from __future__ import annotations

import witness.config as config_mod
import witness.storage as storage


def _point_storage(tmp_path) -> None:
    storage.BASE_DIR = tmp_path
    storage.TRACES_DIR = tmp_path / "traces"
    storage.DB_PATH = tmp_path / "witness.db"
    storage._engine = None
    config_mod.reset_cache()


def test_telemetry_off_by_default(tmp_path):
    _point_storage(tmp_path)
    assert config_mod.load().telemetry is False


def test_write_default_creates_file_once(tmp_path):
    _point_storage(tmp_path)
    p = config_mod.write_default_if_missing()
    assert p.exists()
    before = p.read_text()
    # Calling again must not overwrite — user edits survive.
    p.write_text("telemetry = true\n", encoding="utf-8")
    config_mod.reset_cache()
    config_mod.write_default_if_missing()
    assert p.read_text() == "telemetry = true\n"
    assert config_mod.load().telemetry is True
    # Sanity: original default *would* have telemetry=false.
    assert "telemetry = false" in before


def test_malformed_toml_falls_back_to_defaults(tmp_path):
    _point_storage(tmp_path)
    storage._ensure_dirs()
    config_mod.config_path().write_text("this is not valid toml {{{", encoding="utf-8")
    assert config_mod.load().telemetry is False  # no crash, default used


def test_explicit_opt_in(tmp_path):
    _point_storage(tmp_path)
    storage._ensure_dirs()
    config_mod.config_path().write_text("telemetry = true\n", encoding="utf-8")
    assert config_mod.load().telemetry is True


def test_load_is_cached(tmp_path):
    _point_storage(tmp_path)
    config_mod.load()
    # Mutating the file after first load shouldn't change the cached result.
    storage._ensure_dirs()
    config_mod.config_path().write_text("telemetry = true\n", encoding="utf-8")
    assert config_mod.load().telemetry is False  # still cached default
