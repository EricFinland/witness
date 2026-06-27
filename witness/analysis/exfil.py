"""Secret and PII exfiltration detection.

Two channels are flagged:

1. The agent TYPING credential or PII shaped strings into a page field. We look
   at "input"-style actions (``input_text``, ``type``, ``fill``, etc.) and scan
   the action payload text for emails, phone numbers, credit-card numbers, API
   keys / tokens, passwords, and SSNs. The regexes are reused from
   :mod:`witness.redact` so detection stays consistent with redaction.

2. DOM-held secrets carried into a navigation or form post. If a secret or PII
   string is present in an earlier captured DOM and the same string later shows
   up in an action payload or a navigated URL query string, that is a
   carry-over: data lifted off one page and pushed somewhere else.

Evidence never stores a raw secret in full. Matches are masked the same way
redaction works (keep a short prefix, drop the rest) so a reviewer can correlate
without the trace leaking the value.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import parse_qsl, urlparse

from witness import redact, storage
from witness.analysis import base

logger = logging.getLogger("witness")

KIND = base.KIND_EXFILTRATION

# Action types that mean "the agent typed text into a field".
_TYPING_ACTIONS = {
    "input_text",
    "type",
    "fill",
    "set_value",
    "input",
    "enter_text",
    "send_keys",
}

# Action types that constitute pushing data outward (navigation / form post).
_NAV_ACTIONS = {
    "navigate",
    "goto",
    "go_to",
    "open",
    "open_url",
    "submit",
    "submit_form",
    "post",
    "request",
}

# SSN: 3-2-4 digit groups. Not in redact.py, so defined locally.
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}[ -]?(?!00)\d{2}[ -]?(?!0000)\d{4}\b")

# Password-bearing payload keys (case-insensitive substring match on key names).
_PASSWORD_KEY = re.compile(r"(?i)pass(?:word|wd|phrase)?|secret|pwd")


# --- sensitivity classification ---------------------------------------------

# (label, severity, score) keyed by detector name. Credentials and cards are the
# most damaging; a bare email is low.
_SENSITIVITY = {
    "anthropic_key": ("Anthropic API key", "critical", 0.95),
    "openai_key": ("OpenAI API key", "critical", 0.95),
    "github_token": ("GitHub token", "critical", 0.95),
    "google_key": ("Google API key", "high", 0.9),
    "aws_key": ("AWS access key", "critical", 0.95),
    "stripe_key": ("Stripe key", "critical", 0.95),
    "slack_token": ("Slack token", "high", 0.9),
    "bearer": ("Bearer token", "high", 0.9),
    "jwt": ("JWT", "high", 0.85),
    "cookie": ("Cookie header", "high", 0.85),
    "password": ("password", "high", 0.9),
    "credit_card": ("credit card number", "high", 0.9),
    "ssn": ("Social Security number", "high", 0.9),
    "phone": ("phone number", "low", 0.4),
    "email": ("email address", "low", 0.4),
}

# Detectors that look for a high-confidence structured secret. Ordered most- to
# least-specific so e.g. an Anthropic key is labelled before the generic OpenAI
# pattern would also match it.
_SECRET_DETECTORS: tuple[tuple[str, re.Pattern], ...] = (
    ("anthropic_key", redact._ANTHROPIC),
    ("openai_key", redact._OPENAI),
    ("github_token", redact._GITHUB),
    ("google_key", redact._GOOGLE),
    ("aws_key", redact._AWS),
    ("stripe_key", redact._STRIPE),
    ("slack_token", redact._SLACK),
    ("bearer", redact._BEARER),
    ("jwt", redact._JWT),
    ("cookie", redact._COOKIE),
    ("ssn", _SSN),
)

# Detectors whose patterns match free-text label boilerplate, not just a real
# token. ``_COOKIE`` matches any "Cookie: ..." line, which fires on cookie /
# privacy-consent banner copy ("Cookie: we value your privacy ...") that is
# nearly universal on real pages. ``_BEARER`` similarly matches a label plus
# arbitrary words. Including these in the DOM-secret set turns benign banner text
# into spurious high-severity carry-over findings when the banner recurs across
# pages, so they are excluded from DOM-secret extraction. They remain available
# for the typed-field channel, where the agent actually entered the value.
_LABEL_BOILERPLATE_DETECTORS = frozenset({"cookie", "bearer"})

# Minimum length of the token portion (after the "Cookie:" / "Bearer " label)
# for a label-boilerplate match to be considered an actual secret rather than
# prose.
_MIN_TOKEN_LEN = 20
# A token value: a single run of base64/hex-ish characters, no spaces.
_TOKEN_VALUE = re.compile(r"[A-Za-z0-9+/=._~\-]{%d,}" % _MIN_TOKEN_LEN)


def _label_match_is_token(detector: str, raw: str) -> bool:
    """Whether a label-boilerplate match carries an actual token, not prose.

    For ``cookie`` / ``bearer`` matches the regex also captures the label and any
    following words. We only treat it as a real secret when the value after the
    label looks like a contiguous token (>= _MIN_TOKEN_LEN of base64/hex with no
    spaces), not free-text banner copy.
    """
    if detector not in _LABEL_BOILERPLATE_DETECTORS:
        return True
    # Strip the leading label ("Cookie:", "Set-Cookie:", "Bearer ").
    value = re.sub(r"(?i)^\s*(?:set-)?cookie\s*:|^\s*bearer\s+", "", raw).strip()
    if not value:
        return False
    m = _TOKEN_VALUE.search(value)
    return bool(m)


def _mask(value: str) -> str:
    """Mask a matched secret/PII string, keeping only a short head.

    Never returns the full value. For short values we drop everything; for
    longer ones we keep the first few characters so a reviewer can correlate.
    """
    if value is None:
        return ""
    v = value.strip()
    if len(v) <= 4:
        return redact.REDACTED
    head = v[:4]
    return f"{head}{redact.REDACTED}"


def _valid_card(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    return redact._luhn(digits)


def _valid_phone(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    return 9 <= len(digits) <= 15


def _detect(text: str, *, structured_only: bool = False) -> list[tuple[str, str]]:
    """Return ``(detector_name, raw_match)`` for every sensitive hit in ``text``.

    The raw match is returned so the caller can mask it; it is never stored
    unmasked.

    When ``structured_only`` is set (used for DOM-secret extraction), free-text
    label-boilerplate detectors (``cookie`` / ``bearer``) are skipped unless the
    captured value actually looks like a token. This keeps cookie/privacy
    banner copy from being stored as a DOM secret and later flagged as a
    spurious high-severity carry-over when the banner recurs on another page.
    """
    if not text:
        return []
    hits: list[tuple[str, str]] = []
    spans: list[tuple[int, int]] = []  # consumed regions, to avoid double-flag

    def _overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in spans)

    # 1. Structured secrets (incl. SSN) first.
    for name, pat in _SECRET_DETECTORS:
        for m in pat.finditer(text):
            if _overlaps(m.start(), m.end()):
                continue
            if name in _LABEL_BOILERPLATE_DETECTORS:
                if structured_only:
                    continue
                if not _label_match_is_token(name, m.group(0)):
                    continue
            spans.append((m.start(), m.end()))
            hits.append((name, m.group(0)))

    # 2. Emails.
    for m in redact._EMAIL.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        spans.append((m.start(), m.end()))
        hits.append(("email", m.group(0)))

    # 3. Credit cards (Luhn-validated).
    for m in redact._CC.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        if _valid_card(m.group(0)):
            spans.append((m.start(), m.end()))
            hits.append(("credit_card", m.group(0)))

    # 4. Phone numbers (digit-count validated).
    for m in redact._PHONE.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        if _valid_phone(m.group(0)):
            spans.append((m.start(), m.end()))
            hits.append(("phone", m.group(0)))

    return hits


def _payload_text(payload: dict) -> str:
    """Flatten an action payload into a single searchable string."""
    if not payload:
        return ""
    parts: list[str] = []

    def _walk(v) -> None:
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for sub in v.values():
                _walk(sub)
        elif isinstance(v, (list, tuple)):
            for sub in v:
                _walk(sub)
        elif v is not None:
            parts.append(str(v))

    _walk(payload)
    return "\n".join(parts)


def _payload_mentions_password(payload: dict) -> Optional[str]:
    """If a payload has a password-shaped key with a non-trivial value, return it."""
    if not payload:
        return None
    for k, v in payload.items():
        if isinstance(k, str) and _PASSWORD_KEY.search(k):
            if isinstance(v, str) and len(v.strip()) >= 4:
                return v
    return None


def _finding(
    *,
    step: storage.Step,
    detector: str,
    raw: str,
    channel: str,
    extra_evidence: Optional[dict] = None,
    carry_over: bool = False,
) -> storage.Finding:
    label, severity, score = _SENSITIVITY.get(
        detector, ("sensitive value", "medium", 0.6)
    )
    masked = _mask(raw)
    if carry_over:
        title = f"DOM secret carried into {channel}: {label}"
        detail = (
            f"A {label} present in an earlier captured DOM reappears in this "
            f"step's {channel}, indicating data lifted off one page and pushed "
            f"outward."
        )
        # Carry-over of a credential is more severe than a one-off type.
        if severity in ("low",):
            severity = "medium"
            score = max(score, 0.6)
    else:
        title = f"{label.capitalize()} typed into field"
        detail = (
            f"The agent entered a {label} into a page field "
            f"({step.action_type}). Masked value: {masked}."
        )
    evidence: dict = {
        "channel": channel,
        "detector": detector,
        "masked": masked,
        "action_type": step.action_type,
    }
    if step.url:
        # Sanitize sensitive query keys, then run full-text redaction so any
        # secret value sitting in a benignly-named query param is masked too.
        evidence["url"] = redact.redact_text(redact.sanitize_url(step.url))
    if extra_evidence:
        evidence.update(extra_evidence)
    return storage.Finding(
        trace_id=step.trace_id,
        step_id=step.id,
        kind=KIND,
        severity=severity,
        score=score,
        title=title,
        detail=detail,
        evidence=evidence,
    )


def _url_query_text(url: Optional[str]) -> str:
    """Return the decoded query-string values of a URL as searchable text."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    if not parsed.query:
        return ""
    try:
        params = parse_qsl(parsed.query, keep_blank_values=True)
    except Exception:
        return ""
    return "\n".join(v for _k, v in params)


def analyze(ctx: base.AnalysisContext) -> list[storage.Finding]:
    """Flag typed secrets/PII and DOM-to-action carry-over."""
    findings: list[storage.Finding] = []

    # Running set of (detector, raw) secrets seen in DOMs at earlier steps, used
    # for carry-over detection. We key carry-over on the raw string so an exact
    # value match is required.
    dom_secrets: dict[str, str] = {}  # raw -> detector

    for step in ctx.steps:
        action = (step.action_type or "").lower()
        payload = step.action_payload or {}
        payload_text = _payload_text(payload)

        typed_hits: list[tuple[str, str]] = []

        # --- channel: typed field --------------------------------------------
        if action in _TYPING_ACTIONS:
            typed_hits = _detect(payload_text)
            seen: set[tuple[str, str]] = set()
            for detector, raw in typed_hits:
                key = (detector, raw)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    _finding(
                        step=step,
                        detector=detector,
                        raw=raw,
                        channel="typed field",
                    )
                )
            # Password-shaped payload key (value may not match a secret regex).
            pw = _payload_mentions_password(payload)
            if pw is not None and not any(d == "password" for d, _ in typed_hits):
                findings.append(
                    _finding(
                        step=step,
                        detector="password",
                        raw=pw,
                        channel="typed field",
                    )
                )

        # --- channel: carry-over into a navigation / form post ---------------
        # A secret seen in an earlier DOM that now appears in this step's
        # payload or URL query string.
        if dom_secrets:
            url_query = _url_query_text(step.url)
            carry_targets = [
                ("url", url_query),
                (
                    "form post" if action in _NAV_ACTIONS else "action payload",
                    payload_text,
                ),
            ]
            reported: set[tuple[str, str, str]] = set()
            for channel, haystack in carry_targets:
                if not haystack:
                    continue
                for raw, detector in dom_secrets.items():
                    if raw and raw in haystack:
                        key = (channel, detector, raw)
                        if key in reported:
                            continue
                        reported.add(key)
                        findings.append(
                            _finding(
                                step=step,
                                detector=detector,
                                raw=raw,
                                channel=channel,
                                carry_over=True,
                                extra_evidence={"source": "earlier DOM"},
                            )
                        )

        # --- update the running DOM-secret set from this step's DOMs ----------
        for which in ("before", "after"):
            try:
                html = ctx.load_dom(step, which)
            except Exception:
                logger.debug("exfil: load_dom failed", exc_info=True)
                html = None
            if not html:
                continue
            for detector, raw in _detect(html, structured_only=True):
                if raw:
                    dom_secrets.setdefault(raw, detector)

    return findings
