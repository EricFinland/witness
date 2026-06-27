"""Browser Use capture adapter.

Holds the full instrumentation logic for ``browser_use.Agent``. The SDK's
``witness.instrument`` is a thin dispatcher that delegates here when this
adapter's ``detect`` matches.

Beyond the DOM/screenshot capture, this adapter best-effort captures extra
depth per step (network requests, console / JS errors, and the accessibility
tree) via the page's CDP session when the running browser_use / playwright
version exposes it. Every one of those captures is fully guarded so a missing
capability never breaks a step. The extra blobs are written next to the DOM and
their relative paths are referenced in the step's ``action_payload`` under a
``_witness_capture`` key (storage schema is owned by another component, so no
new columns are added here).
"""

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
from witness.adapters.base import BaseAdapter
from witness.pricing import calculate_cost
from witness.schema import SCHEMA_VERSION, StepRecord, TraceRecord

log = logging.getLogger("witness")


class BrowserUseAdapter(BaseAdapter):
    """Recognize and instrument a ``browser_use.Agent``."""

    name = "browser_use"

    def detect(self, agent: Any) -> bool:
        """Duck-type a Browser Use agent.

        A Browser Use Agent exposes a ``task`` attribute plus async ``step`` and
        ``run`` callables. We avoid importing browser_use so detection is cheap
        and works even when the package is absent.
        """
        if agent is None:
            return False
        if not hasattr(agent, "task"):
            return False
        return callable(getattr(agent, "step", None)) and callable(
            getattr(agent, "run", None)
        )

    def instrument(self, agent: Any) -> Any:
        """Attach Witness tracing to a browser_use.Agent.

        Monkey-patches ``agent.step`` to capture before/after snapshots and LLM
        calls per step, and wraps ``agent.run`` to finalize the trace. Safe to
        call once per agent; returns the same agent for chaining.
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
            # Validate the canonical record builds (schema-first capture).
            try:
                TraceRecord.from_orm_obj(trace)
            except Exception as e:  # noqa: BLE001
                log.debug("witness: TraceRecord build failed: %r", e)

        agent._witness_trace_id = trace_id
        agent._witness_trace_dir = trace_dir
        agent._witness_step_counter = 0
        agent._witness_schema_version = SCHEMA_VERSION

        original_step = agent.step

        @wraps(original_step)
        async def wrapped_step(*args, **kwargs):
            idx: int = agent._witness_step_counter
            agent._witness_step_counter += 1

            page = await _safe_get_page(agent)
            url_before = await _safe_get_url(page)
            shot_before = await _safe_screenshot(page)
            dom_before = await _safe_get_html(page)

            # Start best-effort depth capture (network + console) for this step.
            depth = _DepthCapture(page)
            await depth.start()

            # Create the step row early so we have an id to buffer LLM spans.
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

                # Collect depth artifacts (best-effort, fully guarded), then
                # detach the listeners so they do not accumulate on the
                # long-lived page across steps.
                network = await depth.collect_network()
                console = await depth.collect_console()
                await depth.stop()
                axtree = await _safe_axtree(page_after or page)

                action_type, action_payload = _extract_action(agent)
                paths = _persist_blobs(
                    trace_dir, idx, shot_before, shot_after, dom_before, dom_after
                )
                depth_paths = _persist_depth_blobs(
                    trace_dir, idx, network, console, axtree
                )
                if depth_paths:
                    # Reference depth blobs on the step without new columns.
                    action_payload = dict(action_payload)
                    action_payload["_witness_capture"] = {
                        "schema_version": SCHEMA_VERSION,
                        **depth_paths,
                    }

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
                        try:
                            StepRecord.from_orm_obj(step_row)
                        except Exception as e:  # noqa: BLE001
                            log.debug("witness: StepRecord build failed: %r", e)

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


# --- model + page helpers ----------------------------------------------------


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
        # browser-use >=0.11 rejects bare JS; it requires a `(...args) => ...`
        # arrow function. Playwright accepts both forms, so this works for either.
        result = await page.evaluate("() => document.documentElement.outerHTML")
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


# --- capture depth (B2/B3): network, console, accessibility tree -------------


class _DepthCapture:
    """Best-effort per-step capture of network requests and console messages.

    Hooks into the underlying Playwright page event API when present. Everything
    is guarded: if the page object does not expose ``on`` / events (older or
    different browser_use versions), the capture is simply empty and the step
    proceeds normally.
    """

    def __init__(self, page: Any):
        self._page = page
        self._network: list[dict] = []
        self._console: list[dict] = []
        self._started = False
        # The resolved underlying page and the exact (event, callback) pairs we
        # registered, so start() can be undone by stop(). Without this the
        # listeners would accumulate on the long-lived page across every step.
        self._pw: Any = None
        self._listeners: list[tuple[str, Any]] = []

    @staticmethod
    def _underlying(page: Any) -> Any:
        """Find an object that exposes a Playwright-style ``on`` listener.

        browser_use wraps the real Playwright page; try common attribute names
        before falling back to the page itself.
        """
        if page is None:
            return None
        for attr in ("_page", "page", "playwright_page", "_playwright_page"):
            inner = getattr(page, attr, None)
            if inner is not None and hasattr(inner, "on"):
                return inner
        if hasattr(page, "on"):
            return page
        return None

    async def start(self) -> None:
        pw = self._underlying(self._page)
        if pw is None:
            return
        handlers = (
            ("request", self._on_request),
            ("requestfailed", self._on_request_failed),
            ("console", self._on_console),
            ("pageerror", self._on_page_error),
        )
        try:
            for event, cb in handlers:
                pw.on(event, cb)
                self._listeners.append((event, cb))
            self._pw = pw
            self._started = True
        except Exception as e:  # noqa: BLE001
            log.debug("witness: depth capture start failed: %r", e)
            # Detach anything that did register before the failure so a partial
            # start does not leak listeners.
            self._detach()

    def _detach(self) -> None:
        """Remove every listener this instance registered, best-effort.

        Playwright's Python page exposes ``remove_listener``; older variants use
        ``off``. Each removal is guarded so a missing method or a stale page
        never breaks the step.
        """
        pw = self._pw
        if pw is None:
            self._listeners.clear()
            return
        remover = getattr(pw, "remove_listener", None) or getattr(pw, "off", None)
        if callable(remover):
            for event, cb in self._listeners:
                try:
                    remover(event, cb)
                except Exception:  # noqa: BLE001
                    pass
        self._listeners.clear()
        self._pw = None
        self._started = False

    async def stop(self) -> None:
        """Detach all registered listeners. Safe to call once after collection."""
        try:
            self._detach()
        except Exception as e:  # noqa: BLE001
            log.debug("witness: depth capture stop failed: %r", e)

    def _on_request(self, request: Any) -> None:
        try:
            self._network.append(
                {
                    "url": getattr(request, "url", None),
                    "method": getattr(request, "method", None),
                    "resource_type": getattr(request, "resource_type", None),
                    "failed": False,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_request_failed(self, request: Any) -> None:
        try:
            failure = getattr(request, "failure", None)
            self._network.append(
                {
                    "url": getattr(request, "url", None),
                    "method": getattr(request, "method", None),
                    "resource_type": getattr(request, "resource_type", None),
                    "failed": True,
                    "failure": str(failure) if failure else None,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_console(self, msg: Any) -> None:
        try:
            self._console.append(
                {
                    "type": getattr(msg, "type", None),
                    "text": getattr(msg, "text", None),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_page_error(self, error: Any) -> None:
        try:
            self._console.append({"type": "pageerror", "text": str(error)})
        except Exception:  # noqa: BLE001
            pass

    async def collect_network(self) -> list[dict] | None:
        if not self._started:
            return None
        return list(self._network)

    async def collect_console(self) -> list[dict] | None:
        if not self._started:
            return None
        return list(self._console)


async def _safe_axtree(page: Any) -> Any:
    """Snapshot the accessibility tree, best-effort.

    Prefers ``page.accessibility.snapshot()``; falls back to the CDP
    Accessibility domain (``Accessibility.getFullAXTree``) where a CDP session is
    reachable. Returns None when neither path is available.
    """
    if page is None:
        return None

    inner = _DepthCapture._underlying(page) or page

    # Path 1: Playwright accessibility API.
    accessibility = getattr(inner, "accessibility", None)
    if accessibility is not None:
        snapshot = getattr(accessibility, "snapshot", None)
        if callable(snapshot):
            try:
                tree = await snapshot()
                if tree is not None:
                    return tree
            except Exception as e:  # noqa: BLE001
                log.debug("witness: accessibility.snapshot failed: %r", e)

    # Path 2: CDP Accessibility domain.
    try:
        context = getattr(inner, "context", None)
        new_cdp = getattr(context, "new_cdp_session", None) if context else None
        if callable(new_cdp):
            session = await new_cdp(inner)
            try:
                await session.send("Accessibility.enable")
                result = await session.send("Accessibility.getFullAXTree")
                return result
            finally:
                detach = getattr(session, "detach", None)
                if callable(detach):
                    try:
                        await detach()
                    except Exception:  # noqa: BLE001
                        pass
    except Exception as e:  # noqa: BLE001
        log.debug("witness: CDP accessibility capture failed: %r", e)

    return None


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


def _persist_depth_blobs(
    trace_dir,
    idx: int,
    network: Any,
    console: Any,
    axtree: Any,
) -> dict[str, str]:
    """Write network / console / accessibility blobs next to the DOM.

    Lives under a ``depth`` subdir to avoid colliding with DOM/screenshot files.
    Returns a mapping of capture name -> relative path for the ones that were
    written. Anything that is None (capability unavailable) is skipped.
    """
    paths: dict[str, str] = {}
    items = [
        ("network", network),
        ("console", console),
        ("axtree", axtree),
    ]
    if not any(data is not None for _, data in items):
        return paths

    depth_dir = trace_dir / "depth"
    try:
        depth_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        log.debug("witness: could not create depth dir: %r", e)
        return paths

    for name, data in items:
        if data is None:
            continue
        rel = f"depth/{idx:04d}_{name}.json"
        full = trace_dir / rel
        try:
            full.write_text(
                json.dumps(data, default=str, ensure_ascii=False), encoding="utf-8"
            )
            paths[name] = rel
        except Exception as e:  # noqa: BLE001
            log.debug("witness: persist depth blob %s failed: %r", name, e)
    return paths


def _extract_action(agent: Any) -> tuple[str, dict]:
    """Pull the most recent action from agent.state.last_model_output.

    Browser Use stores the AgentOutput with `action: list[ActionModel]`. Each
    ActionModel has exactly one field set (e.g. click_element_by_index,
    go_to_url). We take the first one as the primary action_type.
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
        payload = (
            dump[action_type]
            if isinstance(dump[action_type], dict)
            else {"value": dump[action_type]}
        )
        # Sanitize non-JSON-serializable values.
        return action_type, json.loads(json.dumps(payload, default=str))
    except Exception as e:  # noqa: BLE001
        log.debug("witness: action extract failed: %r", e)
        return "unknown", {}
