"""Playwright capture adapter (stub).

Demonstrates the adapter pattern for a second framework. Detection recognizes a
raw Playwright ``Page`` (or an object exposing the Playwright page surface), but
``instrument`` is not implemented yet: a Playwright script has no single ``step``
seam to wrap the way a browser_use Agent does, so wiring it up needs a deliberate
design (hooking ``page.goto`` / ``page.click`` / etc.). Until then this adapter
fails loudly so it never silently swallows traces.
"""

from __future__ import annotations

import logging
from typing import Any

from witness.adapters.base import BaseAdapter

log = logging.getLogger("witness")


class PlaywrightAdapter(BaseAdapter):
    """Recognize a raw Playwright page; instrumentation is not yet implemented."""

    name = "playwright"

    def detect(self, agent: Any) -> bool:
        """True for a Playwright ``Page``-like object.

        A Playwright Page exposes ``goto`` and ``click`` callables and a
        ``context`` attribute, but (unlike a browser_use Agent) has no ``task``.
        The ``task`` exclusion keeps this from shadowing the browser_use adapter.
        """
        if agent is None or hasattr(agent, "task"):
            return False
        has_nav = callable(getattr(agent, "goto", None))
        has_click = callable(getattr(agent, "click", None))
        has_context = hasattr(agent, "context")
        return has_nav and has_click and has_context

    def instrument(self, agent: Any) -> Any:
        """Not implemented yet. See module docstring."""
        raise NotImplementedError(
            "PlaywrightAdapter.instrument is not implemented yet. Witness "
            "currently traces browser_use agents; raw Playwright support is "
            "planned. Wrap your browser_use.Agent with witness.instrument "
            "instead."
        )
