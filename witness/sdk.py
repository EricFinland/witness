"""`witness.instrument(agent)` — wrap a Browser Use Agent to record every step."""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from witness import otel_bridge, storage
from witness.pricing import calculate_cost

log = logging.getLogger("witness")


def instrument(agent: Any) -> Any:
    """Attach Witness tracing to a browser_use.Agent.

    Monkey-patches `agent.step` to capture before/after snapshots and LLM calls
    per step. Safe to call once per agent. Returns the same agent for chaining.
    """
    if getattr(agent, "_witness_trace_id", None):
        return agent  # already instrumented

    storage.init_db()
    otel_bridge.init_tracing(app_name="witness")

    trace_id = uuid.uuid4().hex[:12]
    task = str(getattr(agent, "task", "") or "untask")
    model_name = _detect_model_name(agent)

    trace_dir = storage.trace_dir(trace_id)
    started_at = datetime.now(timezone.utc)

    with storage.get_session() as s:
        trace = storage.Trace(
            id=trace_id,
            task=task[:2000],
            model=model_name,
            started_at=started_at,
            status="running",
        )
        s.add(trace)
        s.commit()

    agent._witness_trace_id = trace_id
    agent._witness_trace_dir = trace_dir
    agent._witness_step_counter = 0

    original_step = agent.step

    @wraps(original_step)
    async def wrapped_step(*args, **kwargs):
        idx: int = agent._witness_step_counter
        agent._witness_step_counter += 1

        page = await _safe_get_page(agent)
        url_before = await _safe_get_url(page)
        shot_before = await _safe_screenshot(page)
        dom_before = await _safe_get_html(page)

        # Create the step row early so we have an id to buffer LLM spans against.
        with storage.get_session() as s:
            step = storage.Step(
                trace_id=trace_id,
                idx=idx,
                action_type="pending",
                action_payload={},
                ts=datetime.now(timezone.utc),
                url=url_before,
            )
            s.add(step)
            s.commit()
            s.refresh(step)
            step_id = step.id
        assert step_id is not None

        token = otel_bridge.set_active_step(step_id)
        t0 = time.perf_counter()
        err: str | None = None
        try:
            await original_step(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            err = repr(e)
            raise
        finally:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            otel_bridge.reset_active_step(token)

            # Re-fetch page because the action may have navigated.
            page_after = await _safe_get_page(agent)
            url_after = await _safe_get_url(page_after) or url_before
            shot_after = await _safe_screenshot(page_after)
            dom_after = await _safe_get_html(page_after)

            action_type, action_payload = _extract_action(agent)
            paths = _persist_blobs(
                trace_dir, idx, shot_before, shot_after, dom_before, dom_after
            )

            llm_snaps = otel_bridge.drain_for_step(step_id)

            with storage.get_session() as s:
                step_row = s.get(storage.Step, step_id)
                if step_row is not None:
                    step_row.action_type = action_type
                    step_row.action_payload = action_payload
                    step_row.latency_ms = latency_ms
                    step_row.error = err
                    step_row.url = url_after
                    step_row.dom_before_path = paths.get("dom_before")
                    step_row.dom_after_path = paths.get("dom_after")
                    step_row.shot_before_path = paths.get("shot_before")
                    step_row.shot_after_path = paths.get("shot_after")
                    s.add(step_row)

                total_cost = 0.0
                total_tokens = 0
                for snap in llm_snaps:
                    cost = calculate_cost(
                        snap.model, snap.prompt_tokens, snap.completion_tokens
                    )
                    total_cost += cost
                    total_tokens += snap.prompt_tokens + snap.completion_tokens
                    s.add(
                        storage.LLMCall(
                            step_id=step_id,
                            model=snap.model,
                            prompt_tokens=snap.prompt_tokens,
                            completion_tokens=snap.completion_tokens,
                            cost_usd=cost,
                            latency_ms=snap.latency_ms,
                            prompt=snap.prompt,
                            response=snap.response,
                            ts=datetime.now(timezone.utc),
                        )
                    )

                trace_row = s.get(storage.Trace, trace_id)
                if trace_row is not None:
                    trace_row.step_count = idx + 1
                    trace_row.total_latency_ms += latency_ms
                    trace_row.total_cost_usd += total_cost
                    trace_row.total_tokens += total_tokens
                    s.add(trace_row)
                s.commit()

    agent.step = wrapped_step

    # Wrap run() so we can finalize the trace (success/error + ended_at).
    if hasattr(agent, "run"):
        original_run = agent.run

        @wraps(original_run)
        async def wrapped_run(*args, **kwargs):
            status = "success"
            run_err: str | None = None
            try:
                return await original_run(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                status = "error"
                run_err = repr(e)
                raise
            finally:
                with storage.get_session() as s:
                    t = s.get(storage.Trace, trace_id)
                    if t is not None:
                        t.ended_at = datetime.now(timezone.utc)
                        t.status = status
                        t.error = run_err
                        s.add(t)
                        s.commit()

        agent.run = wrapped_run

    log.info("witness: instrumented trace=%s task=%r", trace_id, task[:80])
    return agent


# --- helpers -----------------------------------------------------------------


def _detect_model_name(agent: Any) -> str | None:
    llm = getattr(agent, "llm", None)
    if llm is None:
        return None
    for attr in ("model", "model_name", "name"):
        v = getattr(llm, attr, None)
        if v:
            return str(v)
    return type(llm).__name__


async def _safe_get_page(agent: Any):
    try:
        session = getattr(agent, "browser_session", None)
        if session is None:
            return None
        getter = getattr(session, "get_current_page", None)
        if getter is None:
            return None
        return await getter()
    except Exception as e:  # noqa: BLE001
        log.debug("witness: get_current_page failed: %r", e)
        return None


async def _safe_get_url(page) -> str | None:
    if page is None:
        return None
    try:
        return await page.get_url()
    except Exception:  # noqa: BLE001
        return None


async def _safe_screenshot(page) -> bytes | None:
    if page is None:
        return None
    try:
        data = await page.screenshot(format="png")
    except Exception as e:  # noqa: BLE001
        log.debug("witness: screenshot failed: %r", e)
        return None
    # Browser Use actor returns base64 str; some code paths may return bytes.
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        try:
            return base64.b64decode(data)
        except Exception:  # noqa: BLE001
            return data.encode("utf-8", errors="ignore")
    return None


async def _safe_get_html(page) -> str | None:
    if page is None:
        return None
    try:
        result = await page.evaluate("document.documentElement.outerHTML")
    except Exception as e:  # noqa: BLE001
        log.debug("witness: evaluate(outerHTML) failed: %r", e)
        return None
    if isinstance(result, str):
        return result
    # Some versions wrap in {"value": "..."} or similar.
    if isinstance(result, dict):
        for k in ("value", "result", "html"):
            v = result.get(k)
            if isinstance(v, str):
                return v
    return str(result) if result is not None else None


def _persist_blobs(
    trace_dir,
    idx: int,
    shot_before: bytes | None,
    shot_after: bytes | None,
    dom_before: str | None,
    dom_after: str | None,
) -> dict[str, str]:
    paths: dict[str, str] = {}
    for name, data, ext, sub in [
        ("shot_before", shot_before, "png", "screenshots"),
        ("shot_after", shot_after, "png", "screenshots"),
        ("dom_before", dom_before, "html", "doms"),
        ("dom_after", dom_after, "html", "doms"),
    ]:
        if data is None:
            continue
        rel = f"{sub}/{idx:04d}_{name}.{ext}"
        full = trace_dir / rel
        if isinstance(data, bytes):
            full.write_bytes(data)
        else:
            full.write_text(data, encoding="utf-8")
        paths[name] = rel
    return paths


def _extract_action(agent: Any) -> tuple[str, dict]:
    """Pull the most recent action from agent.state.last_model_output.

    Browser Use stores the AgentOutput with `action: list[ActionModel]`. Each
    ActionModel has exactly one field set (e.g. click_element_by_index, go_to_url).
    We take the first one as the primary action_type.
    """
    try:
        state = getattr(agent, "state", None)
        model_output = getattr(state, "last_model_output", None) if state else None
        actions = getattr(model_output, "action", None) if model_output else None
        if not actions:
            return "unknown", {}
        first = actions[0]
        dump = first.model_dump(exclude_none=True) if hasattr(first, "model_dump") else {}
        if not dump:
            return "unknown", {}
        # Exactly one key per ActionModel.
        action_type = next(iter(dump.keys()))
        payload = dump[action_type] if isinstance(dump[action_type], dict) else {"value": dump[action_type]}
        # Sanitize non-JSON-serializable values.
        return action_type, json.loads(json.dumps(payload, default=str))
    except Exception as e:  # noqa: BLE001
        log.debug("witness: action extract failed: %r", e)
        return "unknown", {}
