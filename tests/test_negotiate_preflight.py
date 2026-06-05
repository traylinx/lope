from __future__ import annotations

from lope.cli import _negotiate_prompt_profile


def test_preflight_reports_bytes_and_lines_for_inline_context(monkeypatch):
    monkeypatch.setenv("LOPE_NEGOTIATE_LARGE_PROMPT_BYTES", "999999")
    lines = _negotiate_prompt_profile(
        goal="Ship auth",
        context="alpha\nbeta",
        domain="engineering",
        timeout=900,
        inline_context="alpha\nbeta",
    )
    text = "\n".join(lines)

    assert "Negotiate preflight:" in text
    assert "Context payload:" in text
    assert "bytes, 2 lines" in text
    assert "Generated drafter prompt:" in text
    assert "Effective timeout: 900s" in text
    assert "--context is inlined into the model prompt" in text


def test_preflight_context_file_note_says_inlined_not_attached():
    lines = _negotiate_prompt_profile(
        goal="Ship auth",
        context="file payload",
        domain="engineering",
        timeout=900,
        context_file="brief.md",
    )
    text = "\n".join(lines)

    assert "--context-file is read and inlined into the model prompt" in text
    assert "not attached as a separate file" in text


def test_preflight_warns_for_large_prompt_and_low_timeout(monkeypatch):
    monkeypatch.setenv("LOPE_NEGOTIATE_LARGE_PROMPT_BYTES", "10")
    monkeypatch.setenv("LOPE_NEGOTIATE_LARGE_PROMPT_LINES", "999999")
    monkeypatch.setenv("LOPE_NEGOTIATE_LOW_TIMEOUT_SECONDS", "300")

    lines = _negotiate_prompt_profile(
        goal="Ship auth",
        context="large context payload",
        domain="engineering",
        timeout=120,
        inline_context="large context payload",
    )
    text = "\n".join(lines)

    assert "WARNING: large generated prompt with low timeout" in text
    assert "Use a compact LOPE_BRIEF.md or --timeout 300+" in text
