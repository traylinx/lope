"""
PhaseExecutor — phase-by-phase autonomous execution loop with validator-in-the-loop.

Walks a SprintDoc in phase-index order. Per phase:

  1. Call `implementation_fn(phase, fix_context)` — the caller's hook that
     actually writes code and runs tests. First call has fix_context=None.
     Retry calls pass the validator's `required_fixes` from the prior
     attempt so the implementation LLM can address them precisely.
  2. Build a validation prompt from the phase's goal/criteria/files/tests
     plus the implementation_fn's return value.
  3. Ask the ValidatorPool to validate.
  4. If PASS → phase.verdict set, advance to next phase.
  5. If NEEDS_FIX → re-enter step 1 with fix_context. Up to
     max_rounds_per_phase attempts before escalating.
  6. If FAIL or INFRA_ERROR after pool fallback → escalate immediately.

The executor never raises — EscalationRequired is returned as an
ExecutionReport.error value so the caller can inspect and recover.

Optionally accepts callback hooks (on_start, on_phase, on_end) for
integrating with external task tracking or logging systems.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from .models import (
    EscalationRequired,
    ExecutionReport,
    Phase,
    PhaseVerdict,
    SprintDoc,
    VerdictStatus,
)

log = logging.getLogger("lope.executor")


# Implementation callback contract — returns an ImplementationResult
# summarizing what the hook did for this round (files changed,
# tests run, any error messages). The executor forwards this to
# the validator prompt so opencode can reason about it.

ImplementationFn = Callable[..., "ImplementationResult"]


@dataclass
class ImplementationResult:
    """What `implementation_fn` returns per phase attempt."""
    ok: bool = True
    summary: str = ""
    files_changed: Optional[list] = None
    test_results: Optional[dict] = None
    error: str = ""

    def __post_init__(self):
        if self.files_changed is None:
            self.files_changed = []
        if self.test_results is None:
            self.test_results = {}


class PhaseExecutor:
    """Runs a SprintDoc phase-by-phase with validator-in-the-loop retry."""

    def __init__(
        self,
        validator_pool,
        implementation_fn: ImplementationFn,
        max_rounds_per_phase: int = 3,
        timeout_seconds: int = 480,
        on_start=None,
        on_phase=None,
        on_end=None,
        gate_runner=None,
    ):
        if validator_pool is None:
            raise ValueError("PhaseExecutor needs a validator_pool")
        if implementation_fn is None:
            raise ValueError("PhaseExecutor needs an implementation_fn")
        if max_rounds_per_phase < 1:
            raise ValueError(
                f"max_rounds_per_phase must be >= 1, got {max_rounds_per_phase}"
            )
        self._pool = validator_pool
        self._impl = implementation_fn
        self._max_rounds = max_rounds_per_phase
        self._timeout = timeout_seconds
        self._on_start = on_start
        self._on_phase = on_phase
        self._on_end = on_end
        self._gate_runner = gate_runner

    def run(self, sprint_doc: SprintDoc) -> ExecutionReport:
        """Walk every phase. Returns ExecutionReport (never raises)."""
        self._domain = getattr(sprint_doc, "domain", "engineering")
        report = ExecutionReport(sprint_doc=sprint_doc)
        started = time.time()
        task_id = self._checkpoint_start(sprint_doc)

        ordered = sorted(sprint_doc.phases, key=lambda p: p.index)
        for phase in ordered:
            escalation = self._run_phase(phase, task_id)
            if phase.verdict is not None:
                report.phase_verdicts.append(phase.verdict)
            if escalation is not None:
                report.ok = False
                report.error = str(escalation)
                log.warning(f"[executor] escalation: {escalation}")
                break

        report.total_duration_seconds = time.time() - started
        if report.ok:
            log.info(
                f"[executor] sprint complete: {len(report.phase_verdicts)} phase(s) "
                f"in {report.total_duration_seconds:.1f}s, "
                f"avg confidence={report.confidence_average():.2f}"
            )
        self._checkpoint_end(task_id, report)
        return report

    # ─── Per-phase state machine ─────────────────────────────

    def _run_phase(self, phase: Phase, task_id: Optional[str]) -> Optional[EscalationRequired]:
        """Implement + validate one phase with retry. Returns EscalationRequired
        on failure, None on success. Sets phase.verdict as a side effect.

        Two-stage review (v0.3): each attempt runs validators twice —
        first for spec compliance, then (only if spec passes) for code
        quality. Spec NEEDS_FIX short-circuits to avoid wasting a quality
        pass on unmet spec. Set LOPE_SINGLE_STAGE=1 to revert to the
        legacy single-pass flow.
        """
        import os as _os
        single_stage = _os.environ.get("LOPE_SINGLE_STAGE", "").strip() in ("1", "true", "yes", "on")

        fix_context: Optional[list] = None
        for attempt in range(1, self._max_rounds + 1):
            log.info(
                f"[executor] phase {phase.index} ({phase.name}) attempt {attempt}/{self._max_rounds}"
            )
            self._checkpoint_phase_attempt(task_id, phase, attempt)

            impl_result = self._impl(phase=phase, fix_context=fix_context)

            if not impl_result.ok:
                phase.verdict = PhaseVerdict(
                    status=VerdictStatus.INFRA_ERROR,
                    rationale=f"implementation_fn failed: {impl_result.error or 'unknown'}",
                    validator_name="implementation",
                )
                return EscalationRequired(
                    phase_index=phase.index,
                    phase_name=phase.name,
                    reason=f"implementation failed on attempt {attempt}: {impl_result.error}",
                    last_verdict=phase.verdict,
                )

            # ── Stage 1: spec compliance ─────────────────────────
            stage1_prompt = _build_validation_prompt(
                phase, impl_result, domain=self._domain,
                stage=None if single_stage else "spec",
            )
            stage1_result = self._pool.validate(stage1_prompt, timeout=self._timeout)
            # Copy the verdict so stage tagging doesn't mutate a shared reference
            # (matters for stub validators in tests that return sticky objects).
            stage1_verdict = replace(
                stage1_result.verdict,
                stage=None if single_stage else "spec",
            )
            phase.verdict = stage1_verdict

            # Terminal spec failures escalate without running quality pass
            if stage1_verdict.status == VerdictStatus.FAIL:
                return EscalationRequired(
                    phase_index=phase.index,
                    phase_name=phase.name,
                    reason="spec stage returned FAIL",
                    last_verdict=stage1_verdict,
                )
            if stage1_verdict.status == VerdictStatus.INFRA_ERROR:
                return EscalationRequired(
                    phase_index=phase.index,
                    phase_name=phase.name,
                    reason=f"validator infra error: {stage1_result.error or stage1_verdict.rationale}",
                    last_verdict=stage1_verdict,
                )
            if stage1_verdict.status == VerdictStatus.NEEDS_FIX:
                if attempt >= self._max_rounds:
                    return EscalationRequired(
                        phase_index=phase.index,
                        phase_name=phase.name,
                        reason=f"{self._max_rounds} NEEDS_FIX rounds exhausted on spec stage",
                        last_verdict=stage1_verdict,
                    )
                fix_context = list(stage1_verdict.required_fixes)
                log.info(
                    f"[executor] phase {phase.index} spec NEEDS_FIX — retrying with "
                    f"{len(fix_context)} fix(es)"
                )
                continue  # retry from the top of the attempt loop

            # stage1 == PASS
            log.info(
                f"[executor] phase {phase.index} spec PASS conf={stage1_verdict.confidence:.2f}"
            )

            # Legacy single-stage path: we're done after spec PASS
            if single_stage:
                return None

            # ── Optional objective gate check ─────────────────────
            gate_run = None
            if self._gate_runner is not None:
                gate_run = self._gate_runner(phase=phase, attempt=attempt)

            # ── Stage 2: code quality ────────────────────────────
            stage2_prompt = _build_validation_prompt(
                phase, impl_result, domain=self._domain, stage="quality",
                gate_report=gate_run,
            )
            stage2_result = self._pool.validate(stage2_prompt, timeout=self._timeout)
            stage2_verdict = replace(stage2_result.verdict, stage="quality")
            # Keep both stage verdicts visible on the phase so the auditor
            # can render the full two-column scorecard.
            phase._stage_verdicts = [stage1_verdict, stage2_verdict]
            phase.verdict = stage2_verdict

            if stage2_verdict.status == VerdictStatus.PASS:
                gate_failures = _gate_failures(gate_run)
                if gate_failures:
                    if attempt >= self._max_rounds:
                        downgraded = replace(
                            stage2_verdict,
                            status=VerdictStatus.NEEDS_FIX,
                            required_fixes=gate_failures,
                            rationale=(stage2_verdict.rationale + " Objective gates failed: " + "; ".join(gate_failures[:3])).strip(),
                        )
                        phase.verdict = downgraded
                        return EscalationRequired(
                            phase_index=phase.index,
                            phase_name=phase.name,
                            reason=f"{self._max_rounds} NEEDS_FIX rounds exhausted on objective gates",
                            last_verdict=downgraded,
                        )
                    fix_context = list(gate_failures)
                    log.info(
                        f"[executor] phase {phase.index} gates NEEDS_FIX — retrying with "
                        f"{len(fix_context)} gate fix(es)"
                    )
                    continue
                log.info(
                    f"[executor] phase {phase.index} quality PASS "
                    f"conf={stage2_verdict.confidence:.2f}"
                )
                return None
            if stage2_verdict.status == VerdictStatus.FAIL:
                return EscalationRequired(
                    phase_index=phase.index,
                    phase_name=phase.name,
                    reason="quality stage returned FAIL",
                    last_verdict=stage2_verdict,
                )
            if stage2_verdict.status == VerdictStatus.INFRA_ERROR:
                return EscalationRequired(
                    phase_index=phase.index,
                    phase_name=phase.name,
                    reason=f"validator infra error on quality: {stage2_result.error or stage2_verdict.rationale}",
                    last_verdict=stage2_verdict,
                )
            # quality == NEEDS_FIX
            if attempt >= self._max_rounds:
                return EscalationRequired(
                    phase_index=phase.index,
                    phase_name=phase.name,
                    reason=f"{self._max_rounds} NEEDS_FIX rounds exhausted on quality stage",
                    last_verdict=stage2_verdict,
                )
            fix_context = list(stage2_verdict.required_fixes)
            log.info(
                f"[executor] phase {phase.index} quality NEEDS_FIX — retrying with "
                f"{len(fix_context)} fix(es)"
            )

        return EscalationRequired(
            phase_index=phase.index,
            phase_name=phase.name,
            reason="phase loop exited unexpectedly",
        )

    # ─── Checkpoint hooks (callback-based) ────────────────────

    def _checkpoint_start(self, sprint_doc: SprintDoc) -> Optional[str]:
        if self._on_start:
            return self._on_start(sprint_doc)
        return None

    def _checkpoint_phase_attempt(self, task_id, phase, attempt):
        if self._on_phase:
            self._on_phase(task_id, phase, attempt)

    def _checkpoint_end(self, task_id, report):
        if self._on_end:
            self._on_end(task_id, report)


# ─── Prompt builder (pure function, unit-testable) ──────────────


def _build_validation_prompt(
    phase: Phase,
    impl: ImplementationResult,
    domain: str = "engineering",
    stage: Optional[str] = None,
    gate_report=None,
) -> str:
    """Render a domain-aware validator prompt for a phase + its implementation result.

    `stage` controls which review framing is used:
      - None: legacy single-pass "review everything" prompt
      - "spec": ONLY check if the implementation matches Goal + Criteria
      - "quality": ONLY check code/deliverable quality, craftsmanship, edge cases
    """
    from .models import DOMAINS
    dc = DOMAINS.get(domain, DOMAINS["engineering"])

    criteria_block = (
        "\n".join(f"  - {c}" for c in phase.criteria) or "  (none)"
    )

    artifacts_used = list(phase.artifacts)
    for changed in (impl.files_changed or []):
        if changed not in artifacts_used:
            artifacts_used.append(changed)
    artifacts_block = "\n".join(f"  - {f}" for f in artifacts_used) or "  (none)"

    check_lines = []
    for name, status in (impl.test_results or {}).items():
        check_lines.append(f"  - {name}: {status}")
    for t in phase.checks:
        check_lines.append(f"  - {t}")
    check_block = "\n".join(check_lines) or "  (none)"

    summary = impl.summary or "(implementation returned no summary)"
    gate_block = ""
    if gate_report is not None:
        try:
            from .gates import prompt_summary as _gate_prompt_summary
            gate_block = "\n" + _gate_prompt_summary(gate_report) + "\n"
        except Exception:
            gate_block = "\n## Objective gate report\n- Gate report unavailable.\n"

    if stage == "spec":
        review_task = (
            "Check ONLY spec compliance — does the implementation do "
            "what the Goal and Exit Criteria say? Do not critique code "
            "style, naming, or craftsmanship. Ignore quality concerns. "
            "If the implementation meets every criterion, return PASS. "
            "If any criterion is unmet, return NEEDS_FIX with the missing "
            "ones listed in required_fixes. If the approach fundamentally "
            "cannot meet the Goal, return FAIL."
        )
        stage_label = "SPEC STAGE"
    elif stage == "quality":
        review_task = (
            "Spec compliance has already been confirmed in a prior stage. "
            "Check ONLY code quality — craftsmanship, edge cases, error "
            "handling, maintainability, obvious anti-patterns. Do not "
            "re-check whether the implementation matches the Goal; that "
            "is settled. Return PASS if the implementation is well-built. "
            "Return NEEDS_FIX with specific quality issues. Return FAIL "
            "only if the code is unsalvageable."
        )
        stage_label = "QUALITY STAGE"
    else:
        review_task = dc["review_task"]
        stage_label = None

    from .caveman import get_directive as _caveman
    caveman = _caveman()
    stage_header = f"\n### {stage_label}\n" if stage_label else ""

    evidence_instruction = (
        "\nInclude at least one file:line reference or a test output "
        "excerpt in your rationale — no evidence means no PASS."
    )

    return f"""\
{caveman}
{stage_header}
Review phase {phase.index} of sprint. Be critical. Verify claims. No rubber-stamp.

## Phase {phase.index}: {phase.name}

**Goal:** {phase.goal}

## Exit Criteria
{criteria_block}

## {dc['artifact_label']}
{artifacts_block}

## Implementation
{summary}

## {dc['check_label']}
{check_block}
{gate_block}
## Task

{review_task}{evidence_instruction}

VERDICT block:

---VERDICT---
status: PASS | NEEDS_FIX | FAIL
confidence: 0.0-1.0
rationale: 1-3 sentences, terse, no filler
required_fixes:
  - fix 1
  - fix 2
---END---

PASS=advance. NEEDS_FIX=list fixes. FAIL=escalate. Conf<0.7 on PASS→NEEDS_FIX.
"""


def _gate_failures(gate_run) -> list:
    if gate_run is None:
        return []
    try:
        failures = gate_run.blocking_failures()
    except Exception:
        return ["Objective gate check failed but could not be summarized"]
    return ["Objective gate failed: " + f for f in failures]
