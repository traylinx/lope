"""End-to-end tests for ``lope.review`` consensus orchestration.

These tests never call real validators — they replace ``_default_fanout``
with deterministic stubs so the pipeline is exercised against curated
validator output. The four output formats (text, json, markdown,
markdown-pr, sarif) are pinned for shape, redaction, and fallback
behavior.
"""

from __future__ import annotations

import json
from typing import List, Optional, Tuple

import pytest

from lope import review as review_module
from lope.review import (
    ReviewInput,
    ReviewReport,
    SUPPORTED_FORMATS,
    build_review_prompt,
    parse_responses,
    render_report,
    run_consensus_review,
)


FanoutResult = Tuple[str, str, Optional[str]]


def _stub_fanout(results: List[FanoutResult]):
    def _fn(pool, prompt, timeout):
        return list(results)

    return _fn


CLAUDE_OUT = (
    "- [HIGH] auth.py:42 — Missing rate limiting before password check (confidence: 0.86)\n"
    "- [MEDIUM] auth.py:88 — Token expiry edge case is not tested (confidence: 0.62)\n"
)

GEMINI_OUT = (
    "- [HIGH] auth.py:42 — Missing rate limiting before password check (confidence: 0.80)\n"
    "- [MEDIUM] auth.py:88 — Token expiry edge case is not tested (confidence: 0.55)\n"
)

CODEX_OUT = '[{"severity":"high","file":"auth.py","line":42,"message":"Missing rate limiting before password check","confidence":0.78}]'


def _three_validator_results():
    return [
        ("claude", CLAUDE_OUT, None),
        ("gemini", GEMINI_OUT, None),
        ("codex", CODEX_OUT, None),
    ]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_build_review_prompt_includes_focus_and_filename():
    prompt = build_review_prompt(
        ReviewInput(target="auth.py", content="def login(): pass\n", focus="security")
    )
    assert "security" in prompt
    assert "auth.py" in prompt
    assert "def login()" in prompt
    assert "[HIGH]" in prompt  # encourages structured format


def test_build_review_prompt_falls_back_to_default_focus():
    prompt = build_review_prompt(ReviewInput(target="x.py", content="pass"))
    assert "Identify bugs" in prompt or "Review this file" in prompt


def test_parse_responses_separates_errors_from_findings():
    raw = [
        ("claude", CLAUDE_OUT, None),
        ("gemini", "", "subprocess timed out"),
    ]
    findings, parses, errors = parse_responses(raw, source_file="auth.py")
    assert len(findings) == 2  # claude's two lines
    assert "claude" in parses
    assert errors == [{"validator": "gemini", "error": "subprocess timed out"}]


def test_parse_responses_redacts_error_text():
    raw = [("oauth", "", "Bearer abcdefghijklmnopqrstuvwxyz123456 expired")]
    _, _, errors = parse_responses(raw)
    assert "abcdefghijklmnop" not in errors[0]["error"]


def test_supported_formats_constant_matches_renderer():
    assert "text" in SUPPORTED_FORMATS
    assert "sarif" in SUPPORTED_FORMATS
    assert "markdown-pr" in SUPPORTED_FORMATS


# ---------------------------------------------------------------------------
# run_consensus_review wiring
# ---------------------------------------------------------------------------


def test_run_consensus_review_uses_injected_fanout():
    fanout = _stub_fanout(_three_validator_results())
    report = run_consensus_review(
        target="auth.py",
        content="placeholder",
        validators=["claude", "gemini", "codex"],
        focus="security",
        fanout=fanout,
    )
    assert isinstance(report, ReviewReport)
    assert report.raw_count >= 5  # 2 + 2 + 1
    assert report.merged_count <= report.raw_count
    assert "claude" in report.parse_methods
    assert report.fallback is False
    assert report.errors == []


def test_run_consensus_review_records_per_validator_errors():
    fanout = _stub_fanout(
        [
            ("claude", CLAUDE_OUT, None),
            ("gemini", "", "Bearer abcdefghijklmnopqrstuvwxyz123456 expired"),
        ]
    )
    report = run_consensus_review(
        target="auth.py",
        content="x",
        validators=["claude", "gemini"],
        fanout=fanout,
    )
    assert len(report.errors) == 1
    assert report.errors[0]["validator"] == "gemini"
    assert "abcdefghijklmnop" not in report.errors[0]["error"]


def test_run_consensus_review_falls_back_when_no_findings_parse():
    fanout = _stub_fanout(
        [
            ("claude", "no structured output here", None),
            ("gemini", "", None),
        ]
    )
    report = run_consensus_review(
        target="x.py",
        content="pass",
        validators=["claude", "gemini"],
        fanout=fanout,
    )
    assert report.fallback is True
    assert report.merged_count == 0


def test_run_consensus_review_min_consensus_filters_low_score_findings():
    fanout = _stub_fanout(_three_validator_results())
    high = run_consensus_review(
        target="auth.py",
        content="x",
        validators=["claude", "gemini", "codex"],
        fanout=fanout,
        min_consensus=0.0,
    )
    filtered = run_consensus_review(
        target="auth.py",
        content="x",
        validators=["claude", "gemini", "codex"],
        fanout=fanout,
        min_consensus=0.99,
    )
    assert len(filtered.scored) <= len(high.scored)
    assert all(f.consensus_score >= 0.99 for f in filtered.scored)


def test_run_consensus_review_default_fanout_is_lazy(monkeypatch):
    captured = {}

    def fake_fanout(pool, prompt, timeout):
        captured["called"] = True
        return [("claude", CLAUDE_OUT, None)]

    monkeypatch.setattr(review_module, "_default_fanout", fake_fanout)
    report = run_consensus_review(
        target="auth.py",
        content="x",
        validators=["claude"],
    )
    assert captured.get("called") is True
    assert report.merged_count >= 1


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


def _make_report():
    fanout = _stub_fanout(_three_validator_results())
    return run_consensus_review(
        target="auth.py",
        content="placeholder",
        validators=["claude", "gemini", "codex"],
        focus="security",
        fanout=fanout,
    )


def test_render_text_includes_summary_and_consensus_section():
    out = render_report(_make_report(), output_format="text")
    assert "Lope consensus review: auth.py" in out
    assert "Validators: claude, gemini, codex" in out
    assert "raw → " in out
    assert "auth.py:42" in out
    assert "consensus:" in out


def test_render_text_includes_raw_when_flag_passed():
    out = render_report(_make_report(), output_format="text", include_raw=True)
    assert "## Raw responses" in out
    assert "claude" in out
    assert "auth.py:42" in out


def test_render_json_emits_machine_readable_payload():
    out = render_report(_make_report(), output_format="json")
    parsed = json.loads(out)
    assert parsed["target"] == "auth.py"
    assert parsed["validators"] == ["claude", "gemini", "codex"]
    assert parsed["fallback"] is False
    assert "findings" in parsed and isinstance(parsed["findings"], list)
    assert parsed["findings"]
    sample = parsed["findings"][0]
    for key in ("message", "consensus_score", "consensus_level", "detected_by"):
        assert key in sample


def test_render_markdown_pr_returns_table_with_consensus_columns():
    out = render_report(_make_report(), output_format="markdown-pr")
    assert "## 🔍 Lope consensus review" in out
    assert "| Severity | Location | Issue | Consensus | Detected by |" in out
    assert "auth.py:42" in out


def test_render_markdown_includes_title_and_body():
    out = render_report(_make_report(), output_format="markdown")
    assert out.startswith("# Lope consensus review: auth.py")
    assert "**Validators:**" in out
    assert "## CONFIRMED" in out or "## NEEDS VERIFICATION" in out


def test_render_sarif_returns_valid_envelope():
    out = render_report(_make_report(), output_format="sarif")
    parsed = json.loads(out)
    assert parsed["version"] == "2.1.0"
    assert parsed["runs"][0]["tool"]["driver"]["name"] == "lope"
    results = parsed["runs"][0]["results"]
    assert results
    sample = results[0]
    assert sample["ruleId"].startswith("lope.")
    assert sample["level"] in {"error", "warning", "note"}


def test_render_unknown_format_raises():
    with pytest.raises(ValueError):
        render_report(_make_report(), output_format="exe")


def test_fallback_text_emits_warning_then_raw():
    fanout = _stub_fanout(
        [
            ("claude", "no structure here", None),
            ("gemini", "", "boom"),
        ]
    )
    report = run_consensus_review(
        target="x.py",
        content="x",
        validators=["claude", "gemini"],
        fanout=fanout,
    )
    out = render_report(report, output_format="text")
    assert "No structured findings parsed" in out
    assert "claude" in out
    assert "[ERROR]" in out


def test_fallback_markdown_pr_announces_zero_findings():
    fanout = _stub_fanout([("claude", "freeform prose", None)])
    report = run_consensus_review(
        target="x.py",
        content="x",
        validators=["claude"],
        fanout=fanout,
    )
    out = render_report(report, output_format="markdown-pr")
    assert "0 structured findings" in out
    assert "include-raw" in out


def test_render_redacts_secrets_in_consensus_output():
    secret_line = (
        "- [HIGH] auth.py:42 — leaks Authorization: "
        "Bearer abcdefghijklmnopqrstuvwxyz123456 (confidence: 0.9)"
    )
    fanout = _stub_fanout([("claude", secret_line, None)])
    report = run_consensus_review(
        target="auth.py",
        content="x",
        validators=["claude"],
        fanout=fanout,
    )
    text_out = render_report(report, output_format="text")
    json_out = render_report(report, output_format="json")
    sarif_out = render_report(report, output_format="sarif")
    pr_out = render_report(report, output_format="markdown-pr")
    for blob in (text_out, json_out, sarif_out, pr_out):
        assert "abcdefghijklmnop" not in blob


def test_consensus_render_is_byte_deterministic():
    report = _make_report()
    a = render_report(report, output_format="json")
    b = render_report(report, output_format="json")
    assert a == b
    a = render_report(report, output_format="sarif")
    b = render_report(report, output_format="sarif")
    assert a == b


def test_parse_responses_isolates_parser_exceptions(monkeypatch):
    from lope import review as review_module
    from lope.findings import parse_findings as real_parse_findings

    def boom(text, validator, source_file=None):
        if validator == "broken":
            raise RuntimeError("simulated parser failure")
        return real_parse_findings(text, validator, source_file=source_file)

    monkeypatch.setattr(review_module, "parse_findings", boom)
    raw = [
        ("claude", CLAUDE_OUT, None),
        ("broken", "anything", None),
    ]
    findings, parses, errors = review_module.parse_responses(raw, source_file="auth.py")
    assert "claude" in parses
    assert any("broken" == e["validator"] for e in errors)
    assert any("simulated parser failure" in e["error"] for e in errors)
    assert findings  # claude's findings still came through


def test_render_markdown_pr_includes_raw_when_flag_set():
    fanout = _stub_fanout(_three_validator_results())
    report = run_consensus_review(
        target="auth.py",
        content="x",
        validators=["claude", "gemini", "codex"],
        fanout=fanout,
    )
    out = render_report(report, output_format="markdown-pr", include_raw=True)
    assert "<details><summary>raw response — claude</summary>" in out
    assert "auth.py:42" in out


def test_render_markdown_pr_fallback_with_include_raw_emits_details():
    fanout = _stub_fanout([("claude", "freeform prose, no structure", None)])
    report = run_consensus_review(
        target="x.py",
        content="x",
        validators=["claude"],
        fanout=fanout,
    )
    out = render_report(report, output_format="markdown-pr", include_raw=True)
    assert "<details>" in out
    assert "freeform prose" in out


def test_run_consensus_review_dissenting_includes_silent_validators():
    fanout = _stub_fanout(
        [
            ("claude", CLAUDE_OUT, None),
        ]
    )
    report = run_consensus_review(
        target="auth.py",
        content="x",
        validators=["claude", "gemini", "codex"],
        fanout=fanout,
    )
    finding = report.scored[0]
    assert sorted(finding.dissenting) == ["codex", "gemini"]
    assert finding.total_validators == 3
