"""Tests for ``lope.exporters`` — AGTX task spec + review-report passthroughs.

The AGTX exporter is pure text — no LLM, no network. Tests pin
deterministic output and section ordering, exhaustive redaction, and
that the passthroughs match the Phase 3 renderer output byte-for-byte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lope import __version__ as LOPE_VERSION
from lope.exporters import (
    AgtxTask,
    export_agtx_task,
    export_markdown_pr,
    export_sarif,
    write_agtx_task,
)
from lope.findings import ConsensusFinding, ConsensusLevel
from lope.review import ReviewReport


# ---------------------------------------------------------------------------
# AGTX export
# ---------------------------------------------------------------------------


def test_export_agtx_task_uses_required_section_order():
    task = export_agtx_task(
        "source body",
        title="Add JWT auth",
        source_label="SPRINT-JWT-AUTH.md",
    )
    body = task.body
    headers = [
        "## Source",
        "## Objective",
        "## Phases",
        "## Acceptance Criteria",
        "## Suggested Plugin",
        "## Lope Validation Requirements",
    ]
    last = -1
    for h in headers:
        idx = body.find(h)
        assert idx != -1, f"Missing header: {h}"
        assert idx > last, f"Section {h} out of order"
        last = idx


def test_export_agtx_task_is_deterministic():
    a = export_agtx_task("body", title="x", source_label="src.md")
    b = export_agtx_task("body", title="x", source_label="src.md")
    assert a.body == b.body


def test_export_agtx_task_includes_lope_version_provenance():
    task = export_agtx_task("body", title="x", source_label="src.md")
    assert f"Lope v{LOPE_VERSION}" in task.body


def test_export_agtx_task_uses_provided_objective_and_phases():
    task = export_agtx_task(
        "raw",
        title="Migration",
        source_label="MIGRATION.md",
        objective="Cut over from MySQL to Postgres without downtime.",
        phases=["Stand up replica", "Dual-write", "Cut over reads", "Decommission MySQL"],
        acceptance_criteria=["No P1 incidents", "Read latency stable", "Replica lag < 1s"],
    )
    assert "Cut over from MySQL" in task.body
    assert "Stand up replica" in task.body
    assert "Read latency stable" in task.body


def test_export_agtx_task_falls_back_when_optional_sections_missing():
    task = export_agtx_task(
        "body",
        title="x",
        source_label="src.md",
    )
    assert "## Phases" in task.body
    assert "Inherit phase ordering" in task.body
    assert "## Acceptance Criteria" in task.body


def test_export_agtx_task_redacts_every_text_input():
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    task = export_agtx_task(
        f"raw body with {secret}",
        title=f"Add JWT {secret}",
        source_label=f"SPRINT-{secret}.md",
        objective=f"Roll out {secret}",
        phases=[f"Use {secret}"],
        acceptance_criteria=[f"Verify {secret} not logged"],
        validation_command=f"lope review src/ --consensus  # {secret}",
        suggested_plugin=f"agtx-{secret}",
        extra_sections={"Notes": f"Watch for {secret} regressions."},
    )
    assert "abcdefghijklmnop" not in task.body
    assert "Bearer <redacted>" in task.body or "<redacted>" in task.body


def test_export_agtx_task_includes_extra_sections_after_required_ones():
    task = export_agtx_task(
        "body",
        title="x",
        source_label="src.md",
        extra_sections={"Risk Notes": "- DB lock during migration"},
    )
    assert "## Risk Notes" in task.body
    risk_idx = task.body.index("## Risk Notes")
    val_idx = task.body.index("## Lope Validation Requirements")
    assert risk_idx > val_idx


def test_export_agtx_task_default_validation_command_is_lope_review():
    task = export_agtx_task("body", title="x", source_label="src.md")
    assert "lope review" in task.body
    assert "--consensus" in task.body


def test_export_agtx_task_returns_dataclass_with_metadata():
    task = export_agtx_task(
        "body",
        title="JWT auth",
        source_label="SPRINT-JWT-AUTH.md",
    )
    assert isinstance(task, AgtxTask)
    assert task.title == "JWT auth"
    assert task.source_label == "SPRINT-JWT-AUTH.md"
    assert task.suggested_plugin == "agtx"
    payload = task.to_dict()
    assert payload["title"] == "JWT auth"
    assert int(payload["body_chars"]) > 0


def test_write_agtx_task_creates_parent_dirs(tmp_path):
    task = export_agtx_task("body", title="x", source_label="src.md")
    target = tmp_path / "deep" / "nested" / "out.md"
    written = write_agtx_task(task, target)
    assert written == target
    assert target.read_text(encoding="utf-8") == task.body


# ---------------------------------------------------------------------------
# Review-report passthroughs
# ---------------------------------------------------------------------------


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
