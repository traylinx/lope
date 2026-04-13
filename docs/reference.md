# Lope — Complete Reference

This is the authoritative reference for lope. It's the single source of truth read by:

- `lope docs` subcommand (prints this file)
- `/lope-help` slash command (skills/lope-help/SKILL.md — delegates to `lope docs`)
- `/lope:help` slash command (Gemini CLI, via commands/lope/help.toml)

If you are an AI agent reading this because the user asked about lope, load it into your context and answer from it. Do not read other lope source files unless this doc points you to them.

---

## What lope is

Lope is a **multi-CLI validator ensemble sprint runner**. Any AI CLI implements. Any AI CLI validates. Majority vote decides if a phase ships. No single-model blindspot.

Lope is not a library. It's not a framework. It's a CLI harness that runs **other** AI CLIs as validators. You don't embed lope in Python code — you invoke `lope <mode> <args>` from a shell, and lope orchestrates subprocess calls to Claude Code, OpenCode, Gemini CLI, Codex, etc.

Works for **three domains**: `engineering` (default), `business`, `research`. Same loop, different validator role prompt and artifact labels.

Repo: https://github.com/traylinx/lope · MIT · Zero Python dependencies (pure stdlib).

---

## The three modes

| Mode | CLI | Slash command (where supported) | What it does |
|---|---|---|---|
| **Negotiate** | `lope negotiate <goal>` | `/lope-negotiate` | Primary CLI drafts a structured sprint doc. Other CLIs independently review. Majority vote. Iterates until consensus or escalation. Writes the sprint doc to disk. |
| **Execute** | `lope execute <sprint_doc>` | `/lope-execute` | Runs the sprint phase by phase. Each phase: primary implements, then two-stage validator review (spec compliance, then code quality). NEEDS_FIX retries with fix instructions (3 attempts). PASS advances. FAIL escalates. |
| **Audit** | `lope audit <sprint_doc>` | `/lope-audit` | Generates a scorecard from executed sprint results — per-phase verdicts, confidence scores, duration, overall status. Appends to lope's journal. |

Default flow: **negotiate → execute → audit**. Users usually run negotiate, hand-review the sprint doc, then run execute.

---

## CLI reference

### `lope negotiate <goal>`

Draft a sprint doc via multi-round validator review.

```
Usage: lope negotiate [-h] [--out OUT] [--max-rounds MAX_ROUNDS]
                     [--context CONTEXT]
                     [--domain {engineering,business,research}]
                     [--validators VALIDATORS] [--primary PRIMARY]
                     [--timeout TIMEOUT] [--parallel | --sequential]
                     goal

Positional:
  goal                        Sprint goal description (one sentence to one paragraph).

Flags:
  --out OUT                   Output path for sprint doc (default: ./SPRINT-<slug>.md).
  --max-rounds MAX_ROUNDS     Max negotiation rounds before escalation (default: 3).
  --context CONTEXT           Additional context string or file path (e.g., --context @CLIENT-BRIEF.md).
  --domain DOMAIN             engineering (default) / business / research.
  --validators VALIDATORS     Comma-separated validator list, e.g. opencode,gemini (overrides config).
  --primary PRIMARY           Primary validator name (must be in --validators or global config).
  --timeout TIMEOUT           Per-validator timeout in seconds (overrides config).
  --parallel / --sequential   Force parallel or sequential ensemble execution (overrides config).
```

**There is no `--host`, no `--title`, no `--output-format` on negotiate.** Run `lope negotiate --help` if unsure.

### `lope execute <sprint_doc>`

Run sprint phases with validator-in-the-loop retry.

```
Usage: lope execute [-h] [--phase PHASE] [--manual]
                   [--validators VALIDATORS] [--primary PRIMARY]
                   [--timeout TIMEOUT] [--parallel | --sequential]
                   sprint_doc

Positional:
  sprint_doc                  Path to the sprint doc produced by `lope negotiate`.

Flags:
  --phase PHASE               Run only the named phase instead of the full sprint.
  --manual                    Human-in-the-loop mode: wait for Enter between phases.
  --validators VALIDATORS     Comma-separated validator list (overrides config, not persisted).
  --primary PRIMARY           Primary validator name (overrides config, not persisted).
  --timeout TIMEOUT           Per-validator timeout in seconds (overrides config, not persisted).
  --parallel / --sequential   Force parallel or sequential ensemble execution.
```

### `lope audit <sprint_doc>`

Generate a scorecard from execution results.

```
Usage: lope audit [-h] [--no-journal]
                 [--validators VALIDATORS] [--primary PRIMARY]
                 [--timeout TIMEOUT] [--parallel | --sequential]
                 sprint_doc

Flags:
  --no-journal                Skip journal write.
  --validators VALIDATORS     Comma-separated validator list (for future re-runs).
  --primary PRIMARY           Primary validator name.
  --timeout TIMEOUT           Per-validator timeout in seconds.
  --parallel / --sequential   Force parallel or sequential ensemble execution.
```

### `lope status`

Show detected validators on this machine and the active config. Run this first if lope is acting up.

### `lope configure`

Interactive validator picker. Writes to `~/.lope/config.json`.

### `lope install`

Engine-level installer pointer. Prefer the top-level `./install` bash script or the paste-a-prompt flow (see below).

### `lope version`

Prints the version banner.

### `lope docs`

Prints this reference document to stdout. Pipe into `less` or redirect to a file.

---

## Domains

Pass `--domain <name>` on negotiate to switch validator role prompt and artifact labels.

| Domain | For | Artifacts / Files | Success Criteria / Tests |
|---|---|---|---|
| `engineering` (default) | code, software, infra, devops | Files | Tests |
| `business` | marketing campaigns, budgets, ops, consulting, finance, legal | Deliverables | Success Metrics |
| `research` | academic work, systematic reviews, studies, replication | Artifacts | Validation Criteria |

The ensemble checks the same thing across all three domains: specific plan, measurable criteria, complete scope, poke-a-hole review. The role prompt and labels swap to match the domain's vocabulary.

---

## Supported validators

12 built-in CLI adapters, auto-detected on `$PATH`:

Claude Code · OpenCode · Gemini CLI · Codex · Mistral Vibe · Aider · Ollama · Goose · Open Interpreter · llama.cpp · GitHub Copilot CLI · Amazon Q

**You need at least two different validators for the ensemble to have signal.** A pool of one is not an ensemble.

Custom providers via `~/.lope/config.json` — subprocess or HTTP. Schema in the README.

---

## Environment variables

| Var | Effect |
|---|---|
| `LOPE_CAVEMAN` | `full` (default) / `lite` / `off`. Caveman mode token compression on validator prompts. |
| `LOPE_LINT` | `off` to skip no-placeholder lint on drafts. |
| `LOPE_EVIDENCE_GATE` | `off` to skip the PASS-needs-evidence downgrade. |
| `LOPE_SINGLE_STAGE` | `1` to revert execute mode to legacy single-pass validation. |
| `LOPE_HOOK` | `off` to suppress the SessionStart briefing. |
| `LOPE_LLM_URL` | Optional hosted LLM fallback when primary validator can't draft. |
| `LOPE_LLM_API_KEY` | Bearer token for `LOPE_LLM_URL`. |
| `LOPE_WORKDIR` | Working directory for validator subprocesses. |
| `LOPE_TIMEOUT` | Validator timeout in seconds (default 480). |
| **v0.4.0 pool scoping** | |
| `LOPE_VALIDATORS` | Comma-separated validator list, e.g. `opencode,gemini`. Overrides global config without mutating it. |
| `LOPE_PRIMARY` | Primary validator name. Must be in `LOPE_VALIDATORS` (or the global config's list). |
| `LOPE_PARALLEL` | `1`/`true` to force parallel ensemble, `0`/`false` for sequential. |
| `LOPE_SEQUENTIAL` | `1` to force sequential (shortcut for `LOPE_PARALLEL=0`). |
| **v0.4.0 self-heal** | |
| `LOPE_SELF_HEAL` | `1` to opt into adapter self-healing on flag-break detection. Default off in v0.4.0. |
| `LOPE_HOME` | Override `~/.lope` for the global config directory. Useful for sandboxed test runs. |

## Config precedence (v0.4.0)

Lope loads config with a 5-layer precedence chain, highest-wins per field:

1. **Command-line flags** — `--validators opencode,gemini --primary opencode --timeout 240`. Highest precedence. Zero persistence.
2. **Environment variables** — `LOPE_VALIDATORS=opencode,gemini LOPE_PRIMARY=opencode`. Per-shell-session scope. Each terminal sets once, all `lope` calls in that shell inherit without touching any file.
3. **Per-project config** — `./.lope/config.json` in the current working directory. Repo-scoped defaults. Fields not in the project file fall through to layer 4.
4. **User global config** — `~/.lope/config.json`. Written by `lope configure`. Read-only for every other command.
5. **Built-in defaults** — empty validators, 480s timeout, parallel=True. Only visible when the user has never configured lope.

Each layer overrides the previous one **field-by-field**, not whole-object. You can set `LOPE_VALIDATORS` in your shell rc while still inheriting `timeout` and `providers` from the global file. You can have `--validators opencode,gemini` on the command line while env vars set the `primary`.

**Why this matters:** v0.3.x had only one config file. Running two `lope negotiate` invocations from two terminals with different validator pools was impossible — whichever wrote last silently clobbered the other. v0.4.0 makes each terminal/each invocation self-contained: only `lope configure` touches the global file.

## Self-healing adapters (v0.4.0)

Each `Validator` subclass in `lope/validators.py` hardcodes the subprocess invocation for its host CLI (e.g. `claude --print <prompt>`, `opencode run --format json`). When a CLI vendor renames a flag in a future release, lope detects the failure and can automatically repair itself.

**How it works:**

1. A validator subprocess fails with `unrecognized argument`, `unknown option`, or similar flag-surface error in stderr.
2. Lope's `_is_flag_error()` heuristic matches the stderr pattern and the pool attaches a `flag_error_hint` to the validator result.
3. If `LOPE_SELF_HEAL=1` is set, the `SelfHealer` runs `<cli> --help`, asks the primary reviewer in the pool to propose a corrected argv template (JSON object with `argv_template`, `stdin_mode`, `stdout_parser`, `confidence`, `rationale`), and validates the proposal.
4. The healer smoke-tests the proposal with a fixed prompt: *"Reply with the single word OK and nothing else."* If the response contains "OK", the learned adapter is atomically persisted to `~/.lope/config.json` under `learned_adapters.<cli_name>`.
5. Future calls to that CLI use the learned invocation. A 90-day TTL triggers re-verification.

**Opt-in for v0.4.0.** Set `LOPE_SELF_HEAL=1` to enable. Default-off until telemetry confirms low false-positive rate; will flip to default-on in v0.5.0.

**Guardrails:**

- **One heal attempt per CLI per session.** Prevents infinite heal loops.
- **Skipped when no reviewer is available.** If the pool has only one validator (the failing one), heal cannot proceed and lope escalates.
- **Journaled to `~/.lope/journal.jsonl`.** Every `heal_attempt`, `heal_success`, `heal_failure`, and `heal_skipped` event is appended with timestamp, CLI name, old argv, proposed argv, and rationale. Run `lope status` to see recent heal events inline.
- **Smoke-test gated.** Nothing persists until the proposed invocation actually produces "OK" for the smoke prompt.
- **Out of scope for healer:** HTTP providers (different failure modes), custom subprocess providers from `providers` array (handled by their own schema), and entirely new CLIs that don't have a `Validator` subclass yet.

---

## How you invoke lope

There are **two invocation paths**, and the user will probably use #2.

### 1. Explicit slash command (Claude Code, OpenCode, Gemini CLI, Cursor)

```
/lope-negotiate "Add JWT auth with refresh tokens"
/lope-execute SPRINT-JWT-AUTH.md
/lope-audit SPRINT-JWT-AUTH.md
/lope              # umbrella explaining the three modes
/lope-help         # prints this reference into your context
```

Gemini CLI uses namespaced syntax: `/lope:negotiate`, `/lope:execute`, `/lope:audit`, `/lope:help`.

### 2. Natural language (any CLI, including Codex and Vibe)

Most users will describe multi-phase work in prose — *"plan the auth refactor"*, *"scope the data migration"*, *"negotiate the Q4 campaign, needs to be right"*. Your job as the host agent is to **recognize the shape** and invoke lope on the user's behalf by running `lope <mode> <args>` in a shell.

**Trigger words** for the natural-language path: *plan, negotiate, scope, draft, roll out, work through, carefully, don't break things, needs to be right.*

When you recognize one of those + multi-phase work, construct the goal string from the user's prose and run `lope negotiate "<goal>" --domain <engineering|business|research>` directly. Do not wait for the user to type a slash command.

**Do not** trigger on: single-edit tasks (rename a var, fix a typo), pure Q&A ("what does this do?"), trivial operations (add a print statement), urgent fire-fighting.

---

## Per-host support matrix

Different CLIs have different slash-command mechanisms (or lack thereof). This is the honest state as of lope v0.3.2:

| Host | Slash commands | Natural language | Install path |
|---|---|---|---|
| **Claude Code** | ✅ `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`, `/lope-help`, `/using-lope` | ✅ | `~/.claude/skills/lope*/` (symlinks) |
| **Codex** | ❌ does not register `/name` from SKILL.md (confirmed by asking Codex directly) | ✅ — skill content loaded, agent invokes via bash | `~/.codex/skills/lope*/` (content only) |
| **Gemini CLI** | ✅ `/lope:negotiate`, `/lope:execute`, `/lope:audit`, `/lope:help` (namespaced, colon not hyphen) | ✅ | `~/.gemini/commands/lope/*.toml` |
| **OpenCode** | ✅ `/lope-*` | ✅ | `~/.config/opencode/commands/*.md` (PLURAL "commands") |
| **Cursor** | ⚠️ unverified — uses `.cursor/skills/` format; test before relying | ✅ | `.cursor/skills/` (project-local) |
| **Mistral Vibe** | ❌ no user slash commands (confirmed by Vibe directly) | ✅ — skill content loaded, agent invokes via bash | `~/.vibe/skills/lope*/` (content only) |
| **GitHub Copilot CLI** | ❌ no user skill dir yet | ✅ — agent invokes via bash | none |

**Takeaway:** If your CLI is in the ❌ slash-command column, `lope` still works perfectly from a terminal and the agent still knows about it. Just describe your task in prose and the agent will run `lope <mode> <args>` for you. Do not wait for an autocomplete that won't come.

---

## Two-stage validator review (v0.3.0+)

Each execute phase gets validated twice per retry attempt:

1. **Spec compliance pass** — *"does this output match the phase goal?"*
   - Spec NEEDS_FIX → short-circuits the quality pass, retries with fix instructions
   - Spec FAIL → escalates immediately
2. **Code quality pass** — *"is this well-built?"*
   - Only runs if spec PASS
   - NEEDS_FIX or FAIL feed back into the retry loop

Separates "clever slop that misses the requirement" from "meets spec but rough around the edges." Disable by setting `LOPE_SINGLE_STAGE=1`.

---

## Verification-before-completion evidence gate (v0.3.0+)

Any validator that returns PASS with a rationale that lacks **evidence** — no `file:line` reference, no test output, no code fence, no explicit verification phrase ("tests passed", "verified", etc.) — gets auto-downgraded to NEEDS_FIX with a synthesized "provide evidence" fix instruction.

Kills rubber-stamping at the framework level. You don't have to trust validators to be rigorous; lope enforces it structurally. Disable by setting `LOPE_EVIDENCE_GATE=off`.

---

## No-placeholder lint on drafts (v0.3.0+)

If the negotiator produces a sprint doc containing any of:

- `TBD`, `TODO`, `XXX`, `FIXME`
- `<placeholder>` or `[insert X]` tokens
- Bare prose ellipsis (`...`) outside code fences
- Phases with empty Artifacts / Files / Deliverables
- Phases with empty Checks / Tests / Success Metrics

…the drafter loops back with specific fix instructions **before** any validator round. Much cheaper than paying validators to say "you forgot to fill in phase 3." Disable with `LOPE_LINT=off`.

---

## Intelligent caveman mode

On by default. Compresses validator prompts by dropping articles, filler, and hedging, while keeping code, paths, line numbers, and error messages **exact**. Roughly 50-65% token savings per validator round in internal measurements.

Adapted from [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) (MIT). Lope's contribution is integrating the rules into the validator prompt injection pipeline.

Modes via `LOPE_CAVEMAN` env var:

- `full` (default) — maximum compression
- `lite` — drops filler and hedging only, keeps full sentences
- `off` — disable entirely (use for external writing / published content)

---

## Install

**Preferred (paste-a-prompt):** Paste one line into any AI agent you already use:

```
Read https://raw.githubusercontent.com/traylinx/lope/main/INSTALL.md and follow the instructions to install lope on this machine natively.
```

Your agent fetches `INSTALL.md`, identifies which CLI it's running inside, and follows the matching section. Auto-detects the host, writes skills/commands to that host's native directory in the format that host expects.

**Manual:** Clone and run the bash installer.

```bash
git clone --depth 1 https://github.com/traylinx/lope.git ~/.lope
~/.lope/install
alias lope='PYTHONPATH=~/.lope python3 -m lope'
```

**Restart your CLI after install.** Every host caches its skill list at session start — freshly-installed commands won't appear until you quit and reopen the CLI.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/lope*` doesn't autocomplete after install | Host caches skill list at session start | Quit and reopen the CLI |
| `/lope*` doesn't autocomplete after restart in Claude Code | Skills were installed to the wrong path | Check `ls ~/.claude/skills/ \| grep lope` — should list 6 lope* dirs |
| `/lope*` doesn't appear in Vibe or Codex | Vibe/Codex don't support user slash commands (by design) | Invoke via natural language: *"use lope to negotiate the auth refactor"* |
| `lope status` shows 0 detected CLIs | No AI CLIs on `$PATH` | Install at least 2 of the 12 supported CLIs |
| `lope negotiate` crashes with a traceback | Engine bug | Capture the full traceback and open an issue — do NOT patch lope source as the fix |
| `LOPE_LLM_URL` returns 401 | `LOPE_LLM_API_KEY` not set | `export LOPE_LLM_API_KEY=sk-...` |
| Negotiate escalates on round 1 | Validator pool disagreement, or lint caught a placeholder | Read the escalation message — it names the issue |

---

## Hard rules for agents invoking lope

1. **Do not invent flags.** `lope negotiate` flags are exactly: `--out`, `--max-rounds`, `--context`, `--domain`, `--validators`, `--primary`, `--timeout`, `--parallel`, `--sequential`. No `--host`, no `--title`, no `--output-format`. Run `lope <mode> --help` if unsure.

2. **Do not write a wrapper script around lope.** Lope is already a CLI. Never create `lope_runner.py`, `generate_with_lope.sh`, or any Python/bash scaffold that imports or wraps lope. Invoke `lope <mode> <args>` directly in a shell.

3. **Do not commit lope state to the user's project git repo** unless they explicitly ask.

4. **Do not trigger lope on single-edit tasks, typo fixes, trivial edits, or pure Q&A.** Lope is for multi-phase work with a plan and success criteria.

5. **For external writing** (emails, board memos, published content), set `LOPE_CAVEMAN=off` before running so validator prose stays polished. Default `full` mode is for internal terse work.

---

## Where to read more

- `lope --help` / `lope <mode> --help` — authoritative flag surface
- `lope docs` — this document
- `/lope-help` — this document, injected as a slash command
- `~/.lope/docs/samples.md` — 8 end-to-end conversation walkthroughs across all 3 domains
- `~/.lope/README.md` — marketing/overview version
- `~/.lope/CHANGELOG.md` — release notes
- https://github.com/traylinx/lope — source of truth

Built by Sebastian Schkudlara (Traylinx). MIT licensed. Caveman mode core rules adapted from JuliusBrussee/caveman.
