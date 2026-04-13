# Changelog

## 0.4.6 — Drafter auto-fallback (Chernobyl fix)

**Critical bug: single-point-of-failure in the drafter stage.**

`ValidatorPool` already fell back on `INFRA_ERROR` during the REVIEW stage (primary → next → next). The DRAFT stage did not — `_cmd_negotiate` caught only `NotImplementedError` from `primary.generate()` and let every other exception propagate. If a user's primary CLI crashed for ANY reason (stale model config, broken binary, expired auth, subprocess stdin weirdness), `lope negotiate` bricked with a cryptic subprocess error before a single validator saw the draft.

This is exactly the bug that bit the public campaign on launch day: a config pointing at `codex` with a nonexistent `gpt-5.4` model → codex exits 1 → lope dies → marketing copy never got validated.

### Fix

`_cmd_negotiate` now builds a drafter fallback chain from the full validator pool (primary first, then all others). On any exception from `drafter.generate()`, it logs `[drafter fallback] <name> failed, trying <next>...` and continues. Only fails when EVERY validator in the pool has been tried. Error message upgraded: references `lope status`, suggests known-good primaries, points at `LOPE_LLM_URL` as last resort.

### Why this matters

Lope's whole premise is "any CLI implements, any CLI validates." A broken single primary should not defeat the ensemble — the ensemble IS the redundancy. v0.4.6 makes the drafter stage honor that premise the same way the reviewer stage already did.

Tested against a config deliberately pointing at a broken `codex` primary with `opencode` and `vibe` in the pool: lope logged the fallback, drafted via opencode, ran the reviewer ensemble, and returned a real verdict instead of crashing.

## 0.4.5 — Full test coverage: 10 → 81 tests

v0.4.4 shipped with 10 passing tests (Phase 1 of the v0.4.0 sprint — `load_layered` precedence chain, autonomously written by claude during the meta-dogfood). v0.4.5 brings the remaining four phases under pytest:

- **Phase 2 — atomic + locked writes** (`tests/test_atomic_writes.py`, 11 tests): save/load round-trip including learned_adapters, version field is schema version, no tmp files leftover after a clean save, save overwrites cleanly, `_safe_read` handles missing / malformed / valid files, 10 parallel processes all call `save()` with distinct payloads and the final file parses cleanly as exactly one writer's state (no torn merge), `load` rejects wrong schema version.
- **Phase 3 — self-heal detection** (`tests/test_self_heal.py` sections): `_is_flag_error` parametrized across 8 real flag-break patterns (unrecognized arguments, unknown option, no such option, invalid option, usage: header) and 8 non-flag failures (rate limit, connection error, timeout, permission denied, content policy), `AdapterFlagError` carries + copies argv context, `_infra_error` attaches `flag_error_hint` on match and omits it on non-flag failure.
- **Phase 4 — self-heal execution** (rest of `tests/test_self_heal.py`): `SelfHealer.should_attempt` gate under 5 scenarios (env unset, env set, truthy env values, session state, reviewer availability, empty cli_name), `_parse_heal_response` handles fenced JSON / bare JSON / malformed / missing argv / non-list argv, `_build_heal_prompt` includes old argv + stderr + help + schema + placeholder docs and truncates long help, `_fill_template` expands `{prompt}` and `{binary}`, `is_adapter_expired` TTL (old / fresh / legacy-zero), full `SelfHealer.attempt` end-to-end with mocked reviewer + mocked subprocess across 5 paths (success, reviewer garbage, smoke test fail, help capture fail, reviewer exception).
- **Phase 5 — observability** (`tests/test_journal.py`, 10 tests): `journal_path` respects `LOPE_HOME`, `append_event` writes one JSONL line per call with timestamp, multiple calls append, timestamps are monotonic nondecreasing, `append_event` creates parent directory, `read_recent` returns empty / all / limit-newest-last / skips malformed lines, **`append_event` swallows disk errors** (best-effort observability never propagates exceptions), `read_recent` on missing file returns empty list.

### The numbers

```
$ pytest tests/ -v
tests/test_config_layered.py  10 passed  (v0.4.4 — claude's autonomous test suite)
tests/test_atomic_writes.py   11 passed  (v0.4.5 — Phase 2)
tests/test_journal.py         10 passed  (v0.4.5 — Phase 5)
tests/test_self_heal.py       50 passed  (v0.4.5 — Phases 3 + 4, parametrized)

81 passed in 2.00s
```

From zero tests at v0.3.0 to 81 tests at v0.4.5 in one session. Ten of those were written autonomously by the lope ensemble itself during the v0.4.3 meta-dogfood; seventy-one were hand-written for this release after the v0.4.4 meta-dogfood-on-negotiate spin escalated on prose-ellipsis lint friction (claude's drafter style couldn't converge).

### Dogfood lesson

The lope-on-lope meta-dogfood shines for **implementation review** — it caught the `load_layered` spec drift that a single-model loop would have shipped. But the meta-dogfood **struggled on pure-test-writing negotiation** — 2 rounds of negotiate spin on nothing more than "write 20 tests, no prose ellipses" because claude's paragraph-shaping style kept injecting `...` into phase rationales and v0.3.0's strict lint kept rejecting. The right wedge: use lope-on-lope for code implementation with validator ensemble review, hand-write for test suites where the scope is crisp and the value of ensemble review is marginal.

### Not yet covered

- `_cmd_execute` autonomous-execute path end-to-end (would need a fake `Validator` subclass with deterministic `.generate()` — deferred to v0.4.6)
- `_cmd_negotiate` happy path (same reason)
- Two parallel `lope negotiate` invocations with different env vars (the concurrent-session-safety promise) — requires real subprocess harness; deferred

### Upgrade path

v0.4.5 is a pure additive release — no API changes, no schema changes, no config file format changes. Users on v0.4.4 can upgrade with a simple `git pull && git reset --hard v0.4.5`. No migration required.

## 0.4.4 — Meta-dogfood fruit: autonomous tests + spec drift fix

The v0.4.3 meta-dogfood with **claude as primary** (via `LOPE_PRIMARY=claude LOPE_VALIDATORS=claude,opencode`, in-memory only — `~/.lope/config.json` mtime unchanged, verifying the v0.4.0 config scoping) ran for **28 minutes**, three full autonomous rounds of implementation → two-stage validator review → NEEDS_FIX retry. Phase 1 escalated on attempt 3 when the opencode reviewer caught a genuine spec-vs-code API drift.

This is lope working exactly as designed. A single-model loop would have shipped the drift. The ensemble caught it.

### The spec drift

Sprint doc Phase 1 **Files:** said:

> `lope/config.py` — add `load_layered(cwd, env, cli_overrides)` that merges all four layers with clear precedence.

My v0.4.0 implementation had signature `load_layered(cwd, cli_overrides)` — no explicit `env` parameter. Env vars were read from `os.environ` directly inside the function. This shipped and worked for the common case, but:

- Made unit testing harder (monkeypatch required instead of passing a dict)
- Diverged from the documented API
- Opencode's 3 review rounds flagged `config.py:175 — load_layered signature is (cwd, cli_ov...` as a spec deviation

### Fix in v0.4.4

- **`load_layered(cwd=None, env=None, cli_overrides=None)`** — added explicit `env: Optional[Dict[str, str]]` parameter. Defaults to `os.environ` when None. Tests can now pass a custom dict without touching global state.
- **Internal env var reads** in `load_layered` now use the `env` mapping arg (not `os.environ` directly). Backwards-compatible: all existing callers work without changes because `env=None` defaults to `os.environ`.
- **Matches sprint doc.** Validator escalation resolved by making code match spec, not spec match code.

### Autonomous-written test suite (`tests/test_config_layered.py`, 437 lines, 10 tests)

Claude, running as the primary implementer in the meta-dogfood, **wrote the entire Phase 1 test file autonomously** across the 3 rounds. This is the first lope release where part of the code was written by the autonomous loop lope runs. Tests added:

- `test_load_layered_full_precedence` — the 4-layer precedence chain with all layers set (CLI > env > project > global). The acceptance criterion from Phase 1 of the v0.4.0 sprint doc.
- `test_load_layered_global_only_matches_load` — regression: `load_layered()` with only global config equals `load()`.
- `test_lope_validators_env_parsed` — `LOPE_VALIDATORS="claude,gemini"` parses to `["claude", "gemini"]`.
- `test_lope_validators_env_strips_whitespace` — trims whitespace in comma-separated values.
- `test_cli_validators_flag_does_not_mutate_global` — **the critical integration test**: running `lope negotiate --validators opencode,gemini` does not touch `~/.lope/config.json` mtime. This is the v0.4.0 concurrent-session-safety promise verified automatically.
- `test_project_config_overrides_global_primary` — per-project `.lope/config.json` overrides global primary without mutating the global file.
- `test_configure_writes_to_global_path` — regression: `lope configure` still writes to `~/.lope/config.json` (the one explicit-save path that DOES touch global).
- `test_project_config_no_version_accepted` — new relaxed version policy: per-project configs with absent `version` key are accepted as partial overrides.
- `test_lope_sequential_forces_sequential` — `LOPE_SEQUENTIAL=1` flips the default parallel ensemble to sequential.
- `test_learned_adapters_not_inherited_from_project` — per-project configs cannot override the user-global `learned_adapters` (intentional: healer state is always user-global).

All 10 tests pass on first run against v0.4.4 code: `pytest tests/test_config_layered.py` → **10 passed in 3.45s**.

### Quality improvements also shipped (also claude-written during the meta-dogfood)

- **`docs/reference.md`** — added the new `--validators` / `--primary` / `--timeout` / `--parallel` / `--sequential` flags to the documented usage blocks for all three subcommands (`negotiate`, `execute`, `audit`). Updated the "do not invent flags" hard rule to include the new flag list.
- **`lope/config.py`** — relaxed per-project version policy: configs with absent `version` key are accepted (partial override). Only rejected on explicit version mismatch with the current schema. Dual `_safe_read` call for project config collapsed into one, cleaner.
- **`lope/cli.py`** — cleaner `--parallel`/`--sequential` default pattern using `set_defaults(parallel=None)` instead of `default=None` on each add_argument, which was causing argparse to reject the combination.

### Dogfood tally — 9 bugs caught in one session

| # | Bug | Shipped in |
|---|---|---|
| 1 | v0.3.1 OpenCode path singular vs plural | v0.3.2 |
| 2 | Zero-phase sprint false success | v0.4.0 |
| 3 | `_cmd_execute` hardcoded `input()` | v0.4.0 |
| 4 | Global config clobber / no pool flags | v0.4.0 |
| 5 | `CodexValidator` hardcoded `--quiet` | v0.4.1 |
| 6 | Self-heal validate-only (gap in generate path) | v0.4.1 |
| 7 | `_phase_to_prompt` list/str crash on retry | v0.4.2 |
| 8 | `CodexValidator` stdin inheritance | v0.4.3 |
| 9 | `load_layered` signature drift from spec | v0.4.4 |

Plus one operational signal: codex account rate-limited, resolved by switching primary via env var (which is literally the feature v0.4.0 config scoping was designed for).

### What's still open

- **Phases 2-5 of the v0.4.0 sprint** never ran in the meta-dogfood — Phase 1 escalated on attempt 3 and the sprint stopped there. Phase 2 (atomic writes + fcntl.flock) is already implemented in v0.4.0 and covered by manual smoke tests, but not yet autonomously re-verified. Phases 3/4 (self-heal detection + execution) and Phase 5 (observability + docs) are implemented and smoke-tested (18 manual assertions) but not yet covered by the new pytest suite.
- **Tests for phases 2-5** are the next natural step. Either hand-write them now or trigger another autonomous round with claude as primary (which is cheaper than the v0.4.3 run because Phase 1 is now green).

## 0.4.3 — Codex stdin inheritance fix

Eighth bug caught by the v0.4.2 meta-dogfood retry. Codex 0.120.0 now reads from stdin whenever stdin is inherited (not a TTY) — it looks for additional input to append as a `<stdin>` block alongside the argv prompt. When run from a non-interactive subprocess with no stdin piped, codex blocks or errors with:

```
Reading additional input from stdin...
```

and exits 1 before ever hitting the model.

### Fix

`CodexValidator.generate()` and `.validate()` both pass `input=""` to `subprocess.run()` now, explicitly piping an empty stdin so codex treats the argv prompt as the sole instructions. Before v0.4.3 the `stdin` kwarg was omitted, which inherited the parent's stdin — fine in a foreground TTY, broken in any non-TTY context (background tasks, CI, nested subprocesses like the autonomous execute loop).

### Housekeeping

- Removed broken symlink `~/.codex/skills/skill-creator → ../../.agents/skills/skill-creator` that was polluting codex's stderr on every run. Not a lope file, but it was a distraction when diagnosing this bug.

### Dogfood tally (still climbing)

- v0.4.0 → bugs 5+6 (codex `--quiet`, self-heal generate() gap)
- v0.4.1 → bug 7 (fix_context list/str mismatch)
- v0.4.2 → bug 8 (codex stdin inheritance)
- v0.4.3 → meta-dogfood still blocked at this point because codex account is also **rate-limited** ("You've hit your usage limit"). Next step is to switch primary to claude via `LOPE_PRIMARY=claude` env var, which also dogfoods the v0.4.0 config-scoping feature (env var override without touching `~/.lope/config.json`).

## 0.4.2 — `_phase_to_prompt` handles list fix_context

v0.4.1's meta-dogfood advanced one full retry round deeper than v0.4.0 before hitting the next bug. The autonomous implementer ran, codex returned 767 chars, validators reviewed, spec NEEDS_FIX with 7 required fixes, executor correctly started attempt 2 — then crashed:

```
File "/Users/sebastian/Projects/lope/lope/cli.py", line 683, in _phase_to_prompt
    return "\n".join(parts)
TypeError: sequence item 22: expected str instance, list found
```

`_phase_to_prompt(phase, doc, fix_context)` assumed `fix_context` is a string. The executor's actual call shape is `fix_context: Optional[list]` — specifically `list(phase.verdict.required_fixes)` — so attempt 2 always crashed when validators returned NEEDS_FIX with any fixes. v0.4.1 worked only for the happy path where attempt 1 passed immediately.

### Fix

- `_phase_to_prompt` now handles `list`, `tuple`, `str`, and `None` shapes for `fix_context`. List items render as bulleted lines (`- <fix>`), matching the rest of the sprint prompt format.
- Smoke test: 3 shapes (str / list / None) verified programmatically.

### Why this matters

The NEEDS_FIX retry loop is the whole point of multi-round validator review. Without it lope is no better than a single-shot code generator. v0.4.1 shipped with retry loops that worked in theory but crashed on the first real retry because the CLI frontend made an assumption about the callback contract that the executor doesn't guarantee.

### Dogfood tally

v0.4.0 → 1 bug (codex --quiet hardcoded + self-heal generate() gap). v0.4.1 → 1 bug (fix_context list/str mismatch). Each release fixes one layer of the onion and surfaces the next. v0.4.2 is the first release where the meta-dogfood should actually reach Phase 2 of the sprint.

## 0.4.1 — Codex `--quiet` flag break + self-heal on generate() path

The v0.4.0 meta-dogfood — running v0.4.0 lope against its own sprint doc — surfaced the *exact* self-heal scenario v0.4.0 was built to fix:

**Codex upstream removed `--quiet`.** `CodexValidator` had `codex exec --quiet <prompt>` hardcoded in both `validate()` and `generate()`. The new codex binary rejects the flag with `error: unexpected argument '--quiet' found / Usage: codex exec [OPTIONS]`. Every autonomous run with codex as primary failed before v0.4.0 even reached the validators.

**v0.4.0 self-heal only covered `validate()`, not `generate()`.** The heal detection was wired into `_infra_error()`, which is called on the `validate()` failure path. But `Validator.generate()` bypasses `_infra_error` and raises `RuntimeError` directly. So when a flag break happened during *implementation* (the generate() call in the new autonomous `_cmd_execute`), the error flowed up as a generic `Exception`, my implementation_fn caught it, returned `ok=False, summary="unknown"`, and the executor escalated. SelfHealer was never invoked.

### Fixes

- **`CodexValidator.validate()` and `.generate()`** — dropped `--quiet` from both argv templates. Smoke test: `codex.generate("Reply with the single word OK")` returns `"OK\n"` in under 60s.
- **Generate-path flag-error detection** — `_cmd_execute`'s implementation_fn now calls `_is_flag_error(err_msg)` on any exception from `primary.generate()`. When matched, it routes through a new `_try_self_heal_from_generate()` helper which:
  1. Finds a reviewer in the pool that is not the failing CLI
  2. Calls `SelfHealer.should_attempt()` to check `LOPE_SELF_HEAL=1` and session state
  3. Invokes `SelfHealer.attempt()` with the error message as stderr (sufficient context for the reviewer to propose a corrected invocation)
  4. On success, retries the phase with the learned adapter; on failure, escalates with a clear "set LOPE_SELF_HEAL=1" hint
- **Escalation message upgrade** — v0.4.0 reported `implementation_fn failed: unknown` on any exception. v0.4.1 surfaces the full error type, first 400 chars of the message, and a hint about `LOPE_SELF_HEAL=1`.

### Why this is a patch release, not a new feature

The v0.4.0 self-heal architecture was correct; the wiring was incomplete. v0.4.1 extends the same detection logic (`_is_flag_error`) and the same `SelfHealer` class to a path v0.4.0 missed. No schema changes, no new env vars, no new dependencies. Users on v0.4.0 should upgrade — v0.4.0 is broken for any autonomous run where codex is primary.

### Dogfood notes

- Found via the meta-dogfood run: `cd ~/Projects/lope && lope execute ~/HARVEY/development/sprints/lope/SPRINT-LOPE-V0.4.0-ADAPTER-RESILIENCE.md`. Escalated in 0.4s with a non-descriptive error. Direct test of `codex.generate()` surfaced the `--quiet` flag break.
- Codex is the 2nd validator in the lope repo's default pool (`primary: codex, validators: [codex, opencode]`). Other default pools (e.g. `primary: claude`) were unaffected. Still, the bug would have bitten any user whose primary is codex — which is every Harvey-OS user by default.
- The lesson scales: **self-heal has to cover every codepath that calls a validator subprocess**, not just validate(). Any future validator method (draft/review/impl/audit) must be routed through `_infra_error` or an equivalent helper that attaches `flag_error_hint`.

## 0.4.0 — Adapter resilience: autonomous execute, config scoping, self-heal

Fixes three silent bombs caught by dogfooding v0.3.x on itself and ships the first real adapter-layer resilience primitives. This is the "final working version" release that v0.3.0's marketing copy was always about.

### The three bugs that triggered this release

1. **`lope execute` was not actually autonomous.** The `_cmd_execute` hook at `cli.py` hardcoded `input()` as the implementation callback — it waited for a **human** to manually implement each phase and press Enter, then validators reviewed the human's work. Every blog post, LinkedIn post, and README line about "autonomous sprint runner" was overstated. v0.4.0 replaces the `input()` with `primary.generate(build_impl_prompt(phase, fix_context))`, using the primary validator's existing `generate()` method (added in v0.2.1 for negotiate drafting). The primary CLI runs as a subprocess in the current working directory and writes files directly. Legacy human-in-the-loop mode is still available via `lope execute --manual`.

2. **`lope execute` reported "All phases passed!" on zero-phase sprints.** If the sprint doc had the wrong heading level (level-2 `## Phase` instead of level-3 `### Phase`), the phase parser returned an empty list and lope printed the victory mascot. Shipped as a clean PASS. v0.4.0 fails loudly with a clear error pointing at the heading format.

3. **Every "switch which validators to use" rewrote `~/.lope/config.json`.** There were no `--validators`/`--primary` CLI flags and no `LOPE_VALIDATORS` env var. Running two `lope negotiate` invocations from two terminals with different validator pools was impossible — whichever wrote last silently clobbered the other mid-flight. v0.4.0 introduces a 5-layer config precedence chain (below) so each invocation is self-contained.

### Config precedence (new)

5 layers, highest wins per field:

1. **CLI flags** — `--validators opencode,gemini --primary opencode --timeout 240 --parallel/--sequential`. Added to `negotiate`, `execute`, and `audit` subcommands. Zero persistence.
2. **Env vars** — `LOPE_VALIDATORS`, `LOPE_PRIMARY`, `LOPE_TIMEOUT`, `LOPE_PARALLEL`, `LOPE_SEQUENTIAL`. Per-shell-session scope.
3. **Per-project config** — `./.lope/config.json` in cwd. Repo-scoped defaults.
4. **User global config** — `~/.lope/config.json`. Only `lope configure` writes here.
5. **Built-in defaults** — empty validators, 480s timeout, parallel=True.

Each layer overrides field-by-field, not whole-object. See `docs/reference.md` section "Config precedence" for the full semantics.

### Atomic + locked writes on `~/.lope/config.json`

`config.py` `save()` now acquires an `fcntl.flock(LOCK_EX)` on a sidecar `.lock` file before writing. Concurrent `save()` calls from different processes serialize via the lock; readers never see partial state because the rename is atomic. Also adds `_safe_read()` that tolerates the open-vs-rename race with a 50ms retry backoff. Platforms without `flock` (Windows without POSIX emulation) fall through to best-effort without the lock.

### Self-healing validator adapters (opt-in)

New `lope/healer.py` module with `SelfHealer` class:

- **Detection** — `_is_flag_error()` in `validators.py` matches stderr patterns like `unrecognized argument`, `unknown option`, `no such option`, `usage:` header followed by a non-zero exit. When `_infra_error()` builds a failure result, it attaches a `flag_error_hint` to `ValidatorResult` so the pool boundary can route the failure through the healer.

- **Heal sequence** — captures `<cli> --help` with a 10-second timeout, builds a reviewer prompt that includes the old argv, stderr, and help output, asks the primary reviewer for a corrected JSON proposal (schema: `argv_template`, `stdin_mode`, `stdout_parser`, `confidence`, `rationale`), and smoke-tests the proposal with a fixed prompt *"Reply with the single word OK and nothing else."* On smoke-test pass, persists a `LearnedAdapter` to `~/.lope/config.json` under `learned_adapters.<cli_name>` via the atomic+locked Phase 2 save.

- **Guardrails** — one heal attempt per CLI per session (process-local set, no infinite loops), skipped when no reviewer is available, gated by `LOPE_SELF_HEAL=1` for v0.4.0 (will default-on in v0.5.0 once telemetry is clean), 90-day TTL on learned adapters before re-verification.

- **Journaled** — every `heal_attempt`, `heal_success`, `heal_failure`, `heal_skipped` event lands in `~/.lope/journal.jsonl` via the new `lope/journal.py` module. `lope status` surfaces recent heal events inline.

### New `LearnedAdapter` schema

`LopeCfg` gains a `learned_adapters: Dict[str, LearnedAdapter]` field. Backwards-compatible — missing means empty dict. Schema: `argv_template` (list of str with `{prompt}` placeholder), `stdin_mode` ("none"|"pipe"), `stdout_parser` ("plaintext"|"json:dot.path"), `timestamp` (unix seconds), `source_cli` (which reviewer proposed it), `confidence` (0.0-1.0).

### `lope status` (expanded)

Now prints two new blocks when applicable:

- **Learned adapters** — each healed CLI with its age in days, source reviewer, confidence score, and warning flags for adapters nearing the 90-day TTL (`[aging — re-verify soon]` at 60 days, `[EXPIRED]` past 90).
- **Recent heal events** — the last 5 heal events from the journal with type, CLI name, and minutes-ago timestamp.

### `docs/reference.md` expanded

Two new sections:
- **Config precedence** — the 5-layer hierarchy explained with examples.
- **Self-healing adapters** — full description of the heal sequence, guardrails, and opt-in flag.

### New files

- `lope/healer.py` (~290 lines) — `SelfHealer` class, `_build_heal_prompt`, `_parse_heal_response`, `_fill_template`, `is_adapter_expired`, TTL + smoke-prompt constants.
- `lope/journal.py` (~65 lines) — `append_event`, `read_recent`, `journal_path`.

### Modified files

- `lope/config.py` — `load_layered`, `_safe_read`, `fcntl.flock` in `save`, `LearnedAdapter` dataclass, `_hydrate_cfg` picks up the new field, `project_path` helper for `./.lope/config.json`.
- `lope/cli.py` — `_add_pool_flags` attached to negotiate/execute/audit, `_ensure_config(args)` takes CLI overrides, `_cmd_execute` autonomous implementation via primary validator (fixes the `input()` bug), zero-phase sprint fail-loud, `_phase_to_prompt` helper for the implementer prompt, `lope status` shows learned adapters + heal events.
- `lope/validators.py` — `_is_flag_error` helper, `AdapterFlagError` class, `_infra_error` now attaches `flag_error_hint` to results that look like flag breaks.
- `lope/models.py` — `ValidatorResult.flag_error_hint` field.
- `docs/reference.md` — new sections on config precedence + self-healing.

### Dogfood notes

- Shipped using the `scripts/bump-version.sh` + `scripts/check-version.sh` tooling added in v0.3.1/v0.3.2. All 6 version strings stayed in sync on first try — zero manual bumps.
- The three bugs above were found by running `lope execute` against itself during the v0.4.0 sprint and watching it no-op, false-succeed, and EOFError on first phase. "Dogfood before publish" (the v0.2.0 auth-header incident's lesson) scales — every release needs its own dogfood pass.

### Not shipped in v0.4.0 (next)

- Retrofitting each `Validator` subclass to surface `AdapterFlagError` directly is not strictly needed since `_infra_error` picks up the pattern for every subclass already. Individual subclass retrofits are opt-in refinement for v0.4.1.
- `tests/` directory (the public repo has no test suite — tests live internally). Adding a pytest-based suite to the public repo is v0.4.1+.
- Default-on self-heal — remains opt-in behind `LOPE_SELF_HEAL=1` until v0.5.0.
- Learned adapters for HTTP providers — subprocess only for v0.4.0.

## 0.3.2 — Honest per-host matrix + `lope docs` + `/lope-help`

Fixes v0.3.1's over-promise of "install works everywhere." After live-testing the install flow in multiple CLIs and asking each host directly whether it supports user slash commands, the honest state turned out to be:

| Host | Slash commands | Natural language | Status |
|---|---|---|---|
| Claude Code | ✅ | ✅ | works |
| Gemini CLI | ✅ (namespaced) | ✅ | works |
| OpenCode | ✅ | ✅ | works (path was wrong in v0.3.1) |
| Codex | ❌ (confirmed by Codex) | ✅ | content-only |
| Mistral Vibe | ❌ (confirmed by Vibe) | ✅ | content-only |
| Cursor | ⚠️ unverified | ✅ | best-effort |

### What's new

- **`lope docs` subcommand** — prints the complete authoritative reference (all modes, all flags, all domains, env vars, per-host support matrix, troubleshooting, hard rules) from a single source file at `docs/reference.md`. Single source of truth for agents that need to know how lope works.
- **New `/lope-help` slash command** (`/lope:help` in Gemini) — thin skill that instructs the host agent to run `lope docs` and load the output into context, then answer the user's question from the reference. Memorize nothing; pull fresh every time.
- **New `skills/lope-help/SKILL.md`** — installed alongside the other lope skills on every host that supports slash commands.
- **`docs/reference.md`** — the canonical reference document, shared by `lope docs` and the `lope-help` skill body.

### Install fixes

- **OpenCode path corrected** from `~/.config/opencode/command/` (singular) to `~/.config/opencode/commands/` (plural). v0.3.1 shipped with the wrong path; v0.3.2 cleans up the old dir automatically on install.
- **OpenCode command files now carry the required `agent: build` YAML frontmatter.** v0.3.1 symlinked raw SKILL.md files which OpenCode rejected. v0.3.2 ships pre-authored wrapper files at `commands/opencode/*.md` with the correct frontmatter shape, each delegating to `lope <mode>` via bash in its body.
- **INSTALL.md rewritten with an honest per-host support matrix** at the top, so agents read which section applies before copying commands. Codex and Vibe sections now tell agents explicitly that those hosts don't register user slash commands and to invoke lope via natural language instead.
- **Installer output rewritten** — instead of claiming all 6 hosts get slash commands, it now prints two tables: "slash commands by host" for the 3 hosts that actually work, and "content-only hosts" for Codex + Vibe.

### Internals

- `lope/cli.py` gains `_cmd_docs()` — reads `docs/reference.md` from either the repo root or `~/.lope`, falls back cleanly if the file is missing.
- Bash installer uses the new `commands/opencode/*.md` wrapper files (generated once, symlinked in) instead of constructing wrappers at install time. Zero drift, zero installer logic for frontmatter.

No engine changes. Same validator pool, two-stage review, evidence gate, placeholder lint as v0.3.0/v0.3.1.

## 0.3.1 — Install works everywhere

- **`INSTALL.md` rewritten as a per-host router.** The agent reading the file identifies which CLI it runs inside and jumps to the matching section — Claude Code, Codex, Gemini CLI, OpenCode, Cursor, Mistral Vibe, GitHub Copilot CLI, or a generic fallback. Each section has exact shell commands for that host's native skill/command path. No guessing.
- **Mistral Vibe support.** `~/.vibe/skills/` is now a first-class install target alongside Claude Code and Codex. Standard SKILL.md directory format — nothing special required.
- **Explicit restart guidance.** Every host caches its skill list at session start, so freshly-installed `/lope-*` commands never appear mid-session. The installer and INSTALL.md both tell users to quit and reopen their CLI before slash commands show up.
- **Bash installer updated** with a `vibe` host branch and `--host vibe` flag. `./install --host all` now writes to all 6 detected hosts.
- **Generic fallback section** in INSTALL.md for any CLI we haven't explicitly branched on — tells the agent to symlink `~/.lope/skills/*/SKILL.md` into whatever path its host expects.

No engine changes. Same validator pool, same two-stage review, same evidence gate, same lint as v0.3.0.

## 0.3.0 — Initial public release

Lope is an autonomous sprint runner with a multi-CLI validator ensemble. Any AI CLI implements. Any AI CLI validates. Majority vote decides.

### What's in the box

- **Three modes:** `/lope-negotiate` (draft a sprint doc via multi-round validator review), `/lope-execute` (run phases with validator-in-the-loop retry), `/lope-audit` (generate the scorecard). Plus `/lope` for the umbrella and `using-lope` for natural-language auto-triggering.

- **Any CLI drafts, any CLI validates.** The primary validator in your pool drafts the sprint via subprocess (its own CLI — `claude --print`, `opencode run`, `gemini --prompt`, `codex exec`, `aider --message`). Other validators independently review. No separate hosted LLM required; lope's entire reasoning runs through the CLIs you already have.

- **12 built-in CLI adapters:** Claude Code, OpenCode, Gemini CLI, Codex, Mistral Vibe, Aider, Ollama, Goose, Open Interpreter, llama.cpp, GitHub Copilot CLI, Amazon Q. Plus infinite custom providers via JSON config — any HTTP endpoint or subprocess you can describe in five lines becomes a validator.

- **Three domains — not just for code.** `engineering` (code, software, infra), `business` (marketing campaigns, budgets, ops, consulting, legal, teaching), `research` (studies, systematic reviews, academic work, replication studies). Same validator loop, different role prompts and artifact labels. Business sprints use `**Deliverables:** / **Success Metrics:**`, research sprints use `**Artifacts:** / **Validation Criteria:**`, engineering sprints use `**Files:** / **Tests:**`. The ensemble checks what it always checks: specific plan, measurable criteria, complete scope, poke-a-hole review. See the [Use cases section in README](README.md#use-cases) for 9 worked examples across all three domains.

- **Two-stage validator review.** Each phase gets validated twice per retry: first spec compliance ("does this match the Goal?"), then code quality ("is this well-built?"). Spec NEEDS_FIX short-circuits the quality pass. Spec FAIL escalates immediately. Separates "clever slop that misses the requirement" from "meets spec but rough around the edges".

- **Verification-before-completion gate.** Any validator returning PASS with a rationale that lacks evidence (no file:line reference, no test output, no code fence, no explicit verification phrase) gets auto-downgraded to NEEDS_FIX with a synthesized "provide evidence" fix. Kills rubber-stamping architecturally. Word-boundary matching prevents false positives on substrings like "looks" or "passage".

- **No-placeholder lint on drafts.** Negotiator rejects drafts containing `TBD`, `TODO`, `XXX`, `FIXME`, bare prose ellipsis, `<placeholder>` tokens, or phases with empty artifact/check lists. On lint failure, the drafter loops back with specific fix instructions before any validator sees the draft — cheaper than a validator round.

- **Intelligent caveman mode.** Token-efficient validator prompts: drops articles, filler, and hedging while keeping code, paths, line numbers, and error messages exact. 50-65% token savings per validator call. Adapted from [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman). Controlled via `LOPE_CAVEMAN=full|lite|off`.

- **SessionStart hook.** One-paragraph briefing injected into your agent's context on every new session, so agents know lope exists and when to suggest it without the user having to remember a slash command. Specific about when NOT to trigger (single edits, pure conversation, trivial ops). Opt-out via `LOPE_HOOK=off`.

- **`using-lope` auto-trigger skill.** Meta-skill that recognizes natural-language descriptions of multi-phase work ("plan the auth refactor", "negotiate the Q4 campaign carefully") and invokes `lope negotiate` on your behalf. You don't have to type slash commands.

- **Install in one prompt.** Paste one line into any AI agent:

  ```
  Read https://raw.githubusercontent.com/traylinx/lope/main/INSTALL.md and follow the instructions to install lope on this machine natively.
  ```

  Your agent fetches `INSTALL.md`, follows six short steps, reports back when lope is live. CLI-agnostic — writes skills and commands into each host's native command directory using the format that host expects.

- **Cross-CLI slash commands.** `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit` autocomplete natively in Claude Code, Codex, Cursor, OpenCode. Gemini CLI uses namespaced syntax: `/lope:negotiate`, `/lope:execute`, `/lope:audit`. Plugin manifests ready for marketplace publication.

- **Zero external Python dependencies.** Pure stdlib. Works out of the box on Python 3.9+ without a venv. The entire engine is ~2000 lines of readable Python.

- **MIT licensed.**

### Escape hatches

| Env var | Effect |
|---|---|
| `LOPE_LINT=off` | Skip the no-placeholder lint on drafts |
| `LOPE_EVIDENCE_GATE=off` | Skip the PASS-needs-evidence downgrade |
| `LOPE_SINGLE_STAGE=1` | Revert execute mode to legacy single-pass validation |
| `LOPE_HOOK=off` | Suppress the SessionStart briefing |
| `LOPE_CAVEMAN=off` | Disable token compression on validator prompts |
| `LOPE_LLM_URL` | Optional hosted LLM fallback when primary validator can't draft |
| `LOPE_LLM_API_KEY` | Bearer token for the fallback endpoint |
| `LOPE_WORKDIR` | Working directory for validator subprocesses |
| `LOPE_TIMEOUT` | Validator timeout in seconds (default 480) |
