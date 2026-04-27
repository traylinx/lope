"""Tests for ``lope.synthesis`` — prompt assembly, fail-soft execution,
output formatting.

Covers:
* Prompt includes every successful validator response by name (or by
  ``Response A/B/C`` label when ``anonymous=True``).
* Errors are listed in a dedicated section, not silently merged into
  responses.
* ``structured_findings`` mode replaces raw transcripts with deduped
  consensus findings while still naming the source validators.
* ``run_synthesis`` is fail-soft — primary exceptions, missing primary,
  and empty output all return a result with ``ok=False`` and an error
  message instead of propagating.
* Redaction is applied to validator names, prompts, errors, and
  synthesis output before any field is stored.
"""

from __future__ import annotations

import pytest

from lope.findings import ConsensusFinding, ConsensusLevel
from lope.synthesis import (
    REQUIRED_SECTIONS,
    SynthesisResult,
    build_synthesis_prompt,
    format_synthesis,
    run_synthesis,
)


def _resp(name, answer, error=None):
    return (name, answer, error)


def _consensus(message="missing rate limit", **kw):
    return ConsensusFinding(
        message=message,
        file=kw.get("file", "auth.py"),
        line=kw.get("line", 42),
        severity=kw.get("severity", "high"),
        category=kw.get("category", "security"),
        detected_by=list(kw.get("detected_by", ["claude", "gemini"])),
        evidence={n: f"{n} saw it" for n in kw.get("detected_by", ["claude", "gemini"])},
        confidence_max=kw.get("confidence", 0.85),
        confidence_avg=kw.get("confidence", 0.85),
        agreement_count=kw.get("agreement_count", 2),
        total_validators=kw.get("total_validators", 3),
        agreement_ratio=kw.get("agreement_count", 2) / max(kw.get("total_validators", 3), 1),
        consensus_score=kw.get("score", 0.85),
        consensus_level=kw.get("level", ConsensusLevel.CONFIRMED),
    )


# ---------------------------------------------------------------------------
# build_synthesis_prompt
# ---------------------------------------------------------------------------


def test_prompt_includes_every_successful_response_by_name():
    responses = [
        _resp("claude", "claude says A"),
        _resp("gemini", "gemini says B"),
        _resp("codex", "codex says C"),
    ]
    prompt = build_synthesis_prompt("Add JWT auth", responses)
    assert "[claude]" in prompt
    assert "[gemini]" in prompt
    assert "[codex]" in prompt
    assert "claude says A" in prompt
    assert "gemini says B" in prompt
    assert "codex says C" in prompt
    assert "Add JWT auth" in prompt


def test_prompt_includes_required_section_headers():
    prompt = build_synthesis_prompt("task", [_resp("a", "x")])
    for section in REQUIRED_SECTIONS:
        assert section in prompt


def test_prompt_lists_errors_separately_from_responses():
    responses = [
        _resp("claude", "thoughtful answer"),
        _resp("gemini", "", "subprocess timed out"),
    ]
    prompt = build_synthesis_prompt("decide", responses)
    assert "Validator errors" in prompt
    assert "gemini" in prompt
    assert "subprocess timed out" in prompt
    # error text must not appear in the responses block as if it were content
    response_block_end = prompt.index("Validator errors")
    assert "subprocess timed out" not in prompt[:response_block_end]


def test_prompt_anonymous_mode_strips_validator_names():
    responses = [_resp("claude", "alpha"), _resp("gemini", "beta")]
    prompt = build_synthesis_prompt("task", responses, anonymous=True)
    assert "claude" not in prompt
    assert "gemini" not in prompt
    assert "Response A" in prompt
    assert "Response B" in prompt


def test_prompt_handles_no_responses_gracefully():
    prompt = build_synthesis_prompt("task", [])
    assert "Validator responses:" in prompt
    assert "(no validator returned a non-empty response)" in prompt


def test_prompt_redacts_secrets_in_validator_output():
    secret = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    responses = [_resp("claude", f"answer mentions {secret}")]
    prompt = build_synthesis_prompt("task", responses)
    assert "abcdefghijklmnop" not in prompt
    assert "Bearer <redacted>" in prompt


def test_prompt_redacts_secrets_in_task_text():
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    prompt = build_synthesis_prompt(f"check {secret}", [_resp("a", "x")])
    assert "abcdefghijklmnop" not in prompt


def test_prompt_with_structured_findings_replaces_raw_block():
    responses = [_resp("claude", "raw transcript text")]
    findings = [_consensus()]
    prompt = build_synthesis_prompt(
        "consensus review of auth.py",
        responses,
        structured_findings=findings,
    )
    assert "Consensus findings" in prompt
    assert "auth.py:42" in prompt
    assert "[CONFIRMED]" in prompt
    assert "[HIGH]" in prompt
    # Source attribution preserved even when raw is suppressed.
    assert "claude" in prompt
    # Raw transcript should not appear in the findings-mode prompt.
    assert "raw transcript text" not in prompt


def test_prompt_with_empty_structured_findings_acknowledges_zero_findings():
    prompt = build_synthesis_prompt(
        "task",
        [_resp("a", "x")],
        structured_findings=[],
    )
    assert "(no findings parsed by the consensus pipeline)" in prompt


def test_prompt_anonymous_and_structured_combine_safely():
    responses = [_resp("claude", "ans")]
    findings = [_consensus()]
    prompt = build_synthesis_prompt(
        "task",
        responses,
        structured_findings=findings,
        anonymous=True,
    )
    assert "claude" not in prompt
    assert "Response A" in prompt
    assert "auth.py:42" in prompt


# ---------------------------------------------------------------------------
# run_synthesis
# ---------------------------------------------------------------------------


class _FakeValidator:
    name = "fake"

    def __init__(self, *, text="OK", raise_with=None):
        self._text = text
        self._raise = raise_with

    def generate(self, prompt, timeout):
        if self._raise:
            raise self._raise
        return self._text


def test_run_synthesis_returns_text_on_success():
    primary = _FakeValidator(text="## Consensus\n- agreed\n## Recommended action\n- ship")
    result = run_synthesis(primary, "prompt", 30)
    assert isinstance(result, SynthesisResult)
    assert result.ok is True
    assert "Consensus" in result.text
    assert result.primary == "fake"
    assert result.error == ""


def test_run_synthesis_is_failsoft_on_exception():
    primary = _FakeValidator(raise_with=RuntimeError("primary boom"))
    result = run_synthesis(primary, "prompt", 30)
    assert result.ok is False
    assert "primary boom" in result.error
    assert result.primary == "fake"


def test_run_synthesis_handles_missing_primary():
    result = run_synthesis(None, "prompt", 30)
    assert result.ok is False
    assert "No primary" in result.error


def test_run_synthesis_treats_empty_output_as_failure():
    primary = _FakeValidator(text="   \n  ")
    result = run_synthesis(primary, "prompt", 30)
    assert result.ok is False
    assert "empty" in result.error.lower()


def test_run_synthesis_redacts_secret_in_exception_message():
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    primary = _FakeValidator(raise_with=RuntimeError(f"failed with {secret}"))
    result = run_synthesis(primary, "prompt", 30)
    assert result.ok is False
    assert "abcdefghijklmnop" not in result.error


def test_run_synthesis_redacts_secret_in_returned_text():
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    primary = _FakeValidator(text=f"## Consensus\n- {secret}\n## Recommended action\n- act")
    result = run_synthesis(primary, "prompt", 30)
    assert result.ok is True
    assert "abcdefghijklmnop" not in result.text
    assert "Bearer <redacted>" in result.text


# ---------------------------------------------------------------------------
# format_synthesis
# ---------------------------------------------------------------------------


def test_format_synthesis_human_mode_includes_header_and_body():
    result = SynthesisResult(ok=True, text="## Consensus\n- yes", primary="claude")
    out = format_synthesis(result, machine_json=False)
    assert "synthesis" in out.lower()
    assert "claude" in out
    assert "## Consensus" in out


def test_format_synthesis_machine_json_returns_raw_text():
    result = SynthesisResult(ok=True, text="## Consensus\n- yes", primary="claude")
    out = format_synthesis(result, machine_json=True)
    assert out == "## Consensus\n- yes"


def test_format_synthesis_failed_human_mode_shows_error_text():
    result = SynthesisResult(ok=False, error="primary missing")
    out = format_synthesis(result)
    assert "synthesis unavailable" in out
    assert "primary missing" in out


def test_format_synthesis_failed_machine_json_returns_empty_string():
    result = SynthesisResult(ok=False, error="primary missing")
    out = format_synthesis(result, machine_json=True)
    assert out == ""


# ---------------------------------------------------------------------------
# Integration with review consensus path
# ---------------------------------------------------------------------------


def test_anonymous_label_is_consistent_across_provenance_and_findings():
    # Validator order is mixed: claude success, gemini error, codex success.
    # codex should get the same label in both the source-validators line
    # and inside the structured finding's detected_by list.
    responses = [
        _resp("claude", "answer-a"),
        _resp("gemini", "", "boom"),
        _resp("codex", "answer-c"),
    ]
    findings = [_consensus(detected_by=["claude", "codex"])]
    prompt = build_synthesis_prompt(
        "task",
        responses,
        structured_findings=findings,
        anonymous=True,
    )
    # claude=A, gemini=B, codex=C in encounter order across all three lists.
    assert "Source responses: Response A, Response C" in prompt
    assert "detected_by: Response A, Response C" in prompt
    assert "Response B: boom" in prompt
    assert "claude" not in prompt
    assert "gemini" not in prompt
    assert "codex" not in prompt


def test_anon_label_handles_more_than_26_validators():
    from lope.synthesis import _anon_label

    assert _anon_label(0) == "Response A"
    assert _anon_label(25) == "Response Z"
    assert _anon_label(26) == "Response AA"
    assert _anon_label(27) == "Response AB"


def test_prompt_redacts_secrets_inside_validator_names():
    secret_name = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    prompt = build_synthesis_prompt(
        "task",
        [_resp(secret_name, "ans")],
    )
    assert "abcdefghijklmnop" not in prompt


def test_prompt_uses_required_sections_constant_not_literal_strings():
    # Mutate (and restore) the constant to prove the prompt is data-driven.
    import lope.synthesis as mod

    saved = mod.REQUIRED_SECTIONS
    try:
        mod.REQUIRED_SECTIONS = ("## SmokeSection",)
        prompt = build_synthesis_prompt("task", [_resp("a", "x")])
        assert "## SmokeSection" in prompt
    finally:
        mod.REQUIRED_SECTIONS = saved


def test_full_path_with_consensus_findings_redacts_message_secrets():
    secret_message = "leaks Bearer abcdefghijklmnopqrstuvwxyz123456"
    finding = _consensus(message=secret_message)
    prompt = build_synthesis_prompt("task", [], structured_findings=[finding])
    assert "abcdefghijklmnop" not in prompt
