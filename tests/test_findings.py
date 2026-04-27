"""Unit tests for ``lope.findings``.

These tests pin the v0.7 finding contract: parser coverage across the four
supported shapes (bracket bullet, severity prefix, category prefix, JSON
array, fallback bullet), the three deduplication rules, the consensus
classification thresholds, deterministic sort order, redaction integration,
and Python 3.9-compatible imports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lope.findings import (
    ConsensusFinding,
    ConsensusLevel,
    Finding,
    FindingParseResult,
    MergedFinding,
    format_consensus_markdown,
    merge_findings,
    parse_findings,
    score_consensus,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "validator_outputs"


def _load_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------


def test_finding_normalizes_severity_synonyms():
    assert Finding(message="x", validator="v", severity="blocker").severity == "critical"
    assert Finding(message="x", validator="v", severity="WARNING").severity == "medium"
    assert Finding(message="x", validator="v", severity="nit").severity == "low"
    assert Finding(message="x", validator="v", severity="note").severity == "info"
    assert Finding(message="x", validator="v", severity="garbage").severity == "info"


def test_finding_clamps_confidence():
    assert Finding(message="x", validator="v", confidence=-0.5).confidence == 0.0
    assert Finding(message="x", validator="v", confidence=1.5).confidence == 1.0
    assert Finding(message="x", validator="v", confidence=0.42).confidence == 0.42
    assert Finding(message="x", validator="v", confidence="not-a-number").confidence == 0.5


def test_finding_redacts_raw_and_evidence_secrets():
    secret = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    f = Finding(
        message="missing rate limit",
        validator="claude",
        raw=f"snippet with {secret}",
        evidence=f"-> {secret}",
    )
    assert "abcdefghijklmnop" not in f.raw
    assert "Bearer <redacted>" in f.raw
    assert "abcdefghijklmnop" not in f.evidence


def test_finding_normalizes_category_synonyms():
    assert Finding(message="x", validator="v", category="performance").category == "perf"
    assert Finding(message="x", validator="v", category="DOC").category == "docs"
    assert Finding(message="x", validator="v", category=None).category is None


def test_finding_coerces_line_numbers():
    f = Finding(message="x", validator="v", line="42", end_line="50")
    assert f.line == 42
    assert f.end_line == 50
    f2 = Finding(message="x", validator="v", line="oops")
    assert f2.line is None


def test_finding_hash_is_stable_and_short():
    a = Finding(message="missing rate limit", validator="claude", file="auth.py", line=42)
    b = Finding(message="MISSING RATE LIMIT", validator="gemini", file="auth.py", line=42)
    assert a.hash == b.hash
    assert len(a.hash) == 16


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_handles_empty_input():
    result = parse_findings("", "anyone")
    assert result.method == "empty"
    assert len(result) == 0
    assert not result


def test_parse_bracket_bullets_from_claude_fixture():
    text = _load_fixture("claude_review.txt")
    result = parse_findings(text, "claude")
    assert result.method == "structured"
    assert len(result) == 3
    sevs = sorted(f.severity for f in result.findings)
    assert sevs == ["high", "low", "medium"]
    files = sorted(f.file or "" for f in result.findings)
    assert files == ["auth.py", "auth.py", "docs/auth.md"]
    high = [f for f in result.findings if f.severity == "high"][0]
    assert high.line == 42
    assert "rate limiting" in high.message.lower()
    assert high.confidence == 0.86


def test_parse_severity_prefix_and_category_prefix_from_gemini_fixture():
    text = _load_fixture("gemini_review.txt")
    result = parse_findings(text, "gemini")
    assert result.method == "structured"
    assert len(result) == 3
    by_file = {(f.file, f.line): f for f in result.findings}
    critical = by_file[("middleware/auth.go", 142)]
    assert critical.severity == "critical"
    assert "token expiry" in critical.message.lower()
    sec = by_file[("auth.py", 42)]
    assert sec.category == "security"
    assert sec.severity == "info"
    medium = by_file[("auth.py", 88)]
    assert medium.severity == "medium"
    assert "expired token" in medium.message.lower()


def test_parse_json_array_from_codex_fixture():
    text = _load_fixture("codex_review.txt")
    result = parse_findings(text, "codex")
    assert result.method == "json"
    assert len(result) == 2
    rate_limit = [f for f in result.findings if f.line == 42][0]
    assert rate_limit.severity == "high"
    assert rate_limit.confidence == 0.82
    assert rate_limit.file == "auth.py"
    nit = [f for f in result.findings if f.line == 12][0]
    assert nit.severity == "low"
    assert nit.confidence == 0.40


def test_parse_attaches_source_file_when_finding_has_no_file():
    text = "- [HIGH] missing input validation"
    result = parse_findings(text, "claude", source_file="server.py")
    assert len(result) == 1
    assert result.findings[0].file == "server.py"
    assert result.findings[0].severity == "high"


def test_parse_fallback_bullet_emits_info_finding():
    text = "- something looks off in the retry loop"
    result = parse_findings(text, "stub")
    assert result.method == "fallback"
    assert result.fallback_used is True
    assert len(result) == 1
    f = result.findings[0]
    assert f.severity == "info"
    assert f.file is None
    assert "retry loop" in f.message.lower()


def test_parse_mixes_structured_and_fallback():
    text = "- [HIGH] auth.py:1 — broken\n- nothing structured here"
    result = parse_findings(text, "stub")
    assert result.method == "mixed"
    assert len(result) == 2
    assert result.fallback_used is True


def test_parser_redacts_secrets_in_raw_and_evidence():
    secret = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    text = f"- [HIGH] auth.py:42 — leaks {secret} (confidence: 0.9)"
    result = parse_findings(text, "claude")
    assert len(result) == 1
    f = result.findings[0]
    assert "abcdefghijklmnop" not in f.raw
    assert "abcdefghijklmnop" not in f.evidence
    assert "abcdefghijklmnop" not in f.message


def test_parser_recovers_json_array_after_prose_with_brackets():
    text = (
        "Here is my analysis:\n"
        "- [HIGH] auth.py:42 — missing rate limit\n"
        '\n[{"severity": "high", "file": "auth.py", "line": 42, "message": "no limiter", "confidence": 0.9}]'
    )
    result = parse_findings(text, "claude")
    assert result.method == "json"
    assert len(result) == 1
    f = result.findings[0]
    assert f.file == "auth.py"
    assert f.line == 42
    assert f.severity == "high"
    assert f.confidence == pytest.approx(0.9)


def test_parser_handles_json_object_with_findings_key():
    text = '{"findings": [{"severity": "high", "file": "a.py", "line": 5, "message": "boom"}]}'
    result = parse_findings(text, "anyone")
    assert result.method == "json"
    assert len(result) == 1
    f = result.findings[0]
    assert f.file == "a.py"
    assert f.line == 5
    assert f.severity == "high"


def test_parser_extracts_at_least_eighty_percent_of_fixtures():
    fixtures = {
        "claude": _load_fixture("claude_review.txt"),
        "gemini": _load_fixture("gemini_review.txt"),
        "codex": _load_fixture("codex_review.txt"),
    }
    parsed = sum(len(parse_findings(text, name)) for name, text in fixtures.items())
    assert parsed >= 8  # 3 + 3 + 2 raw findings, all expected to be parsed


def test_parser_handles_none_input():
    assert len(parse_findings(None, "v")) == 0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _f(message, validator, **kw):
    return Finding(message=message, validator=validator, **kw)


def test_dedup_exact_duplicate_collapses():
    findings = [
        _f("Missing rate limiter", "claude", file="auth.py", line=42, severity="high", confidence=0.8),
        _f("missing rate limiter", "gemini", file="auth.py", line=42, severity="high", confidence=0.7),
    ]
    merged = merge_findings(findings)
    assert len(merged) == 1
    assert sorted(merged[0].detected_by) == ["claude", "gemini"]


def test_dedup_paraphrase_same_line_collapses():
    findings = [
        _f("Missing rate limit on login", "claude", file="auth.py", line=42, severity="high", confidence=0.8),
        _f("Missing rate limit on login route", "gemini", file="auth.py", line=43, severity="high", confidence=0.6),
    ]
    merged = merge_findings(findings)
    assert len(merged) == 1
    assert sorted(merged[0].detected_by) == ["claude", "gemini"]


def test_dedup_message_only_same_family_cross_file_collapses():
    findings = [
        _f("missing rate limiter on login", "claude", file="auth.py", line=42, severity="high"),
        _f("missing rate limiter on login", "gemini", file="other.py", line=99, severity="high"),
    ]
    merged = merge_findings(findings)
    assert len(merged) == 1
    assert sorted(merged[0].detected_by) == ["claude", "gemini"]


def test_dedup_does_not_collapse_unrelated_messages_at_same_line():
    findings = [
        _f("Missing rate limiter on login", "claude", file="auth.py", line=42, severity="high"),
        _f("Wrong status code emitted on success", "gemini", file="auth.py", line=42, severity="medium"),
    ]
    merged = merge_findings(findings)
    assert len(merged) == 2


def test_dedup_keeps_highest_severity():
    findings = [
        _f("token expiry boundary missing", "claude", file="auth.py", line=88, severity="low", confidence=0.5),
        _f("token expiry boundary missing", "gemini", file="auth.py", line=88, severity="critical", confidence=0.7),
    ]
    merged = merge_findings(findings)
    assert len(merged) == 1
    assert merged[0].severity == "critical"


def test_dedup_keeps_max_and_avg_confidence():
    findings = [
        _f("issue", "a", file="x.py", line=1, severity="medium", confidence=0.4),
        _f("issue", "b", file="x.py", line=1, severity="medium", confidence=0.9),
        _f("issue", "c", file="x.py", line=1, severity="medium", confidence=0.6),
    ]
    merged = merge_findings(findings)
    assert len(merged) == 1
    assert merged[0].confidence_max == pytest.approx(0.9)
    assert merged[0].confidence_avg == pytest.approx((0.4 + 0.9 + 0.6) / 3)


def test_dedup_collects_per_validator_evidence_snippets():
    findings = [
        _f("issue", "a", file="x.py", line=1, severity="medium", evidence="claude says: bad"),
        _f("issue", "b", file="x.py", line=1, severity="medium", evidence="gemini says: bad"),
    ]
    merged = merge_findings(findings)
    assert set(merged[0].evidence.keys()) == {"a", "b"}
    assert "claude says" in merged[0].evidence["a"]
    assert "gemini says" in merged[0].evidence["b"]


def test_dedup_result_is_sorted_by_severity_then_file_then_line():
    findings = [
        _f("a", "v", file="b.py", line=10, severity="low"),
        _f("b", "v", file="a.py", line=20, severity="critical"),
        _f("c", "v", file="a.py", line=10, severity="critical"),
    ]
    merged = merge_findings(findings)
    assert [(m.severity, m.file, m.line) for m in merged] == [
        ("critical", "a.py", 10),
        ("critical", "a.py", 20),
        ("low", "b.py", 10),
    ]


def test_dedup_does_not_double_count_same_validator():
    findings = [
        _f("issue", "claude", file="x.py", line=1, severity="medium", confidence=0.5),
        _f("issue", "claude", file="x.py", line=1, severity="medium", confidence=0.7),
    ]
    merged = merge_findings(findings)
    assert len(merged) == 1
    assert merged[0].detected_by == ["claude"]
    # Confidence list still records both detections so avg reflects them.
    assert len(merged[0].confidences) == 2


# ---------------------------------------------------------------------------
# Consensus scoring
# ---------------------------------------------------------------------------


def test_consensus_score_formula_is_ratio_times_max_confidence():
    findings = [
        _f("issue", "a", file="x.py", line=1, severity="high", confidence=0.8),
        _f("issue", "b", file="x.py", line=1, severity="high", confidence=0.9),
        _f("issue", "c", file="x.py", line=1, severity="high", confidence=0.6),
    ]
    merged = merge_findings(findings)
    scored = score_consensus(merged, ["a", "b", "c"])
    assert len(scored) == 1
    s = scored[0]
    assert s.agreement_count == 3
    assert s.total_validators == 3
    assert s.agreement_ratio == pytest.approx(1.0)
    assert s.consensus_score == pytest.approx(0.9)
    assert s.consensus_level == ConsensusLevel.CONFIRMED
    assert s.dissenting == []


def test_consensus_level_likely_threshold():
    findings = [
        _f("issue", "a", file="x.py", line=1, severity="medium", confidence=0.6),
        _f("issue", "b", file="x.py", line=1, severity="medium", confidence=0.6),
    ]
    merged = merge_findings(findings)
    scored = score_consensus(merged, ["a", "b", "c", "d", "e"])
    # 2/5 = 0.40 ratio AND confidence 0.6 ≥ 0.55 → LIKELY
    assert scored[0].consensus_level == ConsensusLevel.LIKELY


def test_consensus_level_needs_verification_for_high_severity_singletons():
    findings = [_f("crit", "a", file="x.py", line=1, severity="critical", confidence=0.5)]
    merged = merge_findings(findings)
    scored = score_consensus(merged, ["a", "b", "c", "d", "e"])
    # 1/5 = 0.20 → still hits the ≥0.20 OR severity-high path → NEEDS_VERIFICATION
    assert scored[0].consensus_level == ConsensusLevel.NEEDS_VERIFICATION


def test_consensus_level_unverified_for_lone_low_finding():
    findings = [_f("nit", "a", file="x.py", line=1, severity="low", confidence=0.3)]
    merged = merge_findings(findings)
    scored = score_consensus(merged, ["a", "b", "c", "d", "e", "f"])
    assert scored[0].consensus_level == ConsensusLevel.UNVERIFIED


def test_consensus_dissenting_lists_validators_who_missed_finding():
    findings = [_f("issue", "a", file="x.py", line=1, severity="high", confidence=0.7)]
    merged = merge_findings(findings)
    scored = score_consensus(merged, ["a", "b", "c"])
    assert scored[0].dissenting == ["b", "c"]


def test_consensus_sort_is_deterministic_across_runs():
    findings = [
        _f("z bug", "a", file="b.py", line=2, severity="medium", confidence=0.6),
        _f("z bug", "b", file="b.py", line=2, severity="medium", confidence=0.6),
        _f("y bug", "a", file="a.py", line=1, severity="high", confidence=0.8),
        _f("y bug", "b", file="a.py", line=1, severity="high", confidence=0.8),
    ]
    merged = merge_findings(findings)
    scored1 = [f.hash for f in score_consensus(merged, ["a", "b", "c"])]
    scored2 = [f.hash for f in score_consensus(merged, ["a", "b", "c"])]
    assert scored1 == scored2
    # First entry must be the high-severity one.
    first = score_consensus(merged, ["a", "b", "c"])[0]
    assert first.severity == "high"


def test_consensus_handles_empty_roster():
    findings = [_f("x", "a", file="x.py", line=1, severity="low")]
    merged = merge_findings(findings)
    scored = score_consensus(merged, [])
    # Empty roster collapses to total_validators = 1 to avoid division by zero.
    assert scored[0].total_validators == 1


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


def test_format_consensus_markdown_includes_required_fields():
    findings = [
        _f("missing rate limiter on login", "claude", file="auth.py", line=42, severity="high", confidence=0.85, evidence="claude saw missing limiter"),
        _f("missing rate limiter on login", "gemini", file="auth.py", line=42, severity="high", confidence=0.80, evidence="gemini saw missing limiter"),
    ]
    merged = merge_findings(findings)
    scored = score_consensus(merged, ["claude", "gemini", "codex"])
    md = format_consensus_markdown(scored)
    assert "## CONFIRMED" in md
    assert "auth.py:42" in md
    assert "claude" in md and "gemini" in md
    assert "consensus:" in md
    assert "agreement: 2/3" in md
    assert "evidence:" in md


def test_format_consensus_markdown_redacts_secrets_in_evidence():
    secret_line = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    f = _f(
        "leaks credentials",
        "claude",
        file="auth.py",
        line=10,
        severity="high",
        evidence=secret_line,
    )
    merged = merge_findings([f])
    scored = score_consensus(merged, ["claude"])
    md = format_consensus_markdown(scored)
    assert "abcdefghijklmnop" not in md
    assert "Bearer <redacted>" in md


def test_format_consensus_markdown_handles_empty_input():
    assert format_consensus_markdown([]) == "No consensus findings.\n"


# ---------------------------------------------------------------------------
# Full pipeline against fixtures
# ---------------------------------------------------------------------------


def test_full_pipeline_against_fixtures_is_deterministic():
    fixtures = [
        ("claude", _load_fixture("claude_review.txt")),
        ("gemini", _load_fixture("gemini_review.txt")),
        ("codex", _load_fixture("codex_review.txt")),
    ]
    all_findings = []
    for name, text in fixtures:
        all_findings.extend(parse_findings(text, name).findings)
    assert len(all_findings) == 8

    merged = merge_findings(all_findings)
    assert 1 <= len(merged) <= 8

    scored = score_consensus(merged, ["claude", "gemini", "codex"])
    md = format_consensus_markdown(scored)
    # Deterministic re-render is byte-identical.
    md2 = format_consensus_markdown(score_consensus(merge_findings(all_findings), ["claude", "gemini", "codex"]))
    assert md == md2
    # Every parsed file shows up somewhere.
    for f in scored:
        if f.file:
            assert f.file in md


def test_full_pipeline_returns_consensus_findings_with_all_required_fields():
    text = _load_fixture("claude_review.txt")
    findings = parse_findings(text, "claude").findings
    scored = score_consensus(merge_findings(findings), ["claude", "gemini", "codex"])
    sample = scored[0]
    payload = sample.to_dict()
    for key in (
        "message",
        "file",
        "severity",
        "category",
        "detected_by",
        "evidence",
        "confidence_max",
        "confidence_avg",
        "agreement_count",
        "total_validators",
        "agreement_ratio",
        "consensus_score",
        "consensus_level",
        "dissenting",
    ):
        assert key in payload


# ---------------------------------------------------------------------------
# Python 3.9 compatibility smoke test
# ---------------------------------------------------------------------------


def test_module_import_smoke():
    import lope.findings as mod

    assert hasattr(mod, "Finding")
    assert hasattr(mod, "MergedFinding")
    assert hasattr(mod, "ConsensusFinding")
    assert hasattr(mod, "ConsensusLevel")
    assert hasattr(mod, "FindingParseResult")
    assert hasattr(mod, "parse_findings")
    assert hasattr(mod, "merge_findings")
    assert hasattr(mod, "score_consensus")
    assert hasattr(mod, "format_consensus_markdown")


def test_finding_parse_result_iter_and_len():
    result = FindingParseResult(findings=[Finding(message="x", validator="v")], method="structured", parsed_count=1)
    assert len(result) == 1
    assert list(result)[0].message == "x"
    assert bool(result)
