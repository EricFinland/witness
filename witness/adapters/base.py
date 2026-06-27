"""Capture adapter protocol.

A capture adapter knows how to recognize a specific agent framework and attach
Witness tracing to one of its agents. The SDK keeps a registry of adapters and,
on ``witness.instrument(agent)``, picks the first adapter whose ``detect`` call
returns True and delegates to its ``instrument``.

Each adapter is a small class exposing:

* ``name: str`` -- a short identifier, used only for logging.
* ``def detect(self, agent) -> bool`` -- cheap, side-effect-free duck-typing
  check that returns True when this adapter knows how to handle ``agent``.
* ``def instrument(self, agent) -> agent`` -- attach tracing and return the same
  agent for chaining. Must be idempotent (a second call is a no-op).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CaptureAdapter(Protocol):
    """Structural type every adapter satisfies.

    Adapters do not need to subclass anything; matching this shape is enough.
    The concrete adapters in this package subclass ``BaseAdapter`` for shared
    boilerplate, but third-party adapters may implement the protocol directly.
    """

    name: str

    def detect(self, agent: Any) -> bool: ...

    def instrument(self, agent: Any) -> Any: ...


class BaseAdapter:
    """Convenience base with a ``name`` and a not-implemented stub.

    Subclasses override ``detect`` and ``instrument``.
    """

    name: str = "base"

    def detect(self, agent: Any) -> bool:  # pragma: no cover - overridden
        return False

    def instrument(self, agent: Any) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError(
            f"adapter {self.name!r} does not implement instrument()"
        )
