"""Regression tests for opencode JSON-stream parsing + diagnostic.

History: 2026-05-02 — `lope negotiate` escalated for users whenever the
prompt context referenced file paths. Opencode's model decided to read
those files via tool-use, the sandbox auto-rejected the read, the
session ended via `reason: "tool-calls"` having emitted ZERO text
events. `_extract_text_from_json_stream` returned empty, the drafter
raised "opencode returned no text events" with no useful detail, the
fallback chain swallowed the message, and the negotiation appeared to
"just fail" with no actionable reason. Fix landed:
  1. Negotiator system prompt now says DO NOT USE TOOLS.
  2. `_diagnose_empty_opencode_stream` surfaces the actual reason in
     every failure mode.
  3. Drafter fallback prints the surfaced reason inline.

These tests pin the diagnostic behavior so the regression can't reappear.
"""

from lope.validators import (
    _extract_text_from_json_stream,
    _diagnose_empty_opencode_stream,
)
from lope.negotiator import _negotiator_system_prompt


def test_extract_text_concatenates_text_events():
    stream = (
        '{"type":"step_start"}\n'
        '{"type":"text","part":{"text":"Hello "}}\n'
        '{"type":"text","part":{"text":"world"}}\n'
        '{"type":"step_finish","part":{"reason":"stop"}}\n'
    )
    assert _extract_text_from_json_stream(stream) == "Hello world"


def test_extract_text_returns_empty_when_no_text_events():
    stream = (
        '{"type":"step_start"}\n'
        '{"type":"step_finish","part":{"reason":"stop"}}\n'
    )
    assert _extract_text_from_json_stream(stream) == ""


def test_extract_text_skips_malformed_lines():
    stream = (
        '{"type":"step_start"}\n'
        'this is not json\n'
        '{"type":"text","part":{"text":"Hi"}}\n'
    )
    assert _extract_text_from_json_stream(stream) == "Hi"


def test_diagnose_recognizes_rejected_tool_call():
    """Most common production failure: model tried `read` on a path
    outside the sandbox, opencode auto-rejected, session ended via
    `tool-calls`. The diagnostic must say "tool-use rejected" — that's
    the only actionable signal a user has."""
    stream = (
        '{"type":"step_start"}\n'
        '{"type":"tool_use","part":{"tool":"read","state":'
        '{"status":"error","error":"The user rejected permission to use this specific tool call."}}}\n'
        '{"type":"step_finish","part":{"reason":"tool-calls"}}\n'
    )
    diag = _diagnose_empty_opencode_stream(stream)
    assert "tool-use" in diag
    assert "rejected" in diag
    assert "DO NOT USE TOOLS" in diag


def test_diagnose_recognizes_tool_calls_finish_without_explicit_error():
    """Some opencode versions end via `reason: tool-calls` without
    emitting an explicit tool_use error event — diagnose should still
    surface the finish reason."""
    stream = (
        '{"type":"step_start"}\n'
        '{"type":"step_finish","part":{"reason":"tool-calls"}}\n'
    )
    diag = _diagnose_empty_opencode_stream(stream)
    assert "tool-calls" in diag
    assert "DO NOT USE TOOLS" in diag


def test_diagnose_recognizes_error_finish():
    stream = (
        '{"type":"step_start"}\n'
        '{"type":"step_finish","part":{"reason":"error"}}\n'
    )
    diag = _diagnose_empty_opencode_stream(stream)
    assert "error" in diag


def test_diagnose_recognizes_empty_stream():
    assert "empty" in _diagnose_empty_opencode_stream("").lower()


def test_diagnose_recognizes_only_step_start():
    stream = '{"type":"step_start"}\n'
    diag = _diagnose_empty_opencode_stream(stream)
    # Stream had events (step_start) but no step_finish and no text.
    assert "no text events" in diag


def test_negotiator_system_prompt_forbids_tool_use():
    """The drafter prompt MUST instruct the LLM not to use tools.
    Without this directive, opencode/codex/claude/gemini will all try
    to read context-mentioned file paths and fail differently — the
    fix that the diagnostic is actively pointing users toward."""
    prompt = _negotiator_system_prompt("engineering")
    assert "DO NOT USE" in prompt or "do not use" in prompt.lower()
    # And specifically mention tools, not just generic "don't do X"
    assert "tool" in prompt.lower()


def test_negotiator_system_prompt_works_for_all_domains():
    for domain in ("engineering", "business", "research"):
        prompt = _negotiator_system_prompt(domain)
        assert "DO NOT USE" in prompt or "do not use" in prompt.lower()
        assert "tool" in prompt.lower()
