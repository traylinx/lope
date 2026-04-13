# Changelog

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
