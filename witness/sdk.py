"""`witness.instrument(agent)` -- wrap a supported agent to record every step.

This module is a thin dispatcher. The actual per-framework instrumentation lives
in ``witness.adapters``. ``instrument`` picks the first registered adapter whose
``detect`` matches the agent and delegates to it. Today that means browser_use;
the adapter registry makes adding more frameworks a drop-in.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from witness import adapters

log = logging.getLogger("witness")

_API_KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY")


def _warn_if_api_key_empty_shadowed() -> None:
    """Catch a common DX footgun: an empty-string export shadows .env values
    because python-dotenv's load_dotenv() defaults to override=False."""
    for var in _API_KEY_VARS:
        if os.environ.get(var, None) == "":
            log.warning(
                "witness: %s is set to an empty string in your environment. "
                "python-dotenv defaults to override=False, so any value in a "
                ".env file will be ignored. Run `unset %s` or use "
                "`load_dotenv(override=True)`.",
                var, var,
            )
            return


def instrument(agent: Any) -> Any:
    """Attach Witness tracing to a supported agent.

    Walks the adapter registry and delegates to the first adapter whose
    ``detect(agent)`` returns True. Returns the same agent for chaining. Safe to
    call once per agent; the chosen adapter is responsible for idempotency.

    Raises:
        TypeError: if no registered adapter recognizes ``agent``.
    """
    _warn_if_api_key_empty_shadowed()

    adapter = adapters.find_adapter(agent)
    if adapter is None:
        raise TypeError(
            "witness.instrument: no capture adapter matched this object. "
            "Supported today: browser_use.Agent. Pass an instance of a "
            "supported agent framework."
        )

    log.debug("witness: dispatching to adapter %r", adapter.name)
    return adapter.instrument(agent)
