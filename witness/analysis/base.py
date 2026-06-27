"""Shared types and the analyzer protocol.

Each analyzer is a plain module under ``witness.analysis`` that exposes two
attributes:

* ``KIND: str`` -- one of the ``KIND_*`` constants defined here. It labels the
  category of findings the analyzer produces.
* ``def analyze(ctx: AnalysisContext) -> list[storage.Finding]`` -- inspects the
  captured trace and returns zero or more findings.

Returned ``Finding`` objects must be plain instances (``created_at`` set, NOT
added to any session). The runner is responsible for persisting them, so an
analyzer never touches the database directly. An analyzer must not mutate the
context or the ORM rows it is handed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from witness import storage

# Finding kinds. These double as the ``kind`` column value on stored Findings.
KIND_PROMPT_INJECTION = "prompt_injection"
KIND_TRAJECTORY = "trajectory"
KIND_EXFILTRATION = "exfiltration"
KIND_OUTCOME = "outcome"


@dataclass
class AnalysisContext:
    """Everything an analyzer needs to inspect one trace.

    Attributes:
        trace: The trace row under analysis.
        steps: Steps for the trace, ordered by ``idx``.
        llm_calls_by_step: LLM calls grouped by ``step.id``.
        load_dom: ``(step, which)`` -> HTML text or None, where ``which`` is
            either ``"before"`` or ``"after"``. Reads the captured DOM blob from
            disk lazily so analyzers only pay for what they touch.
    """

    trace: storage.Trace
    steps: list[storage.Step]
    llm_calls_by_step: dict[int, list[storage.LLMCall]]
    load_dom: Callable[[storage.Step, str], Optional[str]]
