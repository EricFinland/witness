"""Core diff logic for `witness diff` — no Rich/Typer imports."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import select

from witness import storage


@dataclass
class StepPair:
    kind: str  # "equal" | "insert" | "delete" | "replace"
    step_a: Optional[storage.Step]   # None for insert
    step_b: Optional[storage.Step]   # None for delete
    cost_a: float = 0.0
    cost_b: float = 0.0
    payload_changed: bool = False    # True when kind=="equal" but payloads differ


@dataclass
class DiffResult:
    trace_a: storage.Trace
    trace_b: storage.Trace
    pairs: list[StepPair] = field(default_factory=list)

    @property
    def cost_delta(self) -> float:
        return self.trace_b.total_cost_usd - self.trace_a.total_cost_usd

    @property
    def token_delta(self) -> int:
        return self.trace_b.total_tokens - self.trace_a.total_tokens

    @property
    def latency_delta_ms(self) -> int:
        return self.trace_b.total_latency_ms - self.trace_a.total_latency_ms

    @property
    def step_count_delta(self) -> int:
        return self.trace_b.step_count - self.trace_a.step_count


def diff_traces(trace_a_id: str, trace_b_id: str) -> DiffResult:
    """Load two traces from storage and compute a step-level diff.

    Raises LookupError if either trace_id is not found.
    """
    with storage.get_session() as s:
        trace_a = s.get(storage.Trace, trace_a_id)
        trace_b = s.get(storage.Trace, trace_b_id)
        if trace_a is None:
            raise LookupError(f"Trace {trace_a_id!r} not found")
        if trace_b is None:
            raise LookupError(f"Trace {trace_b_id!r} not found")

        steps_a = list(s.exec(
            select(storage.Step)
            .where(storage.Step.trace_id == trace_a_id)
            .order_by(storage.Step.idx)
        ).all())
        steps_b = list(s.exec(
            select(storage.Step)
            .where(storage.Step.trace_id == trace_b_id)
            .order_by(storage.Step.idx)
        ).all())

        # Collect LLM call costs keyed by step_id.
        all_step_ids = [st.id for st in steps_a + steps_b if st.id is not None]
        cost_by_step: dict[int, float] = {}
        if all_step_ids:
            calls = s.exec(
                select(storage.LLMCall).where(storage.LLMCall.step_id.in_(all_step_ids))
            ).all()
            for c in calls:
                cost_by_step[c.step_id] = cost_by_step.get(c.step_id, 0.0) + c.cost_usd

    types_a = [st.action_type for st in steps_a]
    types_b = [st.action_type for st in steps_b]
    matcher = difflib.SequenceMatcher(None, types_a, types_b, autojunk=False)

    pairs: list[StepPair] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for ia, ib in zip(range(i1, i2), range(j1, j2)):
                sa, sb = steps_a[ia], steps_b[ib]
                pairs.append(StepPair(
                    kind="equal",
                    step_a=sa,
                    step_b=sb,
                    cost_a=cost_by_step.get(sa.id or -1, 0.0),
                    cost_b=cost_by_step.get(sb.id or -1, 0.0),
                    payload_changed=(sa.action_payload != sb.action_payload),
                ))
        elif tag == "replace":
            slice_a, slice_b = steps_a[i1:i2], steps_b[j1:j2]
            for sa, sb in zip(slice_a, slice_b):
                pairs.append(StepPair(
                    kind="replace",
                    step_a=sa,
                    step_b=sb,
                    cost_a=cost_by_step.get(sa.id or -1, 0.0),
                    cost_b=cost_by_step.get(sb.id or -1, 0.0),
                ))
            for sa in slice_a[len(slice_b):]:
                pairs.append(StepPair(
                    kind="delete",
                    step_a=sa, step_b=None,
                    cost_a=cost_by_step.get(sa.id or -1, 0.0),
                ))
            for sb in slice_b[len(slice_a):]:
                pairs.append(StepPair(
                    kind="insert",
                    step_a=None, step_b=sb,
                    cost_b=cost_by_step.get(sb.id or -1, 0.0),
                ))
        elif tag == "delete":
            for sa in steps_a[i1:i2]:
                pairs.append(StepPair(
                    kind="delete",
                    step_a=sa, step_b=None,
                    cost_a=cost_by_step.get(sa.id or -1, 0.0),
                ))
        else:  # insert
            for sb in steps_b[j1:j2]:
                pairs.append(StepPair(
                    kind="insert",
                    step_a=None, step_b=sb,
                    cost_b=cost_by_step.get(sb.id or -1, 0.0),
                ))

    return DiffResult(trace_a=trace_a, trace_b=trace_b, pairs=pairs)
