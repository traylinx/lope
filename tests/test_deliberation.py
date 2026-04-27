"""Tests for ``lope.deliberation`` — council protocol, anonymized critique,
template rubric scoring, minority report, and trace JSONL hygiene.

Validators are stub callables; no real CLI ever runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest

from lope.deliberation import (
    DEPTHS,
    CouncilTurn,
    DeliberationRun,
    RubricVerdict,
    TemplateSpec,
    build_critique_prompt,
    build_position_prompt,
    build_rubric_prompt,
    build_synthesis_prompt,
    default_output_dir,
    get_template,
    list_templates,
    parse_rubric_response,
    run_deliberation,
    write_run,
)


# ---------------------------------------------------------------------------
# Stub generators
# ---------------------------------------------------------------------------


def _record_calls():
    calls: List[Dict[str, str]] = []

    def gen(name, prompt, timeout):
        calls.append({"validator": name, "prompt": prompt, "timeout": timeout})
        if "critique" in prompt.lower() and "Peer positions" in prompt:
            return f"Critique by {name}: response A is missing X."
        if "revising" in prompt.lower() or "revised version" in prompt.lower():
            return f"Revision by {name}: addressed peer feedback."
        if "synthesizing" in prompt.lower() or "synthesizing the council" in prompt.lower() or "Required section headings" in prompt:
            return (
                "## Context\nbody\n## Decision\nadopt approach Y\n"
                "## Consequences\nupside; downside\n"
                "## Alternatives Considered\noption A; option B"
            )
        if "score" in prompt.lower() and "rubric" in prompt.lower():
            return "VERDICT: PASS\nSEVERITY: low\n- minor wording fix"
        return f"Position by {name}: I propose Y because Z."

    return calls, gen


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_list_templates_contains_all_v07_kinds():
    names = set(list_templates())
    assert {
        "adr",
        "prd",
        "rfc",
        "build-vs-buy",
        "migration-plan",
        "incident-review",
    } <= names


def test_get_template_returns_spec():
    spec = get_template("adr")
    assert isinstance(spec, TemplateSpec)
    assert spec.name == "adr"
    assert spec.title == "Architecture Decision Record"
    assert "Decision" in spec.sections
    assert spec.rubric  # non-empty


def test_get_template_raises_on_unknown_name():
    with pytest.raises(KeyError):
        get_template("not-a-template")


def test_get_template_is_case_insensitive():
    assert get_template("ADR").name == "adr"
    assert get_template("Build-Vs-Buy").name == "build-vs-buy"


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def test_build_position_prompt_includes_scenario_and_template():
    spec = get_template("adr")
    prompt = build_position_prompt(spec, "Should we adopt JWT?")
    assert "Should we adopt JWT?" in prompt
    assert "Architecture Decision Record" in prompt or "ADR" in prompt
    assert "Context" in prompt and "Decision" in prompt


def test_build_critique_prompt_does_not_leak_validator_names():
    spec = get_template("adr")
    peer_block = "[Response A]\nclaude said X\n[Response B]\ngemini said Y"
    out = build_critique_prompt(spec, "scenario", peer_block)
    # Names that appear in the peer_block can show through (the test injects
    # them on purpose); the orchestrator's job is to NOT inject names — that
    # is verified separately. Here we just confirm anonymous markers exist.
    assert "Peer positions (anonymized)" in out
    assert "labels are stripped on purpose" in out


def test_build_rubric_prompt_specifies_reply_format():
    spec = get_template("adr")
    out = build_rubric_prompt(spec, "## Decision\nadopt X")
    assert "VERDICT" in out
    assert "SEVERITY" in out
    assert "objection" in out.lower()


def test_build_synthesis_prompt_lists_required_sections():
    spec = get_template("prd")
    out = build_synthesis_prompt(spec, "scenario", "[Response A]\nrev")
    for section in spec.sections:
        assert section in out


# ---------------------------------------------------------------------------
# Rubric parsing
# ---------------------------------------------------------------------------


def test_parse_rubric_pass_with_minor_objections():
    status, severity, objections = parse_rubric_response(
        "VERDICT: PASS\nSEVERITY: low\n- minor typo\n- align section header"
    )
    assert status == "PASS"
    assert severity == "low"
    assert "minor typo" in objections


def test_parse_rubric_needs_fix_high_severity():
    status, severity, objections = parse_rubric_response(
        "VERDICT: NEEDS_FIX\nSEVERITY: HIGH\n- decision rationale missing"
    )
    assert status == "NEEDS_FIX"
    assert severity == "high"
    assert objections == ["decision rationale missing"]


def test_parse_rubric_defaults_to_needs_fix_when_unparseable():
    status, severity, _ = parse_rubric_response("just some prose, no verdict")
    assert status == "NEEDS_FIX"
    assert severity == "medium"


def test_parse_rubric_redacts_objection_secrets():
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    status, _, objections = parse_rubric_response(
        f"VERDICT: NEEDS_FIX\nSEVERITY: high\n- leaks {secret}"
    )
    assert status == "NEEDS_FIX"
    assert all("abcdefghijklmnop" not in o for o in objections)


def test_parse_rubric_collapses_critical_to_high():
    _, severity, _ = parse_rubric_response("VERDICT: NEEDS_FIX\nSEVERITY: critical\n- x")
    assert severity == "high"


# ---------------------------------------------------------------------------
# run_deliberation orchestration
# ---------------------------------------------------------------------------


def test_run_deliberation_quick_skips_critique_and_revision():
    calls, gen = _record_calls()
    run = run_deliberation(
        template=get_template("adr"),
        scenario="should we adopt JWT?",
        validators=["claude", "gemini"],
        primary="claude",
        generate=gen,
        depth="quick",
    )
    stages = {turn.stage for turn in run.turns}
    assert "critique" not in stages
    assert "revision" not in stages
    assert "position" in stages
    assert "synthesis" in stages
    assert "rubric" in stages


def test_run_deliberation_standard_full_protocol():
    calls, gen = _record_calls()
    run = run_deliberation(
        template=get_template("adr"),
        scenario="adopt JWT?",
        validators=["claude", "gemini", "codex"],
        primary="claude",
        generate=gen,
        depth="standard",
    )
    counts: Dict[str, int] = {}
    for turn in run.turns:
        counts[turn.stage] = counts.get(turn.stage, 0) + 1
    assert counts["position"] == 3
    assert counts["critique"] == 3
    assert counts["revision"] == 3
    assert counts["synthesis"] == 1
    assert counts["rubric"] == 3


def test_anonymized_critique_prompts_strip_validator_names():
    captured = []

    def gen(name, prompt, timeout):
        captured.append((name, prompt))
        if "Peer positions (anonymized)" in prompt:
            assert "claude" not in prompt
            assert "gemini" not in prompt
            assert "codex" not in prompt
        if "Synthesis to score" not in prompt and "Required section headings" not in prompt:
            return "Position content"
        return "## Context\n## Decision\n## Consequences\n## Alternatives Considered\nVERDICT: PASS"

    run_deliberation(
        template=get_template("adr"),
        scenario="x",
        validators=["claude", "gemini", "codex"],
        primary="claude",
        generate=gen,
        depth="standard",
    )
    # Sanity check that critique prompts were actually sent.
    critique_prompts = [p for _, p in captured if "Peer positions (anonymized)" in p]
    assert critique_prompts


def test_anonymized_critique_uses_response_labels():
    captured: Dict[str, str] = {}

    def gen(name, prompt, timeout):
        if "Peer positions (anonymized)" in prompt:
            captured[name] = prompt
        if "VERDICT" in prompt:
            return "VERDICT: PASS\nSEVERITY: low\n- ok"
        return "Position by " + name

    run_deliberation(
        template=get_template("adr"),
        scenario="x",
        validators=["claude", "gemini", "codex"],
        primary="claude",
        generate=gen,
        depth="standard",
    )
    # Each critique must see Response labels for at least two peers.
    assert captured
    sample = next(iter(captured.values()))
    assert "Response A" in sample or "Response B" in sample or "Response C" in sample


def test_run_deliberation_rejects_invalid_depth():
    _, gen = _record_calls()
    with pytest.raises(ValueError):
        run_deliberation(
            template=get_template("adr"),
            scenario="x",
            validators=["a"],
            primary="a",
            generate=gen,
            depth="ultra-deep",
        )


def test_run_deliberation_rejects_primary_not_in_validators():
    _, gen = _record_calls()
    with pytest.raises(ValueError):
        run_deliberation(
            template=get_template("adr"),
            scenario="x",
            validators=["a", "b"],
            primary="ghost",
            generate=gen,
        )


def test_run_deliberation_rejects_empty_validators():
    _, gen = _record_calls()
    with pytest.raises(ValueError):
        run_deliberation(
            template=get_template("adr"),
            scenario="x",
            validators=[],
            primary=None,
            generate=gen,
        )


# ---------------------------------------------------------------------------
# Minority report
# ---------------------------------------------------------------------------


def test_minority_report_lists_high_severity_objections():
    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return "## Context\n## Decision\n## Consequences\n## Alternatives Considered"
        if "VERDICT" in prompt:
            if name == "gemini":
                return (
                    "VERDICT: NEEDS_FIX\nSEVERITY: high\n"
                    "- decision rationale not stated\n- alternatives missing"
                )
            return "VERDICT: PASS\nSEVERITY: low\n- nothing major"
        return f"position {name}"

    run = run_deliberation(
        template=get_template("adr"),
        scenario="x",
        validators=["claude", "gemini", "codex"],
        primary="claude",
        generate=gen,
        depth="standard",
    )
    assert "decision rationale not stated" in run.minority_report
    assert run.minority_report.startswith("# Minority Report")


def test_minority_report_says_unanimous_when_all_pass():
    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return "## Context\n## Decision\n## Consequences\n## Alternatives Considered"
        if "VERDICT" in prompt:
            return "VERDICT: PASS\nSEVERITY: low\n- nit"
        return f"position {name}"

    run = run_deliberation(
        template=get_template("adr"),
        scenario="x",
        validators=["claude", "gemini"],
        primary="claude",
        generate=gen,
    )
    assert "unanimously" in run.minority_report.lower()


def test_minority_report_anonymous_by_default():
    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return "## Context\n## Decision\n## Consequences\n## Alternatives Considered"
        if "VERDICT" in prompt:
            if name == "gemini":
                return "VERDICT: NEEDS_FIX\nSEVERITY: high\n- bad"
            return "VERDICT: PASS\nSEVERITY: low\n- ok"
        return f"position {name}"

    run = run_deliberation(
        template=get_template("adr"),
        scenario="x",
        validators=["claude", "gemini"],
        primary="claude",
        generate=gen,
    )
    assert "gemini" not in run.minority_report
    assert "Response" in run.minority_report


def test_minority_report_can_be_de_anonymized():
    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return "## Context\n## Decision\n## Consequences\n## Alternatives Considered"
        if "VERDICT" in prompt:
            if name == "gemini":
                return "VERDICT: NEEDS_FIX\nSEVERITY: high\n- bad"
            return "VERDICT: PASS\nSEVERITY: low\n- ok"
        return f"position {name}"

    run = run_deliberation(
        template=get_template("adr"),
        scenario="x",
        validators=["claude", "gemini"],
        primary="claude",
        generate=gen,
        anonymous=False,
    )
    assert "gemini" in run.minority_report


# ---------------------------------------------------------------------------
# Output directory writer
# ---------------------------------------------------------------------------


def test_write_run_creates_expected_layout(tmp_path):
    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return "## Context\nC\n## Decision\nD\n## Consequences\nQ\n## Alternatives Considered\nA"
        if "VERDICT" in prompt:
            return "VERDICT: PASS\nSEVERITY: low\n- nit"
        return f"draft from {name}"

    out_dir = tmp_path / "lope-runs" / "20260427-adr"
    run = run_deliberation(
        template=get_template("adr"),
        scenario="adopt JWT?",
        validators=["claude", "gemini"],
        primary="claude",
        generate=gen,
        depth="standard",
        output_dir=out_dir,
    )

    assert (out_dir / "scenario.md").exists()
    assert (out_dir / "trace.jsonl").exists()
    assert (out_dir / "final" / "report.md").exists()
    assert (out_dir / "final" / "minority-report.md").exists()
    assert (out_dir / "final" / "decision-log.md").exists()
    positions = list((out_dir / "turns" / "01-positions").iterdir())
    critiques = list((out_dir / "turns" / "02-critiques").iterdir())
    revisions = list((out_dir / "turns" / "03-revisions").iterdir())
    assert len(positions) == 2
    assert len(critiques) == 2
    assert len(revisions) == 2

    report_text = (out_dir / "final" / "report.md").read_text()
    for section in get_template("adr").sections:
        assert section in report_text


def test_trace_jsonl_redacts_secrets(tmp_path):
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"

    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return f"## Context\n## Decision\nadopt {secret}\n## Consequences\n## Alternatives Considered"
        if "VERDICT" in prompt:
            return f"VERDICT: PASS\nSEVERITY: low\n- token leak risk: {secret}"
        return f"position from {name} mentioning {secret}"

    out_dir = tmp_path / "run"
    run_deliberation(
        template=get_template("adr"),
        scenario=f"sensitive: {secret}",
        validators=["claude", "gemini"],
        primary="claude",
        generate=gen,
        output_dir=out_dir,
    )

    trace = (out_dir / "trace.jsonl").read_text()
    scenario_text = (out_dir / "scenario.md").read_text()
    report_text = (out_dir / "final" / "report.md").read_text()
    minority_text = (out_dir / "final" / "minority-report.md").read_text()
    for blob in (trace, scenario_text, report_text, minority_text):
        assert "abcdefghijklmnop" not in blob


def test_trace_jsonl_hides_validator_name_in_anonymous_mode(tmp_path):
    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return "## Context\n## Decision\n## Consequences\n## Alternatives Considered"
        if "VERDICT" in prompt:
            return "VERDICT: PASS\nSEVERITY: low\n- nit"
        return "position"

    out_dir = tmp_path / "anon"
    run_deliberation(
        template=get_template("adr"),
        scenario="x",
        validators=["claude", "gemini"],
        primary="claude",
        generate=gen,
        output_dir=out_dir,
    )
    trace_lines = (out_dir / "trace.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in trace_lines]
    assert all(p["validator"] == "(anonymous)" for p in parsed)
    # labels should still be carried so debugging works.
    labels = {p["label"] for p in parsed}
    assert "Response A" in labels
    assert "Response B" in labels


def test_run_does_not_modify_source_files(tmp_path, monkeypatch):
    sentinel = tmp_path / "auth.py"
    sentinel.write_text("def login(): pass\n")
    original = sentinel.read_text()

    monkeypatch.chdir(tmp_path)

    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return "## Context\n## Decision\n## Consequences\n## Alternatives Considered"
        if "VERDICT" in prompt:
            return "VERDICT: PASS\nSEVERITY: low\n- nit"
        return "position"

    run_deliberation(
        template=get_template("adr"),
        scenario=str(sentinel),
        validators=["claude"],
        primary="claude",
        generate=gen,
        depth="quick",
        output_dir=tmp_path / "council-run",
    )
    assert sentinel.read_text() == original


# ---------------------------------------------------------------------------
# Output dir naming
# ---------------------------------------------------------------------------


def test_default_output_dir_uses_template_name_and_timestamp(tmp_path):
    spec = get_template("rfc")
    out = default_output_dir(spec, root=tmp_path)
    assert out.parent == tmp_path
    assert out.name.endswith("-rfc")
    assert len(out.name.split("-")[0]) == 8  # YYYYMMDD prefix
