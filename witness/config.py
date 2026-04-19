"""Runtime config.

Witness is local-first. The only configurable thing today is `telemetry`,
which is **off by default** and stays off unless the user explicitly opts in.
The infrastructure is here so that (a) privacy-conscious devs see an explicit
opt-out exists, and (b) if we ever ship telemetry, the default is already no.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from witness import storage


@dataclass(frozen=True)
class Config:
    telemetry: bool = False
    """When True, Witness may phone home with anonymous usage counts.
    Off by default. Nothing in v0 honors this yet — the flag is wired in
    first so there's always a visible opt-out."""


_cached: Config | None = None


def config_path() -> Path:
    return storage.BASE_DIR / "config.toml"


def load() -> Config:
    """Read ~/.witness/config.toml if present; otherwise return defaults."""
    global _cached
    if _cached is not None:
        return _cached

    path = config_path()
    if not path.exists():
        _cached = Config()
        return _cached

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        _cached = Config()
        return _cached

    _cached = Config(
        telemetry=bool(data.get("telemetry", False)),
    )
    return _cached


def write_default_if_missing() -> Path:
    """Create a commented default config.toml if none exists. Returns its path."""
    path = config_path()
    if path.exists():
        return path
    storage._ensure_dirs()  # make sure ~/.witness/ exists
    path.write_text(
        (
            "# Witness config. Set telemetry = true to opt in to anonymous\n"
            "# usage counts. Witness is local-first; this is off by default\n"
            "# and nothing in v0 honors it yet.\n"
            "telemetry = false\n"
        ),
        encoding="utf-8",
    )
    return path


def reset_cache() -> None:
    """Test hook — drop cached config."""
    global _cached
    _cached = None
