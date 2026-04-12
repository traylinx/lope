---
name: lope-execute
description: "Execute a sprint phase-by-phase with validator-in-the-loop retry. For each phase: implement (code, deliverables, research, etc.) → AI validators review → retry on NEEDS_FIX → advance on PASS. Works with any domain (engineering, business, research)."
---

# Lope Execute

For each phase: implement → validators review → retry on NEEDS_FIX → advance on PASS.

This skill is invoked **two ways**:

- **Explicit:** the user typed `/lope-execute <sprint_doc>` and expects you to run that doc
- **Implicit:** the user said something like "run the auth sprint" or "execute the plan we just negotiated" and you mapped it to this skill. In that case, find the sprint doc the user is referring to (most recently created `SPRINT-*.md` in the working directory is usually a safe bet — confirm with the user if ambiguous).

## Two-stage review (v0.3+)

Each phase gets validated **twice** back-to-back:

1. **Spec stage** — "Does the implementation match the phase's Goal and Exit Criteria?" Spec NEEDS_FIX short-circuits (no quality pass until spec is met). Spec FAIL escalates immediately.
2. **Quality stage** — "Is the implementation well-built?" Only runs if spec passed. Catches code smell, edge cases, maintainability issues.

Set `LOPE_SINGLE_STAGE=1` to revert to legacy single-pass validation.

## Steps

1. Determine the sprint doc path (explicit arg OR infer from context).
2. For each phase in order:
   a. Implement the phase goals and meet the exit criteria
   b. Run validation:

```bash
PYTHONPATH=~/.lope python3 -m lope execute "$SPRINT_PATH"
```

   c. If spec NEEDS_FIX: apply the spec-level fixes, re-validate. Quality pass won't run until spec passes.
   d. If quality NEEDS_FIX: apply the quality-level fixes, re-validate.
   e. On both PASS: mark phase complete, move to next
3. After all phases: run `/lope-audit` for the scorecard

## Rules

- Mark checkboxes in the sprint doc as you complete them
- Commit after each phase passes
- Apply ALL required fixes from validator feedback before re-validating
- Do not skip or reorder phases
- If you have doubts, negotiate with your lope teammates, not the user

## Domain adaptation

Lope auto-detects the sprint's domain from the doc header:

- **engineering** — Validators check code quality, bugs, regressions, test coverage
- **business** — Validators check timeline, budget, audience targeting, success metrics
- **research** — Validators check methodology, sampling, validity, ethical considerations

The validator review task adapts automatically. Same `/lope-execute` command for every domain.
