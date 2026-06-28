from __future__ import annotations

from lope.executor import _build_validation_prompt, ImplementationResult
from lope.implement import ImplementRoster, build_swarm_prompt
from lope.minimality import (
    OVER_ENGINEERING_FOCUS,
    implementation_directive,
    mode,
    resolve_review_focus,
    validator_rubric,
)
from lope.models import Phase, SprintDoc


def _doc(domain: str = "engineering"):
    return SprintDoc(
        slug="x",
        title="SPRINT-X",
        domain=domain,
        phases=[Phase(index=1, name="one", goal="do one", criteria=["one works"])],
    )


def _roster():
    return ImplementRoster(
        agents=["pi"],
        escalation_agents=["claude"],
        validators=["claude", "pi"],
        primary="pi",
    )


def test_mode_defaults_audit_and_invalid_fails_closed(monkeypatch):
    monkeypatch.delenv("LOPE_MINIMALITY", raising=False)
    assert mode() == "audit"
    assert mode("audit") == "audit"
    assert mode("enforce") == "enforce"
    assert mode("garbage") == "audit"


def test_directives_are_empty_when_off():
    assert implementation_directive("off") == ""
    assert validator_rubric("off", stage="quality") == ""


def test_engineering_directives_are_audit_by_default(monkeypatch):
    monkeypatch.delenv("LOPE_MINIMALITY", raising=False)
    assert "Minimality discipline (audit" in implementation_directive(domain="engineering")
    assert "Minimality review (audit)" in validator_rubric(stage="quality", domain="engineering")


def test_non_engineering_defaults_off_unless_explicit(monkeypatch):
    monkeypatch.delenv("LOPE_MINIMALITY", raising=False)
    assert implementation_directive(domain="business") == ""
    assert validator_rubric(stage="quality", domain="research") == ""
    assert "Minimality review (audit)" in validator_rubric("audit", stage="quality", domain="business")


def test_env_off_disables_engineering_default(monkeypatch):
    monkeypatch.setenv("LOPE_MINIMALITY", "off")
    assert implementation_directive(domain="engineering") == ""
    assert validator_rubric(stage="quality", domain="engineering") == ""


def test_validator_rubric_only_targets_quality_or_legacy_stage():
    assert validator_rubric("audit", stage="spec") == ""
    assert "Minimality review (audit)" in validator_rubric("audit", stage="quality")
    assert "Minimality review (audit)" in validator_rubric("audit", stage=None)


def test_review_focus_alias_expands():
    assert resolve_review_focus("security") == "security"
    assert resolve_review_focus(" over-engineering ") == OVER_ENGINEERING_FOCUS
    assert "Lean already. Ship." in resolve_review_focus("lazy-build")


def test_swarm_prompt_includes_minimality_directive_when_enabled(monkeypatch):
    monkeypatch.delenv("LOPE_MINIMALITY", raising=False)
    doc = _doc()
    prompt = build_swarm_prompt(doc.phases[0], doc, _roster())
    assert "## Minimality discipline" in prompt
    assert "Prefer no build, existing code, stdlib" in prompt


def test_business_swarm_prompt_does_not_include_minimality_by_default(monkeypatch):
    monkeypatch.delenv("LOPE_MINIMALITY", raising=False)
    doc = _doc(domain="business")
    prompt = build_swarm_prompt(doc.phases[0], doc, _roster())
    assert "## Minimality discipline" not in prompt


def test_executor_quality_prompt_includes_minimality_but_spec_prompt_does_not(monkeypatch):
    monkeypatch.delenv("LOPE_MINIMALITY", raising=False)
    phase = _doc().phases[0]
    impl = ImplementationResult(ok=True, summary="changed foo.py", files_changed=["foo.py"])

    spec_prompt = _build_validation_prompt(phase, impl, stage="spec")
    quality_prompt = _build_validation_prompt(phase, impl, stage="quality")

    assert "Minimality review" not in spec_prompt
    assert "Minimality review (audit)" in quality_prompt


def test_business_quality_prompt_does_not_include_minimality_by_default(monkeypatch):
    monkeypatch.delenv("LOPE_MINIMALITY", raising=False)
    phase = _doc().phases[0]
    impl = ImplementationResult(ok=True, summary="changed foo.py", files_changed=["foo.py"])

    quality_prompt = _build_validation_prompt(phase, impl, domain="business", stage="quality")

    assert "Minimality review" not in quality_prompt


def test_enforce_mode_keeps_blocking_threshold(monkeypatch):
    monkeypatch.setenv("LOPE_MINIMALITY", "enforce")
    phase = _doc().phases[0]
    impl = ImplementationResult(ok=True, summary="changed foo.py", files_changed=["foo.py"])

    quality_prompt = _build_validation_prompt(phase, impl, stage="quality")

    assert "Minimality review (enforce)" in quality_prompt
    assert "return NEEDS_FIX only for material bloat" in quality_prompt
