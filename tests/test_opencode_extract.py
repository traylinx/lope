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
    OpencodeValidator,
    _extract_text_from_json_stream,
    _extract_text_from_opencode_export,
    _opencode_session_id_from_stdout,
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


def test_extract_text_concatenates_modern_delta_events():
    stream = (
        '{"type":"message.part.delta","properties":{"field":"text","delta":"Hello "}}\n'
        '{"type":"message.part.delta","properties":{"field":"text","delta":"world"}}\n'
        '{"type":"message.part.delta","properties":{"field":"metadata","delta":"ignored"}}\n'
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


def test_opencode_session_id_from_stdout_handles_plain_json_and_osc():
    stream = '{"type":"step_start","sessionID":"ses_plain"}\n'
    assert _opencode_session_id_from_stdout(stream) == "ses_plain"

    osc = '\x1b]777;notify;warp://cli-agent;{"v":1,"session_id":"ses_osc"}\x07'
    assert _opencode_session_id_from_stdout(osc) == "ses_osc"


def test_extract_text_from_opencode_export_fallback(monkeypatch):
    stream = '{"type":"step_start","sessionID":"ses_export"}\n'

    def fake_run(command, input_text, timeout, cwd):
        assert command == ["opencode", "export", "ses_export"]
        assert input_text is None
        assert timeout == 45
        assert cwd == "/tmp"

        class Proc:
            returncode = 0
            stdout = (
                '{"messages":[{"info":{"role":"user"},"parts":[{"type":"text","text":"prompt"}]},'
                '{"info":{"role":"assistant"},"parts":[{"type":"reasoning","text":"think"},'
                '{"type":"text","text":"ok"}]}]}'
            )
            stderr = ""

        return Proc(), 0.01

    monkeypatch.setattr("lope.validators._run_with_group_kill", fake_run)
    assert (
        _extract_text_from_opencode_export(
            "opencode",
            stream,
            timeout=45,
            cwd="/tmp",
        )
        == "ok"
    )


def test_export_fallback_does_not_launch_without_remaining_stage_budget(monkeypatch):
    stream = '{"type":"step_start","sessionID":"ses_expired"}\n'
    launches = []

    monkeypatch.setattr(
        "lope.validators._run_with_group_kill",
        lambda *_args, **_kwargs: launches.append(True),
    )

    assert _extract_text_from_opencode_export(
        "opencode",
        stream,
        timeout=0,
        cwd="/tmp",
    ) == ""
    assert launches == []


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


def test_diagnose_recognizes_modern_updated_tool_call():
    stream = (
        '{"type":"message.part.updated","properties":{"part":{"type":"tool","tool":"read",'
        '"state":{"status":"error","error":"blocked by sandbox"}}}}\n'
        '{"type":"message.part.updated","properties":{"part":{"type":"step-finish",'
        '"reason":"tool-calls"}}}\n'
    )
    diag = _diagnose_empty_opencode_stream(stream)
    assert "tool-use" in diag
    assert "read: blocked by sandbox" in diag


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


def test_opencode_validator_passes_prompt_as_positional_arg(monkeypatch):
    """OpenCode 1.15.10 starts but emits only step_start when Lope pipes
    the prompt through stdin. Pin the adapter contract: prompt is argv,
    stdin is empty."""

    captured = {}

    def fake_run(command, input_text, timeout, cwd):
        captured["command"] = command
        captured["input_text"] = input_text
        captured["timeout"] = timeout
        captured["cwd"] = cwd

        class Proc:
            returncode = 0
            stdout = '{"type":"text","part":{"text":"OK"}}\n'
            stderr = ""

        return Proc(), 0.01

    monkeypatch.setattr(OpencodeValidator, "available", lambda self: True)
    monkeypatch.setattr("lope.validators._run_with_group_kill", fake_run)

    result = OpencodeValidator(binary="opencode", workdir="/tmp").generate(
        "Reply exactly: OK",
        timeout=45,
    )

    assert result == "OK"
    assert captured["command"][-1] == "Reply exactly: OK"
    assert captured["command"][-2] == "json"
    assert "--pure" in captured["command"]
    assert captured["input_text"] is None
    assert captured["timeout"] == 45
    assert captured["cwd"] == "/tmp"
