from __future__ import annotations

from lope.redaction import redact_mapping, redact_text


def test_redacts_openai_style_key():
    text = "Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    out = redact_text(text)
    assert "sk-proj" not in out
    assert "sk-<redacted>" in out or "Bearer <redacted>" in out


def test_redacts_github_token():
    out = redact_text("token=ghp_abcdefghijklmnopqrstuvwxyz1234567890")
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert "ghp_<redacted>" in out or "<redacted>" in out


def test_redacts_bearer_token():
    out = redact_text("curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz.123456'")
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert "Bearer <redacted>" in out


def test_redacts_pem_private_key_block():
    pem = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA1234567890SECRET
-----END RSA PRIVATE KEY-----"""
    out = redact_text(pem)
    assert "SECRET" not in out
    assert "-----BEGIN RSA PRIVATE KEY-----" in out
    assert "<redacted>" in out


def test_redacts_header_style_long_tokens():
    out = redact_text("X-API-Key: abcdefghijklmnopqrstuvwxyz1234567890")
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert "X-API-Key: <redacted>" in out


def test_redact_mapping_recurses():
    payload = {"outer": {"auth": "Bearer abcdefghijklmnopqrstuvwxyz"}, "items": ["sk-abcdefghi123456789"]}
    out = redact_mapping(payload)
    assert out["outer"]["auth"] == "Bearer <redacted>"
    assert out["items"][0] == "sk-<redacted>"



def test_clean_output_unchanged():
    text = "normal review output\n  with indentation\nand no secrets"
    assert redact_text(text) == text


def test_redaction_is_idempotent():
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    once = redact_text(text)
    twice = redact_text(once)
    assert twice == once


def test_none_becomes_empty_string():
    assert redact_text(None) == ""
