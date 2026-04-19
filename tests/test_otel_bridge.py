"""Unit tests for the OTEL bridge — specifically the span → snapshot extraction."""

from witness.otel_bridge import (
    _join_indexed,
    _maybe_extract_llm,
    drain_for_step,
    set_active_step,
    reset_active_step,
)


class FakeSpan:
    def __init__(self, attributes, start=0, end=1_500_000):
        self.attributes = attributes
        self.start_time = start
        self.end_time = end


def test_non_llm_span_ignored():
    span = FakeSpan({"http.method": "GET"})
    assert _maybe_extract_llm(span) is None


def test_gen_ai_attrs_extracted():
    span = FakeSpan(
        {
            "gen_ai.request.model": "claude-sonnet-4-5",
            "gen_ai.usage.input_tokens": 120,
            "gen_ai.usage.output_tokens": 45,
            "gen_ai.prompt.0.role": "user",
            "gen_ai.prompt.0.content": "hi",
            "gen_ai.completion.0.role": "assistant",
            "gen_ai.completion.0.content": "hello!",
        },
        start=0,
        end=2_000_000,
    )
    snap = _maybe_extract_llm(span)
    assert snap is not None
    assert snap.model == "claude-sonnet-4-5"
    assert snap.prompt_tokens == 120
    assert snap.completion_tokens == 45
    assert "hi" in snap.prompt
    assert "hello!" in snap.response
    assert snap.latency_ms == 2


def test_llm_attrs_fallback():
    span = FakeSpan(
        {
            "llm.request.model": "gpt-4o",
            "llm.usage.prompt_tokens": 10,
            "llm.usage.completion_tokens": 20,
        }
    )
    snap = _maybe_extract_llm(span)
    assert snap is not None
    assert snap.model == "gpt-4o"
    assert snap.prompt_tokens == 10
    assert snap.completion_tokens == 20


def test_join_indexed_stops_at_gap():
    attrs = {
        "p.0.role": "user",
        "p.0.content": "first",
        "p.1.role": "assistant",
        "p.1.content": "second",
    }
    out = _join_indexed(attrs, ["p"])
    assert "first" in out
    assert "second" in out


def test_drain_empties_buffer():
    token = set_active_step(999)
    try:
        # Nothing buffered for this step yet.
        assert drain_for_step(999) == []
    finally:
        reset_active_step(token)
