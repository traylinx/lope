---
name: lope-execute
description: Run a sprint doc phase by phase with validator-in-the-loop retry. Each phase gets two-stage validation (spec compliance then code quality). NEEDS_FIX retries with fix instructions up to 3 times.
agent: build
---

# Lope execute

Run a sprint doc produced by `lope negotiate` phase by phase. Each phase: implement, then validators review, then either PASS (advance), NEEDS_FIX (retry with fix instructions), or FAIL (escalate).

## What to do

1. Confirm the user has a sprint doc file (usually `SPRINT-<slug>.md` in the cwd).
2. Run in a shell:

```bash
lope execute <sprint_doc_path>
```

Optional: `--phase N` to run only one phase.

3. Monitor stdout for per-phase verdicts. PASS advances automatically. NEEDS_FIX retries up to 3 times. FAIL or 3x NEEDS_FIX escalates — read the escalation message and report to the user.

## Two-stage review

Each phase gets validated twice per retry attempt:

1. **Spec compliance pass** — "does this match the goal?" NEEDS_FIX short-circuits the quality pass.
2. **Code quality pass** — "is this well-built?" Only runs if spec PASS.

Set `LOPE_SINGLE_STAGE=1` to disable.

## Evidence gate

Any validator returning PASS with a rationale lacking evidence (no `file:line`, no test output, no explicit verification phrase) gets auto-downgraded to NEEDS_FIX. Set `LOPE_EVIDENCE_GATE=off` to disable.

## Do not

- Do not run execute without a sprint doc. If the user hasn't negotiated one, run `/lope-negotiate` first.
- Do not patch lope source if execution crashes. Capture the traceback and report it.
- Run `lope docs` for the full reference.
