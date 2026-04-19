"""Redact sensitive data before sharing a trace publicly.

Aggressive by design: we bias toward false positives (redact something
benign) over false negatives (leak a real secret). Run against DOM HTML,
LLM prompts, LLM responses, and any URL captured in the trace.

Patterns are ordered roughly from most-specific to most-general so that
a provider-prefixed API key is caught before the generic "long base64"
fallback would match.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

REDACTED = "[REDACTED]"


# --- API key patterns -------------------------------------------------------

# Anthropic: sk-ant-...
_ANTHROPIC = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")
# OpenAI: sk-... or sk-proj-... (new format)
_OPENAI = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}")
# GitHub: ghp_ / gho_ / ghu_ / ghs_ / ghr_ + 36 chars, OR github_pat_*
_GITHUB = re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}")
# Google API keys
_GOOGLE = re.compile(r"AIza[0-9A-Za-z_-]{35}")
# AWS access key
_AWS = re.compile(r"AKIA[0-9A-Z]{16}")
# Stripe
_STRIPE = re.compile(r"(?:sk|pk|rk)_(?:live|test)_[0-9a-zA-Z]{20,}")
# Slack
_SLACK = re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")

# --- Auth/session patterns --------------------------------------------------

# Authorization: Bearer <stuff>  (case-insensitive, stops at whitespace/quotes/closing tag)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/\-]{8,}=*", re.IGNORECASE)
# JWT: three dot-separated base64url segments starting with eyJ
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")
# Set-Cookie / Cookie header lines — stop at newline or <
_COOKIE = re.compile(r"(?i)(?:Set-)?Cookie\s*:\s*[^\n\r<]+")

# --- PII --------------------------------------------------------------------

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Phone — loose, validated by digit-count in the replacer
_PHONE = re.compile(r"\+?\d[\d\s().\-]{8,18}\d")

# Credit card — loose, validated by Luhn in the replacer
_CC = re.compile(r"\b(?:\d[ -]?){13,19}\b")

# --- ordered API key / auth patterns, applied before PII --------------------

_SECRET_PATTERNS = (
    _ANTHROPIC, _OPENAI, _GITHUB, _GOOGLE, _AWS, _STRIPE, _SLACK,
    _BEARER, _JWT, _COOKIE,
)

# Query string keys that mean "this value is a secret"
_SENSITIVE_QUERY_KEYS = re.compile(
    r"(?i)(?:^|[_\-])(?:password|passwd|pwd|token|secret|api[_\-]?key|auth|access[_\-]?key|private[_\-]?key)(?:[_\-]|$)"
)


# --- Luhn for credit card validation ----------------------------------------


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    checksum = 0
    parity = len(d) % 2
    for i, x in enumerate(d):
        if i % 2 == parity:
            x *= 2
            if x > 9:
                x -= 9
        checksum += x
    return checksum % 10 == 0


# --- public API -------------------------------------------------------------


def redact_text(s: str | None) -> str | None:
    """Apply every rule to a blob of text. Returns input unchanged if None/empty."""
    if not s:
        return s

    # 1. High-confidence structured secrets first.
    for pat in _SECRET_PATTERNS:
        s = pat.sub(REDACTED, s)

    # 2. Emails: any match gets redacted.
    s = _EMAIL.sub(REDACTED, s)

    # 3. Credit cards: Luhn-validated to cut false positives on long digit runs
    #    (timestamps, IDs).
    def _cc_sub(m: re.Match) -> str:
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        return REDACTED if _luhn(digits) else raw

    s = _CC.sub(_cc_sub, s)

    # 4. Phone numbers: count digits and redact 9–15 digit runs (E.164 bounds).
    #    We skip pure digit runs already eaten by CC matches.
    def _phone_sub(m: re.Match) -> str:
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if 9 <= len(digits) <= 15:
            return REDACTED
        return raw

    s = _PHONE.sub(_phone_sub, s)

    return s


def sanitize_url(u: str | None) -> str | None:
    """Strip sensitive query-string values from a URL. Leaves the URL shape intact."""
    if not u:
        return u
    try:
        parsed = urlparse(u)
    except Exception:
        return u
    if not parsed.query:
        return u
    try:
        params = parse_qsl(parsed.query, keep_blank_values=True)
    except Exception:
        return u
    clean = [
        (k, REDACTED if _SENSITIVE_QUERY_KEYS.search(k) else v) for k, v in params
    ]
    return urlunparse(parsed._replace(query=urlencode(clean)))
