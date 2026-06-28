# Lope minimality discipline

Status: implemented as an opt-in prompt/rubric slice, experimental only.

## Decision

Adopt Ponytail's useful idea as a Lope-native **minimality discipline**, not as a package dependency or runtime hook.

Lope already has `lope/caveman.py` for token-efficient validator communication. That stays separate. Caveman changes how agents speak. Minimality changes what implementers build and what validators flag.

## Goals

- Reduce over-engineered diffs without reducing correctness.
- Catch unnecessary dependencies, duplicate helpers, one-implementation abstractions, and broad rewrites.
- Push bugfixes toward shared root causes rather than symptom patches.
- Preserve Lope's existing evidence gates, validator conservatism, and zero-dependency Python contract.

## Non-goals

- Do not install `@dietrichgebert/ponytail`.
- Do not import Ponytail hooks, MCP server, Pi/OpenCode/Gemini/Hermes adapters, statusline code, or mode-state files.
- Do not use Ponytail benchmark claims as Lope marketing proof.
- Do not merge minimality with caveman output compression.
- Do not make minimality default-on until the acceptance gates pass on real Lope work.

## Mode model

Proposed environment switch:

```bash
LOPE_MINIMALITY=off      # no prompt/rubric changes
LOPE_MINIMALITY=audit    # validators report minimality findings; implementers get soft guidance
LOPE_MINIMALITY=enforce  # implementers must apply ladder; validators can NEEDS_FIX real bloat
```

Current default: `off`. Use `audit` or `enforce` only in Makakoo-driven experiments until the acceptance gates pass.

## Implementer prompt addition

Add a compact block to `lope/implement.py::build_swarm_prompt` when mode is `audit` or `enforce`:

```text
Minimality discipline:
- Understand touched flow before writing.
- Prefer no build, existing code, stdlib, native platform, installed deps, then minimum custom code.
- Reuse helpers before creating new ones.
- Bugfix root cause once; search sibling callers.
- Do not cut security, validation, accessibility, data-loss handling, or explicit requirements.
- Leave one runnable check for non-trivial logic.
```

In `audit`, this is guidance. In `enforce`, it is part of the completion contract.

## Validator rubric addition

Add an optional minimality subsection to `lope/executor.py::_build_validation_prompt`, quality stage only:

```text
Minimality review:
- Flag unnecessary new dependencies.
- Flag duplicate helpers/patterns already present.
- Flag interfaces/factories/providers/config with one real implementation.
- Flag broad rewrites where a local/shared fix satisfies the phase.
- Flag symptom patches that miss sibling callers.
- Never ask to remove security, validation, accessibility, data-loss handling, or explicit requirements.
```

Suggested finding tags:

- `delete`
- `stdlib`
- `native`
- `yagni`
- `reuse`
- `shrink`
- `wrong-layer`

`audit`: include findings in rationale/required_fixes only when material, but do not fail spec-compliant work for style alone.

`enforce`: `NEEDS_FIX` only for material bloat with concrete replacement and no boundary regression.

## Single-shot review integration

Add a convenience focus for `lope review`:

```bash
lope review <file-or-diff> --focus over-engineering
```

Focus text:

```text
Review for over-engineering only. One line per finding:
<path>:L<line>: <tag>: <what to cut>. <replacement>.
Tags: delete, stdlib, native, yagni, reuse, shrink, wrong-layer.
Do not report correctness, security, performance, or style issues unless the issue is unnecessary complexity.
Never recommend removing validation, security, accessibility, data-loss handling, or explicit requirements.
If nothing real: Lean already. Ship.
```

This can ship before implement/executor integration because it is pure prompt surface.

## Acceptance gates before default-on

Run on at least 30 real Lope/Makakoo tasks before promotion:

1. No drop in phase PASS rate.
2. Zero dropped explicit requirements.
3. Zero regressions in security, validation, accessibility, or data-loss handling.
4. Median token/cost improves after retries, not just first pass.
5. Retry rounds increase by no more than 5%.
6. Human review confirms lower abstraction/dependency count on tasks where baseline overbuilt.
7. Under-build canaries pass:
   - auth/security change that truly needs guard code,
   - input validation boundary,
   - accessibility UI task,
   - bug with sibling callers,
   - task that genuinely needs abstraction,
   - task where a new dependency is justified,
   - task where one-liner is less correct than longer code.
8. `over-engineering` focus has no false-positive findings telling agents to remove legitimate complexity.

## Implemented first slice

1. `lope/minimality.py` parses `LOPE_MINIMALITY` and exposes:
   - `implementation_directive()`,
   - `validator_rubric()`,
   - `resolve_review_focus()`.
2. `lope/implement.py::build_swarm_prompt` injects implementation guidance when mode is `audit` or `enforce`.
3. `lope/executor.py::_build_validation_prompt` injects the validator rubric for quality/legacy review stages, not spec-only review.
4. `lope review --focus over-engineering` / `--focus minimality` / `--focus lazy-build` expands to the over-engineering rubric.
5. `tests/test_minimality.py` covers mode parsing and prompt inclusion.

Next required step before default changes: run a Makakoo holdout task set and evaluate the acceptance gates above.
