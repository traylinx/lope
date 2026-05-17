# SPRINT-LOPE-ASK-TEAM-RELIABILITY

**Date:** 2026-05-17  
**Repo:** `~/.lope` dogfood checkout  
**Branch:** `main`  
**Starting HEAD:** `14738ce` (`v0.9.0`)  
**Priority:** release-blocking before wider Makakoo public push

## Fresh deep-dive finding

The previous diagnosis was directionally right but incomplete. The real failure mode is a compound of three bugs:

1. **CLI timeout is not authoritative for custom subprocess providers.**
   - `pi` has `"timeout": 900` in `~/.lope/config.json`.
   - `GenericSubprocessValidator._run()` uses `self._timeout_override or timeout`.
   - Result: `lope ask ... --validators pi --timeout 30` can still run for 900s.
   - Outer Codex/bash timeout kills the entire Lope process before Lope can print its own per-validator result.

2. **Fanout waits for every validator before rendering.**
   - `_fanout_generate()` uses `as_completed(futures)` without a fanout-level deadline.
   - Lope is intended to isolate per-validator failures, but a long-running validator still delays the whole result set.

3. **Team UX confuses built-in availability with active membership.**
   - `codex` and `opencode` are built-in selectable validators.
   - They are not active unless present in `cfg.validators` or explicitly passed with `--validators`.
   - `lope team add codex` prints “you already have codex if its CLI is on PATH”, which misleads agents into believing codex is active.

Separate non-root-cause: the pasted `superbrain.db` error is unrelated unless `--brain-context` is used.

## Sprint goal

Ship a patch release that makes `lope ask`, `lope team list`, and built-in validator activation obvious and timeout-safe.

## Scope

### Phase 1 — timeout semantics

- Make per-call timeout the ceiling for custom subprocess providers.
- Provider timeout may shorten a call, but may not silently extend a CLI `--timeout` request.
- Error messages must report the effective timeout.

### Phase 2 — fanout resilience

- Add a fanout-level deadline to `_fanout_generate()`.
- Return per-validator timeout errors instead of waiting indefinitely.
- Preserve JSON output shape.

### Phase 3 — team UX

- Add `lope team enable <name...>` for built-in/custom disabled validators.
- Add `lope team disable <name...>` to remove active validators without deleting custom provider config.
- Make `lope team list` show available built-ins that are installed but inactive.
- Make `lope team add codex` tell the user the right command: `lope team enable codex`.

### Phase 4 — release

- Add regression tests.
- Update CHANGELOG.
- Bump version to `0.9.1`.
- Commit, tag, push, and create GitHub release.

## Acceptance gates

```bash
PYTHONPATH=. python3 -m pytest tests/test_new_verbs.py tests/test_team.py -q
PYTHONPATH=. python3 -m pytest tests/ -q
PYTHONPATH=. python3 -m lope version
PYTHONPATH=. python3 -m lope ask "Reply with OK only." --validators pi --timeout 10 --json
PYTHONPATH=. python3 -m lope ask "Reply with OK only." --validators codex,opencode,pi,claude --timeout 8 --json
PYTHONPATH=. python3 -m lope team list
```

## Non-goals

- Do not redesign Lope.
- Do not require all external CLIs to answer quickly.
- Do not make Superbrain/Brain recovery part of this release.
