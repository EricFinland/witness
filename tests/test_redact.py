"""Redaction rule coverage.

Every addition should have a positive case (redacts real secret) and a
negative case (leaves normal text alone). False negatives leak data;
false positives just make traces less readable.
"""

from witness.redact import REDACTED, redact_text, sanitize_url


# --- API keys ---------------------------------------------------------------


def test_anthropic_key_redacted():
    # Synthetic key — same format (sk-ant-api03- + 95 chars) but obviously fake.
    s = "hi my key is sk-ant-api03-FAKEKEYfakekey0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0000000"
    out = redact_text(s)
    assert "sk-ant" not in out
    assert REDACTED in out
    assert "hi my key is" in out


def test_openai_key_redacted():
    assert REDACTED in redact_text("Authorization: sk-proj-abcdefghij1234567890ABCDabcd")


def test_github_pat_redacted():
    s = "token github_pat_11AAAAAAA0ABCDEFGHIJKLmnopqrstuvwxyz0123456789ABCDEFGHIJ"
    assert REDACTED in redact_text(s)


def test_github_legacy_ghp():
    assert REDACTED in redact_text("ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789")


def test_google_api_key():
    assert REDACTED in redact_text("AIzaSyAabcdefghijklmnopqrstuvwxyz1234567")


def test_aws_key():
    assert REDACTED in redact_text("credentials AKIAIOSFODNN7EXAMPLE are set")


def test_stripe_key():
    assert REDACTED in redact_text("sk_live_abcdefghijklmnopqrstuv12")


def test_non_key_long_string_preserved():
    # 40-char base64-ish id that isn't a known prefix — leave it alone.
    s = "request-id: 5f4dcc3b5aa765d61d8327deb882cf99a1b2c3d4e5f6"
    out = redact_text(s)
    assert REDACTED not in out


# --- Auth headers -----------------------------------------------------------


def test_bearer_token_redacted():
    s = 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def'
    out = redact_text(s)
    # Bearer pattern matches, then JWT also would; either way the secret is gone.
    assert "eyJ" not in out
    assert REDACTED in out


def test_jwt_redacted():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert REDACTED in redact_text(f"<meta name=token content='{jwt}' />")


def test_cookie_header_redacted():
    s = "Set-Cookie: session=abc123xyz; HttpOnly; SameSite=Strict"
    out = redact_text(s)
    assert "abc123xyz" not in out
    assert REDACTED in out


# --- PII --------------------------------------------------------------------


def test_email_redacted():
    assert REDACTED in redact_text("contact ada@example.com for help")


def test_email_inside_html():
    out = redact_text("<a href='mailto:eng@anthropic.com'>email us</a>")
    assert "anthropic.com" not in out
    assert REDACTED in out


def test_credit_card_luhn_valid_redacted():
    # Visa test number with valid Luhn
    assert REDACTED in redact_text("card 4111 1111 1111 1111 on file")


def test_credit_card_invalid_luhn_preserved():
    # 16 digits but Luhn-invalid — likely an ID, not a card
    out = redact_text("order id 1234567890123456")
    assert REDACTED not in out


def test_phone_redacted():
    out = redact_text("call me at +1-415-555-0123 ext 4")
    assert "555-0123" not in out


def test_short_digit_run_preserved():
    # 6 digits — not long enough to be a phone
    out = redact_text("error code 500000 raised")
    assert REDACTED not in out


# --- URL sanitization -------------------------------------------------------


def test_url_sensitive_query_redacted():
    out = sanitize_url("https://api.example.com/v1?api_key=sekret&limit=10")
    # urlencode → [REDACTED] becomes %5BREDACTED%5D, which is what we want
    # (still obviously redacted, stays a valid URL).
    assert "sekret" not in out
    assert "%5BREDACTED%5D" in out
    assert "limit=10" in out


def test_url_plain_passthrough():
    u = "https://example.com/page?x=1&y=two"
    assert sanitize_url(u) == u


def test_url_multiple_sensitive_keys():
    out = sanitize_url("https://x.io?token=A&password=B&q=hello")
    # Sensitive values replaced with the URL-encoded redaction marker
    token_val = out.split("token=", 1)[1].split("&")[0]
    pw_val = out.split("password=", 1)[1].split("&")[0]
    assert token_val == "%5BREDACTED%5D"
    assert pw_val == "%5BREDACTED%5D"
    assert "q=hello" in out


def test_url_no_query_untouched():
    assert sanitize_url("https://example.com/page") == "https://example.com/page"


def test_url_handles_none_and_empty():
    assert sanitize_url(None) is None
    assert sanitize_url("") == ""


# --- Misc -------------------------------------------------------------------


def test_none_and_empty_pass_through():
    assert redact_text(None) is None
    assert redact_text("") == ""


def test_idempotent():
    s = "Bearer sk-ant-test-abcdefghijklmnopqrst and ada@example.com"
    once = redact_text(s)
    twice = redact_text(once)
    assert once == twice  # running again doesn't break anything
