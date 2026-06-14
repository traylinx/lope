# SPRINT-014 — Negotiate timeout diagnostics and context preflight

## Origin
HoCa Sprint 011 `lope negotiate` with `pi` and `opencode` appeared broken because both providers timed out. Investigation showed the CLIs were available, but the negotiate run used a low timeout with a large inlined context and slow provider invocations. Lope should make this obvious before/when it fails.

## Decision
Yes, fix Lope. This is not a core execution bug, but it is a real operator-experience bug: Lope reports provider timeout without enough context-shape and invocation diagnostics, making healthy providers look broken.

## Scope
Improve `lope negotiate` only. Do not change validator semantics or hosted provider behavior.

## Phase 1: Context preflight

**Goal:** Show prompt size and effective timeout before drafter calls.

**Criteria:**
- `lope negotiate` reports context bytes/lines and generated prompt bytes/lines when `--context-file` or `--context` is used.
- Output clearly states that context files are inlined into the prompt, not attached as files.
- If generated drafter prompt exceeds a threshold and timeout is low, print a warning before calling any CLI.
- Thresholds are configurable through environment variables, with safe defaults.

**Files:**
- `lope/cli.py`
- `lope/negotiator.py` if helper placement is cleaner
- `tests/test_negotiate_preflight.py`

**Tests:**
- Unit test: preflight reports bytes/lines for inline context.
- Unit test: context-file note appears when context file is present.
- Unit test: warning appears for large prompt + low timeout.

## Phase 2: Better drafter failure diagnostics

**Goal:** Timeout messages must name the exact invocation class and explain the likely cause.

**Criteria:**
- When a drafter times out, fallback output includes validator name, effective timeout, prompt size, and sanitized command shape.
- For `pi`, diagnostic says Lope invokes the raw binary, not shell aliases.
- For `opencode`, diagnostic says Lope default is `opencode run --pure --model myprovider/ail-compound --format json` unless `LOPE_OPENCODE_ARGS` overrides it.
- No prompt body, secrets, tokens, or credentials are printed.

**Files:**
- `lope/cli.py`
- `lope/validators.py`
- `lope/generic_validators.py`
- `tests/test_negotiate_diagnostics.py`

**Tests:**
- Simulated drafter timeout returns diagnostics without leaking prompt content.
- Opencode diagnostics include model override hint.
- Pi diagnostics include alias bypass hint.

## Phase 3: Brief-first workflow docs

**Goal:** Document the correct pattern for large sprint negotiation.

**Criteria:**
- `skills/lope-negotiate/SKILL.md` explains that `--context-file` is inlined.
- Add recommended `LOPE_BRIEF.md` workflow for large specs.
- Add timeout guidance: use default/global timeout or `--timeout 300+` for multi-page specs, never short 120s for long negotiate prompts.
- `README.md` or `docs/` gets a short troubleshooting entry for `pi`/`opencode` timeouts.

**Files:**
- `skills/lope-negotiate/SKILL.md`
- `README.md` or `docs/troubleshooting.md`
- `CHANGELOG.md`

**Tests:**
- Existing skill/doc checks still pass if present.
- No docs include secrets or local client data.

## Phase 4: Release

**Goal:** Ship as a patch release.

**Criteria:**
- Version bumped from `0.10.3` to `0.10.4`.
- Test suite passes, or any unrelated failures are documented with exact reason.
- Commit created on a feature branch.
- Branch pushed to `origin`.
- Release/tag created only after tests pass.
- Local install `/Users/sebastian/.lope` updated from the released source.

**Files:**
- `pyproject.toml`
- `lope/__init__.py`
- `CHANGELOG.md`

**Tests:**
- `pytest -q`
- Smoke: `lope ask "Say OK only" --validators pi,opencode --timeout 60`
- Smoke: `lope negotiate` with a compact brief and `--timeout 300` reaches validator feedback, not infra timeout.

## Out of scope
- Changing Pi or OpenCode model backends.
- Adding a hosted LLM fallback.
- Rewriting Lope negotiation architecture.
- HoCa scraper implementation changes.
