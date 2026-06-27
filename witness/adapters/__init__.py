"""Capture adapters.

Each adapter recognizes a specific agent framework and knows how to attach
Witness tracing to one of its agents. ``ADAPTERS`` is the ordered registry the
SDK dispatcher walks; the first adapter whose ``detect`` returns True wins, so
more specific adapters should come before more general ones.
"""

from __future__ import annotations

from typing import Any

from witness.adapters.base import BaseAdapter, CaptureAdapter
from witness.adapters.browser_use import BrowserUseAdapter
from witness.adapters.playwright import PlaywrightAdapter

# Registry, walked in order by the SDK dispatcher.
ADAPTERS: list[CaptureAdapter] = [
    BrowserUseAdapter(),
    PlaywrightAdapter(),
]


def find_adapter(agent: Any) -> CaptureAdapter | None:
    """Return the first registered adapter whose ``detect`` matches ``agent``."""
    for adapter in ADAPTERS:
        try:
            if adapter.detect(agent):
                return adapter
        except Exception:  # noqa: BLE001 - a misbehaving adapter must not break dispatch
            continue
    return None


__all__ = [
    "ADAPTERS",
    "BaseAdapter",
    "CaptureAdapter",
    "BrowserUseAdapter",
    "PlaywrightAdapter",
    "find_adapter",
]
