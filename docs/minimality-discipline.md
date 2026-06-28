# Lope minimality discipline

Status: implemented as a Lope-native prompt/rubric slice. Engineering
`execute`/`implement` runs use soft audit by default; hard enforcement remains
explicit.

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
- Do not make hard enforcement default-on, or apply the discipline to non-engineering domains by default, until the acceptance gates pass on real Lope work.

## Mode model

Environment switch:

```bash
LOPE_MINIMALITY=off      # no prompt/rubric changes
LOPE_MINIMALITY=audit    # validators report minimality findings; implementers get soft guidance
LOPE_MINIMALITY=enforce  # implementers must apply ladder; validators can NEEDS_FIX real bloat
```

Current default:

- Engineering `lope execute` / `lope implement`: `audit`.
- Business/research `lope execute` / `lope implement`: `off`, unless `LOPE_MINIMALITY` is explicitly set or a caller passes a mode.
- `LOPE_MINIMALITY=off`: disables the engineering default.
- `LOPE_MINIMALITY=enforce`: remains explicit. It can block material bloat, but only with a concrete safer replacement and no regression to security, validation, accessibility, data-loss handling, or explicit requirements.

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

This remains available for explicit one-off over-engineering reviews outside a sprint.

## Acceptance gates before hard enforcement / broader defaults

Run on at least 30 real Lope/Makakoo tasks before promoting `enforce` to a default or applying minimality by default outside engineering:

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
2. `lope/implement.py::build_swarm_prompt` injects implementation guidance for engineering runs by default in `audit`, and for any domain when explicitly enabled.
3. `lope/executor.py::_build_validation_prompt` injects the validator rubric for engineering quality/legacy review stages by default, not spec-only review. Business/research stay off unless explicitly enabled.
4. `lope review --focus over-engineering` / `--focus minimality` / `--focus lazy-build` expands to the over-engineering rubric.
5. `tests/test_minimality.py` covers mode parsing and prompt inclusion.
