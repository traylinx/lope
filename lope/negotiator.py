"""
Negotiator — multi-round scope negotiation state machine.

Follows a propose-validate-refine loop:

  Round 1: Harvey proposes a scope (an implementation LLM drafts a
           markdown proposal, we write it to scratch, send to validator).
  Round 2: Validator critiques. Harvey reads the critique, verifies any
           factual claims in code, and refines the proposal.
  Round N: Repeat until validator PASSes OR max_rounds is hit.
  Converge: Return a SprintDoc parsed from the final accepted proposal,
            OR raise/return EscalationRequired if max_rounds exhausted.

Contracts:
  - The implementation LLM is injected as a callable so tests can stub it.
    Signature: `llm(system: str, user: str) -> str` — returns raw markdown.
  - The validator is any ValidatorPool (or any object with the same
    `.validate(prompt, timeout) -> ValidatorResult` interface).
  - All scratch files are written under `scratch_dir / <slug>/ roundN.md`.
    Use a TemporaryDirectory in tests.
  - `converge` never raises — on escalation it returns EscalationRequired
    as a value so callers can inspect it without try/except.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Callable, List, Optional, Union

from .models import (
    EscalationRequired,
    PhaseVerdict,
    Proposal,
    Round,
    SprintDoc,
    ValidatorResult,
    VerdictStatus,
)

log = logging.getLogger("lope.negotiator")


# Signature for the implementation LLM that drafts proposals.
LLMCall = Callable[[str, str], str]


def _negotiator_system_prompt(domain: str = "engineering") -> str:
    """Build domain-appropriate system prompt for the drafter LLM."""
    from .models import DOMAINS
    dc = DOMAINS.get(domain, DOMAINS["engineering"])
    domain_line = f'\n  - **Domain:** {domain}' if domain != "engineering" else ""
    return f"""\
You are a {dc['role']} drafting a sprint proposal.
Output is markdown sprint doc only. No preamble. No postscript.

Requirements:
  - # title: "SPRINT-<SLUG>"{domain_line}
  - ## Origin: user request
  - ## Phases: one ### Phase N: <name> per phase
  - Each phase: **Goal:** line, **Criteria:** bullets, **{dc['artifact_label']}:** bullets, **{dc['check_label']}:** bullets
  - 3-5 phases. Over/under-decomposition = bugs.
  - Revision rounds: address REQUIRED_FIXES line by line. Verify each claim
    against evidence before accepting/rejecting.

Output ONLY the sprint doc.
"""


REFINEMENT_SUFFIX_TEMPLATE = """\

--- VALIDATOR FEEDBACK (round {prior_round}) ---
status: {prior_status} (conf={prior_confidence})
rationale: {prior_rationale}
fixes:
{fix_block}

--- REVISE ---
Address each fix. Verify claims against code (cite file:line). Push back if wrong.
Output full revised sprint doc, not a diff.
"""


class Negotiator:
    """Multi-round negotiation: LLM drafts → validator reviews → repeat."""

    def __init__(
        self,
        llm_call: LLMCall,
        validator_pool,
        max_rounds: int = 3,
        scratch_dir: Optional[Union[str, Path]] = None,
        timeout_seconds: Optional[int] = None,
        domain: str = "engineering",
    ):
        # Default to the same source of truth as validators
        # (LOPE_TIMEOUT env var, falling back to 480s). The previous
        # hardcoded 300 silently shadowed ~/.lope/config.json's "timeout"
        # when negotiate constructed a Negotiator without passing it
        # through, capping every reviewer call at 300s on big round-2
        # prompts. See feedback_lope_negotiator_300s_bug for details.
        if timeout_seconds is None:
            from .validators import DEFAULT_TIMEOUT_SECONDS
            timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        if llm_call is None:
            raise ValueError("Negotiator needs an llm_call")
        if validator_pool is None:
            raise ValueError("Negotiator needs a validator_pool")
        if max_rounds < 1:
            raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")
        self._llm = llm_call
        self._pool = validator_pool
        self._max_rounds = max_rounds
        self._scratch_dir = Path(scratch_dir) if scratch_dir else None
        self._timeout = timeout_seconds
        self._domain = domain
        self._system_prompt = _negotiator_system_prompt(domain)
        self._rounds: List[Round] = []

    # ─── Public API ──────────────────────────────────────────

    @property
    def rounds(self) -> List[Round]:
        """Read-only view of every round captured so far."""
        return list(self._rounds)

    def propose(self, goal: str, context: str = "") -> Proposal:
        """Produce the initial (round 1) proposal, lint-gated."""
        user_prompt = _build_user_prompt(goal, context)
        text = self._llm_and_lint(
            self._system_prompt,
            user_prompt,
            round_number=1,
        )
        proposal = Proposal(round_number=1, goal=goal, text=text, prior_feedback="")
        self._rounds.append(
            Round(number=1, proposer="drafter", text=text)
        )
        self._save_round(proposal)
        return proposal

    def refine(self, previous: Proposal, feedback: ValidatorResult) -> Proposal:
        """Produce a refined proposal incorporating validator feedback, lint-gated."""
        if feedback.verdict.status == VerdictStatus.PASS:
            # No refinement needed; return the previous proposal unchanged.
            return previous
        suffix = _render_refinement_suffix(feedback.verdict, previous.round_number)
        next_round = previous.round_number + 1
        user_prompt = _build_user_prompt(previous.goal, previous.text) + suffix
        text = self._llm_and_lint(
            self._system_prompt,
            user_prompt,
            round_number=next_round,
        )
        proposal = Proposal(
            round_number=next_round,
            goal=previous.goal,
            text=text,
            prior_feedback=feedback.verdict.rationale,
        )
        self._rounds.append(Round(number=next_round, proposer="drafter", text=text))
        self._save_round(proposal)
        return proposal

    def _llm_and_lint(
        self, system: str, user: str, round_number: int, max_lint_retries: int = 2
    ) -> str:
        """Call the drafter, run the proposal lint, feed errors back on failure.

        Lint-fix rounds are cheaper than validator rounds: we pay for one
        drafter call per retry, but skip an entire validator ensemble pass.
        Returns the first text that passes the lint (or, if the drafter
        insists on placeholder language, the last draft with a warning).

        Set LOPE_LINT=off to disable.
        """
        if os.environ.get("LOPE_LINT", "").lower() == "off":
            return self._llm(system, user)

        current_user = user
        last_text = ""
        for attempt in range(1, max_lint_retries + 2):  # initial + N retries
            last_text = self._llm(system, current_user)
            errors = _lint_proposal(last_text)
            if not errors:
                return last_text
            log.warning(
                f"[negotiator] round {round_number} lint-fix "
                f"attempt {attempt}/{max_lint_retries + 1} — "
                f"{len(errors)} issue(s): {errors[:3]}"
            )
            current_user = user + "\n\n" + _render_lint_feedback(errors)
        log.warning(
            f"[negotiator] round {round_number} lint did not converge "
            f"after {max_lint_retries + 1} attempts; passing draft to "
            f"validators anyway"
        )
        return last_text

    def converge(
        self, goal: str, context: str = ""
    ) -> Union[SprintDoc, EscalationRequired]:
        """Orchestrate propose → refine loop until PASS or max_rounds.

        Returns a SprintDoc on success, EscalationRequired as a value on
        failure (never raises). Call sites should `isinstance(...)` check.
        """
        proposal = self.propose(goal, context)
        for round_idx in range(1, self._max_rounds + 1):
            feedback = self._pool.validate(
                _build_validator_prompt(goal, proposal, domain=self._domain),
                timeout=self._timeout,
            )
            self._rounds.append(
                Round(
                    number=round_idx,
                    proposer="validator",
                    text=feedback.raw_response or feedback.verdict.rationale,
                    verdict=feedback.verdict,
                )
            )

            status = feedback.verdict.status
            if status == VerdictStatus.PASS:
                log.info(f"[negotiator] converged on round {round_idx}")
                return SprintDoc.from_markdown(proposal.text)
            if status == VerdictStatus.FAIL:
                return EscalationRequired(
                    phase_index=0,
                    phase_name=f"negotiation-round-{round_idx}",
                    reason="validator returned FAIL — architectural pushback",
                    last_verdict=feedback.verdict,
                )
            if status == VerdictStatus.INFRA_ERROR:
                return EscalationRequired(
                    phase_index=0,
                    phase_name=f"negotiation-round-{round_idx}",
                    reason=f"validator infra error: {feedback.error or feedback.verdict.rationale}",
                    last_verdict=feedback.verdict,
                )

            # NEEDS_FIX: refine and continue the loop if we have budget
            if round_idx >= self._max_rounds:
                return EscalationRequired(
                    phase_index=0,
                    phase_name=f"negotiation-round-{round_idx}",
                    reason=f"{self._max_rounds} NEEDS_FIX rounds exhausted without PASS",
                    last_verdict=feedback.verdict,
                )
            proposal = self.refine(proposal, feedback)

        # Defensive: loop exited without resolution
        return EscalationRequired(
            phase_index=0,
            phase_name="negotiation-exit",
            reason="negotiation loop exited unexpectedly",
        )

    # ─── Internals ───────────────────────────────────────────

    def _save_round(self, proposal: Proposal) -> Optional[Path]:
        if self._scratch_dir is None:
            return None
        slug = _slug_from_goal(proposal.goal)
        path = self._scratch_dir / slug / f"round{proposal.round_number}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(proposal.text)
        return path


# ─── Pure-function helpers (unit-testable) ───────────────────


def _build_user_prompt(goal: str, context: str) -> str:
    parts = [f"## Goal\n\n{goal}\n"]
    if context:
        parts.append(f"\n## Context\n\n{context}\n")
    parts.append(
        "\n## Task\n\nDraft a sprint doc for this goal. Follow the format "
        "in the system prompt. Think hard about scope — prefer fewer phases "
        "and smaller changes when the payoff is the same."
    )
    return "\n".join(parts)


def _build_validator_prompt(goal: str, proposal: Proposal, domain: str = "engineering") -> str:
    """Prompt the validator to critique the current proposal."""
    from .caveman import get_directive as _caveman
    from .models import DOMAINS
    caveman = _caveman()
    dc = DOMAINS.get(domain, DOMAINS["engineering"])
    return f"""\
{caveman}

Review sprint proposal. Verify claims BEFORE accepting.
Push back on: scope creep, missing edge cases, unverified assumptions, over-complexity.
No rubber-stamping.

## Goal

{goal}

## Proposal (round {proposal.round_number})

{proposal.text}

## Your job

1. Review referenced {dc['artifact_label'].lower()}
2. Verify every claim against evidence
3. Question unnecessary scope
4. Respond with VERDICT block:

---VERDICT---
status: PASS | NEEDS_FIX | FAIL
confidence: 0.0-1.0
rationale: 1-3 sentences, terse, no filler
required_fixes:
  - fix 1
  - fix 2
---END---

PASS=ready. NEEDS_FIX=list specific fixes. FAIL=escalate. Conf<0.7 on PASS→NEEDS_FIX.
"""


def _render_refinement_suffix(verdict: PhaseVerdict, prior_round: int) -> str:
    fix_lines = (
        "\n".join(f"  - {f}" for f in verdict.required_fixes)
        or "  (none listed; improve rigor generally)"
    )
    return REFINEMENT_SUFFIX_TEMPLATE.format(
        prior_round=prior_round,
        prior_status=verdict.status.value,
        prior_confidence=f"{verdict.confidence:.2f}",
        prior_rationale=verdict.rationale or "(no rationale provided)",
        fix_block=fix_lines,
    )


def _slug_from_goal(goal: str) -> str:
    """Turn an arbitrary goal string into a filename-safe slug."""
    s = goal.strip().lower()[:60]
    s = re.sub(r"[^a-z0-9\-]+", "-", s).strip("-")
    return s or "sprint"


# ─── Proposal lint ──────────────────────────────────────────────

# Placeholder tokens that indicate an incomplete draft. Checked case-insensitively
# and only when they appear outside fenced code blocks.
_PLACEHOLDER_TOKENS = [
    r"\bTBD\b",
    r"\bTODO\b",
    r"\bXXX\b",
    r"\bFIXME\b",
    r"<placeholder>",
    r"<your code here>",
    r"<your text here>",
    r"\[insert[^\]]*\]",
]
_PLACEHOLDER_RE = re.compile("|".join(_PLACEHOLDER_TOKENS), re.IGNORECASE)

# Prose ellipsis — three dots not inside a code fence, not preceded by a quote/bracket.
# We match standalone " ... " or "...\n" in text lines.
_ELLIPSIS_RE = re.compile(r"(?<![.\w])\.{3}(?![.\w])")


def _strip_code_fences(text: str) -> str:
    """Remove fenced code blocks so the lint doesn't flag placeholder-looking code."""
    return re.sub(r"```[\s\S]*?```", "", text)


def _lint_proposal(text: str) -> List[str]:
    """Return a list of lint errors for a proposal draft.

    Empty list = clean. Each error is a short human-readable string the
    drafter can act on directly when fed back in.
    """
    errors: List[str] = []
    if not text or not text.strip():
        errors.append("proposal is empty")
        return errors

    # Strip fenced code so we don't false-positive on legitimate `...` inside code.
    prose = _strip_code_fences(text)

    # Check 1: placeholder tokens anywhere in prose.
    for match in _PLACEHOLDER_RE.finditer(prose):
        errors.append(
            f"placeholder token {match.group(0)!r} — replace with concrete content"
        )
        if len(errors) >= 5:
            break

    # Check 2: prose ellipsis (three dots outside code).
    ellipsis_count = len(_ELLIPSIS_RE.findall(prose))
    if ellipsis_count:
        errors.append(
            f"{ellipsis_count} prose ellipsis token(s) '...' — write out the steps, "
            f"do not trail off"
        )

    # Check 3: phase structure — each `### Phase N:` block must have
    # non-empty artifacts/files and checks/tests lists.
    phase_blocks = _split_phase_blocks(text)
    if not phase_blocks:
        errors.append(
            "no `### Phase N:` blocks found — sprint doc must have 3-5 numbered phases"
        )
    else:
        for idx, (phase_header, phase_body) in enumerate(phase_blocks, start=1):
            phase_errors = _lint_phase(phase_header, phase_body, idx)
            errors.extend(phase_errors)
            if len(errors) >= 10:
                errors.append(f"(truncated at 10 errors; more phases unchecked)")
                break

    return errors


def _split_phase_blocks(text: str) -> List[tuple]:
    """Return list of (header, body) tuples for each `### Phase N:` block."""
    # Split on headings that begin with `### Phase`
    pattern = re.compile(r"^###\s+Phase\s+\d+.*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return []
    blocks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        header = m.group(0)
        body = text[start:end]
        blocks.append((header, body))
    return blocks


def _lint_phase(header: str, body: str, index: int) -> List[str]:
    """Return lint errors for a single phase block."""
    errs: List[str] = []

    # Goal must exist and have more than one word.
    goal_match = re.search(
        r"^\s*\*\*Goal\*?\*?\s*:?\*?\*?\s*(.+)$", body, re.MULTILINE
    )
    if not goal_match:
        errs.append(f"phase {index}: missing **Goal:** line")
    else:
        goal_text = goal_match.group(1).strip().rstrip("*").strip()
        if len(goal_text.split()) < 2:
            errs.append(f"phase {index}: goal too short: {goal_text!r}")

    # Check for a label that introduces a list (artifacts, files, deliverables,
    # etc.) followed by at least one bullet. Labels vary by domain.
    list_labels_required = ["artifacts", "files", "deliverables"]
    checks_labels_required = ["checks", "tests", "success metrics", "validation criteria"]

    if not _has_nonempty_list(body, list_labels_required):
        errs.append(
            f"phase {index}: needs a non-empty **Artifacts/Files/Deliverables:** "
            f"bullet list"
        )
    if not _has_nonempty_list(body, checks_labels_required):
        errs.append(
            f"phase {index}: needs a non-empty **Checks/Tests/Success Metrics:** "
            f"bullet list"
        )

    return errs


def _has_nonempty_list(body: str, candidate_labels: List[str]) -> bool:
    """Check if the body has a **Label:** line followed by one or more `- ` bullets.

    We accept any of the candidate labels to be flexible across domains.
    A label counts as "has a list" only if the next non-blank line is an
    actual bullet (`- `, `• `, or `* ` with a following space — NOT `**`,
    which is markdown bold for the next label).
    """
    label_pattern = (
        r"\*\*(?:"
        + "|".join(re.escape(l) for l in candidate_labels)
        + r")\*?\*?\s*:?\s*\*?\*?[ \t]*\n"  # only one newline, no \s* trail
    )
    for m in re.finditer(label_pattern, body, re.IGNORECASE):
        tail = body[m.end():]
        # Find the first non-blank line.
        first_nonblank = ""
        for line in tail.split("\n"):
            stripped = line.strip()
            if stripped:
                first_nonblank = stripped
                break
        if not first_nonblank:
            continue
        # A valid bullet starts with `- `, `• `, or `* ` (literal star + space).
        # We must reject `**` (bold) which is the next label, not a list item.
        if first_nonblank.startswith(("- ", "• ")):
            return True
        if first_nonblank.startswith("* ") and not first_nonblank.startswith("**"):
            return True
    return False


def _render_lint_feedback(errors: List[str]) -> str:
    """Format lint errors so the drafter can read and fix them on the next turn."""
    bullets = "\n".join(f"  - {e}" for e in errors)
    return (
        "--- DRAFT LINT FAILED — FIX BEFORE VALIDATOR REVIEW ---\n"
        "Your previous draft failed the pre-validation lint with these issues:\n"
        f"{bullets}\n\n"
        "Rewrite the draft. Replace every placeholder token with concrete text. "
        "Every phase must have (1) a **Goal:** line with at least a few words, "
        "(2) a non-empty **Artifacts/Files/Deliverables:** bullet list, and "
        "(3) a non-empty **Checks/Tests/Success Metrics:** bullet list. "
        "Output the full revised sprint doc, not a diff."
    )
