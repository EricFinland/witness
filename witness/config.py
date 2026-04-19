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

    share_consent: bool = False
    """Set automatically the first time the user answers 'y' to the share
    warning. Prevents re-prompting on every `witness share` invocation."""

    share_endpoint: str = "https://api.usewitness.dev"
    """Upload target for `witness share`. Override with WITNESS_UPLOAD_ENDPOINT
    env var or by editing config.toml for self-hosted deployments."""


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
        share_consent=bool(data.get("share_consent", False)),
        share_endpoint=str(
            data.get("share_endpoint", Config.__dataclass_fields__["share_endpoint"].default)
        ),
    )
    return _cached


def record_share_consent() -> None:
    """Persist `share_consent = true` so we don't prompt again."""
    path = config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if "share_consent" in text:
        # Toggle existing line.
        import re as _re
        text = _re.sub(r"share_consent\s*=\s*\w+", "share_consent = true", text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "share_consent = true\n"
    storage._ensure_dirs()
    path.write_text(text, encoding="utf-8")
    reset_cache()


def write_default_if_missing() -> Path:
    """Create a commented default config.toml if none exists. Returns its path."""
    path = config_path()
    if path.exists():
        return path
    storage._ensure_dirs()  # make sure ~/.witness/ exists
    path.write_text(
        (
            "# Witness config. Witness is local-first by default.\n"
            "\n"
            "# Anonymous usage counts. Off by default; nothing in v0 reads this.\n"
            "telemetry = false\n"
            "\n"
            "# Set to true automatically the first time you confirm `witness share`.\n"
            "# Leaving it false re-prompts on the next share; that's fine.\n"
            "share_consent = false\n"
            "\n"
            "# Upload target for `witness share`. Override here for self-hosted\n"
            "# or via the WITNESS_UPLOAD_ENDPOINT env var.\n"
            '# share_endpoint = "https://api.usewitness.dev"\n'
        ),
        encoding="utf-8",
    )
    return path


def reset_cache() -> None:
    """Test hook — drop cached config."""
    global _cached
    _cached = None
