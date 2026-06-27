"""Tests for opt-in OTLP export + OTel GenAI attribute normalization.

These tests are hermetic: they never require a live collector. The env var is
monkeypatched and the OTLP exporter class is mocked (or made to look absent),
so nothing is ever shipped over the network.
"""

from __future__ import annotations

import builtins
import logging

import pytest

import witness.otel_bridge as bridge
from witness.otel_bridge import (
    GenAINormalizingSpanProcessor,
    _install_otlp_export,
    genai_attributes,
)


# --- genai_attributes normalization --------------------------------------


def test_genai_normalizes_legacy_llm_attrs():
    attrs = {
        "llm.request.model": "gpt-4o",
        "llm.usage.prompt_tokens": 10,
        "llm.usage.completion_tokens": 20,
    }
    out = genai_attributes(attrs)
    assert out["gen_ai.request.model"] == "gpt-4o"
    assert out["gen_ai.usage.input_tokens"] == 10
    assert out["gen_ai.usage.output_tokens"] == 20
    assert out["gen_ai.operation.name"] == "chat"
    assert out["gen_ai.system"] == "openai"


def test_genai_infers_anthropic_system():
    out = genai_attributes({"llm.request.model": "claude-sonnet-4-5"})
    assert out["gen_ai.system"] == "anthropic"
    assert out["gen_ai.request.model"] == "claude-sonnet-4-5"


def test_genai_existing_attrs_win():
    attrs = {
        "gen_ai.request.model": "claude-opus",
        "llm.request.model": "should-be-ignored",
        "gen_ai.system": "anthropic",
    }
    out = genai_attributes(attrs)
    # The canonical gen_ai.* value is preserved, the legacy one does not override.
    assert out["gen_ai.request.model"] == "claude-opus"
    assert out["gen_ai.system"] == "anthropic"


def test_genai_non_llm_span_yields_nothing():
    out = genai_attributes({"http.method": "GET"})
    assert out == {}


class _FakeWritableSpan:
    """A ReadableSpan-like object that also exposes set_attribute."""

    def __init__(self, attributes):
        self.attributes = dict(attributes)

    def set_attribute(self, key, value):
        self.attributes[key] = value


def test_normalizing_processor_sets_attrs():
    span = _FakeWritableSpan({"llm.request.model": "claude-3-5-haiku"})
    GenAINormalizingSpanProcessor().on_end(span)
    assert span.attributes["gen_ai.request.model"] == "claude-3-5-haiku"
    assert span.attributes["gen_ai.system"] == "anthropic"
    assert span.attributes["gen_ai.operation.name"] == "chat"


def test_normalizing_processor_skips_readonly_span():
    # No set_attribute -> must not raise.
    class _ReadOnly:
        attributes = {"llm.request.model": "gpt-4o"}

    GenAINormalizingSpanProcessor().on_end(_ReadOnly())  # no exception


def test_normalizing_processor_ignores_non_llm():
    span = _FakeWritableSpan({"http.method": "GET"})
    GenAINormalizingSpanProcessor().on_end(span)
    assert "gen_ai.system" not in span.attributes


# --- _install_otlp_export ------------------------------------------------


class _FakeProvider:
    def __init__(self):
        self.processors = []

    def add_span_processor(self, processor):
        self.processors.append(processor)


def test_install_otlp_graceful_skip_when_lib_absent(monkeypatch, caplog):
    """If the OTLP exporter package is absent, skip with a warning, no crash."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if "exporter.otlp" in name:
            raise ImportError("no otlp exporter installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    provider = _FakeProvider()
    with caplog.at_level(logging.WARNING, logger="witness"):
        installed = _install_otlp_export(provider, "http://localhost:4318/v1/traces")
    assert installed is False
    assert provider.processors == []
    assert any("OTLP exporter is not" in r.message for r in caplog.records)


def test_install_otlp_installs_when_lib_present(monkeypatch):
    """When the exporter exists (mocked), install Batch + normalizing processors."""
    created = {}

    class FakeExporter:
        def __init__(self, endpoint=None, **kwargs):
            created["endpoint"] = endpoint

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "opentelemetry.exporter.otlp.proto.http.trace_exporter":
            mod = type("M", (), {"OTLPSpanExporter": FakeExporter})
            return mod
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    provider = _FakeProvider()
    installed = _install_otlp_export(provider, "http://collector:4318/v1/traces")
    assert installed is True
    assert created["endpoint"] == "http://collector:4318/v1/traces"
    # One normalizing processor + one BatchSpanProcessor were registered.
    names = [type(p).__name__ for p in provider.processors]
    assert "GenAINormalizingSpanProcessor" in names
    assert "BatchSpanProcessor" in names


# --- init_tracing wiring (env unset vs set) ------------------------------


@pytest.fixture
def reset_init(monkeypatch):
    """Reset the module init flag so init_tracing actually runs."""
    monkeypatch.setattr(bridge, "_initialized", False)
    yield
    monkeypatch.setattr(bridge, "_initialized", False)


def _stub_traceloop_and_provider(monkeypatch):
    """Stub Traceloop.init and the tracer provider so init_tracing is hermetic."""
    import sys
    import types

    # Stub traceloop.sdk so init_tracing's `from traceloop.sdk import Traceloop` works.
    traceloop_pkg = types.ModuleType("traceloop")
    sdk_mod = types.ModuleType("traceloop.sdk")

    class FakeTraceloop:
        @staticmethod
        def init(**kwargs):
            return None

    sdk_mod.Traceloop = FakeTraceloop
    traceloop_pkg.sdk = sdk_mod
    monkeypatch.setitem(sys.modules, "traceloop", traceloop_pkg)
    monkeypatch.setitem(sys.modules, "traceloop.sdk", sdk_mod)

    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    from opentelemetry import trace

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    return provider


def test_init_tracing_no_otlp_when_env_unset(monkeypatch, reset_init):
    monkeypatch.delenv("WITNESS_OTLP_ENDPOINT", raising=False)
    provider = _stub_traceloop_and_provider(monkeypatch)

    called = {"n": 0}
    monkeypatch.setattr(
        bridge,
        "_install_otlp_export",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or True,
    )

    bridge.init_tracing(app_name="test")
    assert called["n"] == 0  # default path: no OTLP installed


def test_init_tracing_installs_otlp_when_env_set(monkeypatch, reset_init):
    monkeypatch.setenv("WITNESS_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    _stub_traceloop_and_provider(monkeypatch)

    seen = {}
    monkeypatch.setattr(
        bridge,
        "_install_otlp_export",
        lambda provider, endpoint: seen.update(endpoint=endpoint) or True,
    )

    bridge.init_tracing(app_name="test")
    assert seen["endpoint"] == "http://localhost:4318/v1/traces"


def test_init_tracing_explicit_arg_wins(monkeypatch, reset_init):
    monkeypatch.delenv("WITNESS_OTLP_ENDPOINT", raising=False)
    _stub_traceloop_and_provider(monkeypatch)

    seen = {}
    monkeypatch.setattr(
        bridge,
        "_install_otlp_export",
        lambda provider, endpoint: seen.update(endpoint=endpoint) or True,
    )

    bridge.init_tracing(app_name="test", otlp_endpoint="http://explicit:4318/v1/traces")
    assert seen["endpoint"] == "http://explicit:4318/v1/traces"
