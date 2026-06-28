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


def _doc():
    return SprintDoc(
        slug="x",
        title="SPRINT-X",
        phases=[Phase(index=1, name="one", goal="do one", criteria=["one works"])],
    )


def _roster():
    return ImplementRoster(
        agents=["pi"],
        escalation_agents=["claude"],
        validators=["claude", "pi"],
        primary="pi",
    )


def test_mode_defaults_off_and_invalid_fails_closed(monkeypatch):
    monkeypatch.delenv("LOPE_MINIMALITY", raising=False)
    assert mode() == "off"
    assert mode("audit") == "audit"
    assert mode("enforce") == "enforce"
    assert mode("garbage") == "off"


def test_directives_are_empty_when_off():
    assert implementation_directive("off") == ""
    assert validator_rubric("off", stage="quality") == ""


def test_validator_rubric_only_targets_quality_or_legacy_stage():
    assert validator_rubric("audit", stage="spec") == ""
    assert "Minimality review (audit)" in validator_rubric("audit", stage="quality")
    assert "Minimality review (audit)" in validator_rubric("audit", stage=None)


def test_review_focus_alias_expands():
    assert resolve_review_focus("security") == "security"
    assert resolve_review_focus(" over-engineering ") == OVER_ENGINEERING_FOCUS
    assert "Lean already. Ship." in resolve_review_focus("lazy-build")


def test_swarm_prompt_includes_minimality_directive_when_enabled(monkeypatch):
    monkeypatch.setenv("LOPE_MINIMALITY", "audit")
    prompt = build_swarm_prompt(_doc().phases[0], _doc(), _roster())
    assert "## Minimality discipline" in prompt
    assert "Prefer no build, existing code, stdlib" in prompt


def test_executor_quality_prompt_includes_minimality_but_spec_prompt_does_not(monkeypatch):
    monkeypatch.setenv("LOPE_MINIMALITY", "enforce")
    phase = _doc().phases[0]
    impl = ImplementationResult(ok=True, summary="changed foo.py", files_changed=["foo.py"])

    spec_prompt = _build_validation_prompt(phase, impl, stage="spec")
    quality_prompt = _build_validation_prompt(phase, impl, stage="quality")

    assert "Minimality review" not in spec_prompt
    assert "Minimality review (enforce)" in quality_prompt
    assert "return NEEDS_FIX only for material bloat" in quality_prompt
