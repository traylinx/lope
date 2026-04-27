"""Tests for ``lope.exporters`` — review-report passthroughs.

These wrappers must match the Phase 3 renderer output byte-for-byte;
the tests here pin that contract so a future refactor of the renderers
cannot silently diverge from the export surface.
"""

from __future__ import annotations

import json

from lope.exporters import export_markdown_pr, export_sarif
from lope.findings import ConsensusFinding, ConsensusLevel
from lope.review import ReviewReport


def _consensus_report():
    finding = ConsensusFinding(
        message="missing rate limit",
        file="auth.py",
        line=42,
        severity="high",
        category="security",
        detected_by=["claude", "gemini"],
        evidence={"claude": "saw it", "gemini": "saw it"},
        confidence_max=0.85,
        confidence_avg=0.85,
        agreement_count=2,
        total_validators=3,
        agreement_ratio=2 / 3,
        consensus_score=0.85,
        consensus_level=ConsensusLevel.CONFIRMED,
        dissenting=["codex"],
    )
    return ReviewReport(
        target="auth.py",
        focus="security",
        validators=["claude", "gemini", "codex"],
        raw_results=[],
        parse_methods={"claude": "structured", "gemini": "structured"},
        findings=[],
        merged=[],
        scored=[finding],
        errors=[],
        raw_count=2,
        merged_count=1,
        fallback=False,
    )


def test_export_markdown_pr_matches_phase3_renderer():
    from lope.review import render_report

    report = _consensus_report()
    direct = render_report(report, output_format="markdown-pr")
    via_export = export_markdown_pr(report)
    assert direct == via_export


def test_export_sarif_returns_valid_envelope():
    out = export_sarif(_consensus_report())
    parsed = json.loads(out)
    assert parsed["version"] == "2.1.0"
    assert parsed["runs"][0]["tool"]["driver"]["name"] == "lope"
    assert parsed["runs"][0]["results"]


def test_export_markdown_pr_include_raw_threads_through():
    from lope.review import render_report

    report = _consensus_report()
    expected = render_report(report, output_format="markdown-pr", include_raw=True)
    actual = export_markdown_pr(report, include_raw=True)
    assert expected == actual
