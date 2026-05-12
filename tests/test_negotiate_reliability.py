from __future__ import annotations

from types import SimpleNamespace

from lope.cli import _load_negotiate_context, _write_negotiation_escalation_artifacts
from lope.ensemble import EnsemblePool
from lope.models import EscalationRequired, PhaseVerdict, Proposal, Round, ValidatorResult, VerdictStatus
from lope.negotiator import _build_validator_prompt, _render_refinement_suffix
from lope.validators import StubValidator


def _result(name: str, status: VerdictStatus, rationale: str, fixes=None) -> ValidatorResult:
    return ValidatorResult(
        validator_name=name,
        verdict=PhaseVerdict(
            status=status,
            confidence=0.8,
            rationale=rationale,
            required_fixes=list(fixes or []),
            validator_name=name,
        ),
        raw_response=f"raw {name} {status.value}",
    )


def test_negotiate_validator_prompt_is_prompt_only_no_tools():
    proposal = Proposal(
        round_number=1,
        goal="capacity safety",
        text="# SPRINT-CAPACITY\n\n## Phases\n\n### Phase 1: reserve\n\n**Goal:** reserve first\n\n**Files:**\n- checkout.py\n\n**Tests:**\n- pytest\n",
    )
    prompt = _build_validator_prompt("capacity safety", proposal)

    assert "DO NOT USE TOOLS" in prompt
    assert "Do not read files" in prompt
    assert "Review referenced" not in prompt
    assert "Verify every claim against evidence" not in prompt
    assert "in-prompt" in prompt


def test_refinement_suffix_keeps_drafter_prompt_only():
    verdict = PhaseVerdict(
        status=VerdictStatus.NEEDS_FIX,
        confidence=0.72,
        rationale="needs atomic reservation",
        required_fixes=["reservation must precede checkout"],
    )
    suffix = _render_refinement_suffix(verdict, prior_round=1)

    assert "DO NOT USE TOOLS" in suffix
    assert "only the in-prompt" in suffix
    assert "file:line" not in suffix
    assert "Verify claims against code" not in suffix


def test_ensemble_sequential_runs_every_validator_in_order():
    calls = []

    class RecordingStub(StubValidator):
        def validate(self, prompt, timeout=480):
            calls.append(self.name)
            return super().validate(prompt, timeout)

    a = RecordingStub(name="a", response=_result("a", VerdictStatus.PASS, "ok"))
    b = RecordingStub(
        name="b",
        response=_result("b", VerdictStatus.NEEDS_FIX, "fix", ["tighten concurrency"]),
    )
    pool = EnsemblePool(validators=[a, b], primary="a", parallel=False)

    result = pool.validate("prompt", timeout=1)

    assert calls == ["a", "b"]
    assert result.validator_name == "ensemble"
    assert result.verdict.status == VerdictStatus.NEEDS_FIX
    assert result.verdict.required_fixes == ["tighten concurrency"]


def test_context_file_combines_file_then_inline_context(tmp_path):
    context_file = tmp_path / "context.md"
    context_file.write_text("file context", encoding="utf-8")
    args = SimpleNamespace(context_file=str(context_file), context="inline context")

    context = _load_negotiate_context(args)

    assert f"## Context file: {context_file}" in context
    assert "file context" in context
    assert context.index("file context") < context.index("inline context")


def test_escalation_writes_last_proposal_feedback_and_rounds(tmp_path):
    out = tmp_path / "SPRINT.md"
    verdict = PhaseVerdict(
        status=VerdictStatus.NEEDS_FIX,
        confidence=0.66,
        rationale="missing reservation atomicity",
        required_fixes=["reserve before checkout"],
        validator_name="ensemble",
    )
    negotiator = SimpleNamespace(
        rounds=[
            Round(number=1, proposer="drafter", text="# SPRINT-ONE\nold"),
            Round(number=1, proposer="validator", text="round1 feedback", verdict=verdict),
            Round(number=2, proposer="drafter", text="# SPRINT-ONE\nnew"),
        ]
    )
    escalation = EscalationRequired(
        phase_index=0,
        phase_name="negotiation-round-1",
        reason="1 NEEDS_FIX rounds exhausted without PASS",
        last_verdict=verdict,
    )

    paths = _write_negotiation_escalation_artifacts(str(out), negotiator, escalation)

    assert out.read_text(encoding="utf-8") == "# SPRINT-ONE\nnew\n"
    feedback = tmp_path / "SPRINT.md.feedback.md"
    assert feedback.exists()
    assert "reserve before checkout" in feedback.read_text(encoding="utf-8")
    rounds_dir = tmp_path / "SPRINT.md.rounds"
    assert rounds_dir.is_dir()
    assert len(list(rounds_dir.iterdir())) == 3
    assert paths["last_proposal"] == str(out)
    assert paths["feedback"] == str(feedback)
    assert paths["rounds_dir"] == str(rounds_dir)
