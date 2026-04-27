"""SARIF emitter tests for ``lope.sarif``.

Pins the SARIF v2.1.0 envelope shape major scanners care about: top-level
``$schema`` / ``version`` / ``runs``; per-run ``tool.driver`` metadata;
per-result ``ruleId``, ``level``, ``message``, ``locations``, and
``properties`` payload. Also pins the severity → SARIF level mapping and
deterministic rule extraction for the lope.<category>.<severity> id scheme.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lope import sarif
from lope.findings import (
    ConsensusFinding,
    ConsensusLevel,
    Finding,
    merge_findings,
    score_consensus,
)


def _consensus(
    *,
    severity="high",
    category="security",
    file="auth.py",
    line=42,
    detected=("claude", "gemini"),
    confidence=0.8,
    score=0.85,
    agreement_count=2,
    total_validators=3,
    level=ConsensusLevel.CONFIRMED,
    message="missing rate limit on login endpoint",
):
    return ConsensusFinding(
        message=message,
        file=file,
        line=line,
        severity=severity,
        category=category,
        detected_by=list(detected),
        evidence={name: f"{name} saw something" for name in detected},
        confidence_max=confidence,
        confidence_avg=confidence,
        agreement_count=agreement_count,
        total_validators=total_validators,
        agreement_ratio=agreement_count / max(total_validators, 1),
        consensus_score=score,
        consensus_level=level,
        dissenting=[],
    )


def test_severity_to_sarif_level_mapping():
    assert sarif.severity_to_sarif_level("critical") == "error"
    assert sarif.severity_to_sarif_level("high") == "error"
    assert sarif.severity_to_sarif_level("medium") == "warning"
    assert sarif.severity_to_sarif_level("low") == "note"
    assert sarif.severity_to_sarif_level("info") == "note"
    assert sarif.severity_to_sarif_level("garbage") == "note"
    assert sarif.severity_to_sarif_level("") == "note"


def test_rule_id_combines_category_and_severity():
    f = _consensus(category="security", severity="high")
    assert sarif.rule_id_for(f) == "lope.security.high"
    assert sarif.rule_id_for(_consensus(category=None, severity="critical")) == "lope.uncategorized.critical"


def test_finding_to_result_emits_required_fields():
    finding = _consensus()
    result = sarif.finding_to_result(finding)
    assert result["ruleId"] == "lope.security.high"
    assert result["level"] == "error"
    assert "consensus" in result["message"]["text"]
    assert "agreement 2/3" in result["message"]["text"]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "auth.py"
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 42
    props = result["properties"]
    for key in (
        "detected_by",
        "consensus_score",
        "agreement_ratio",
        "agreement_count",
        "total_validators",
        "confidence",
        "severity",
        "category",
        "consensus_level",
        "dissenting",
    ):
        assert key in props
    assert props["detected_by"] == ["claude", "gemini"]


def test_finding_to_result_omits_locations_when_no_file():
    finding = _consensus(file=None, line=None)
    result = sarif.finding_to_result(finding)
    assert "locations" not in result


def test_finding_to_result_handles_end_line():
    finding = ConsensusFinding(
        message="block needs refactor",
        file="x.py",
        line=10,
        end_line=22,
        severity="medium",
        category="correctness",
        detected_by=["a"],
        confidence_max=0.6,
        agreement_count=1,
        total_validators=2,
        agreement_ratio=0.5,
        consensus_score=0.3,
        consensus_level=ConsensusLevel.NEEDS_VERIFICATION,
    )
    region = sarif.finding_to_result(finding)["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] == 10
    assert region["endLine"] == 22


def test_build_rules_is_unique_and_sorted():
    findings = [
        _consensus(category="security", severity="high"),
        _consensus(category="security", severity="high"),
        _consensus(category="correctness", severity="medium"),
        _consensus(category="security", severity="critical"),
    ]
    rules = sarif.build_rules(findings)
    ids = [r["id"] for r in rules]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert "lope.security.high" in ids
    assert "lope.security.critical" in ids
    assert "lope.correctness.medium" in ids


def test_build_sarif_envelope_shape():
    findings = [_consensus()]
    document = sarif.build_sarif(findings, tool_version="0.7.0")
    assert document["$schema"] == sarif.SARIF_SCHEMA
    assert document["version"] == "2.1.0"
    assert isinstance(document["runs"], list) and len(document["runs"]) == 1
    run = document["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "lope"
    assert driver["version"] == "0.7.0"
    assert driver["informationUri"].startswith("https://")
    assert isinstance(driver["rules"], list)
    assert len(run["results"]) == 1


def test_dumps_returns_valid_sortable_json():
    findings = [_consensus(), _consensus(category="correctness", severity="medium")]
    text = sarif.dumps(findings)
    parsed = json.loads(text)
    assert parsed["version"] == "2.1.0"
    assert len(parsed["runs"][0]["results"]) == 2
    # Sorted keys make the output stable across runs.
    assert text == sarif.dumps(findings)


def test_dumps_redacts_secrets_in_message_text():
    secret_message = "leaks Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    finding = _consensus(message=secret_message)
    document = sarif.build_sarif([finding])
    text = json.dumps(document)
    assert "abcdefghijklmnop" not in text


def test_full_pipeline_into_sarif_matches_real_findings():
    text = "- [HIGH] auth.py:42 — Missing rate limit (confidence: 0.86)"
    findings = [
        Finding(
            message="Missing rate limit",
            validator="claude",
            file="auth.py",
            line=42,
            severity="high",
            confidence=0.86,
            category="security",
        ),
        Finding(
            message="Missing rate limit",
            validator="gemini",
            file="auth.py",
            line=42,
            severity="high",
            confidence=0.78,
            category="security",
        ),
    ]
    merged = merge_findings(findings)
    scored = score_consensus(merged, ["claude", "gemini", "codex"])
    document = sarif.build_sarif(scored)
    result = document["runs"][0]["results"][0]
    assert result["ruleId"] == "lope.security.high"
    assert result["level"] == "error"
    assert result["properties"]["agreement_count"] == 2
    assert result["properties"]["total_validators"] == 3
    assert "claude" in result["properties"]["detected_by"]
