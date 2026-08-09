# Lope — Complete Reference

This is the authoritative reference for lope. It's the single source of truth read by:

- `lope docs` subcommand (prints this file)
- `/lope-help` slash command (skills/lope-help/SKILL.md — delegates to `lope docs`)
- `/lope:help` slash command (Gemini CLI, via commands/lope/help.toml)

If you are an AI agent reading this because the user asked about lope, load it into your context and answer from it. Do not read other lope source files unless this doc points you to them.

---

## What lope is

Lope is a **multi-CLI validator ensemble for AI work**. One AI CLI drafts; others validate. Works for multi-phase sprints (negotiate → implement/execute → audit with validator-in-the-loop retry) **and** for single-shot multi-model tasks (ask, review, vote, compare, pipe). No single-model blindspot.

Lope is primarily a CLI harness that runs **other** AI CLIs as validators. You invoke `lope <verb> <args>` from a shell and lope orchestrates subprocess calls to Claude Code, OpenCode, Gemini CLI, Codex, pi, Qwen, Agy, and custom HTTP/subprocess teammates. The parallel fan-out primitive (`EnsemblePool`) is also importable as a library — see [Library usage](#library-usage).

Works for **three domains**: `engineering` (default), `business`, `research`. Same loop, different validator role prompt and artifact labels. The domain knob applies to `negotiate`; single-shot verbs are domain-agnostic.

Repo: https://github.com/traylinx/lope · MIT · Zero Python dependencies (pure stdlib).

---

## The modes

Command surface: structured sprint modes (`negotiate`, `execute`, `implement`, `audit`), single-shot verbs (`ask`, `review`, `vote`, `compare`, `pipe`), roster management (`team`), objective evidence gates (`gate`, `check`), persistent judgment (`memory`, `deliberate`), autonomous graph workflows (`flow`), and maintenance (`update` / `upgrade`). Pick the mode that fits the shape of the work — don't force everything through `negotiate`.

| Mode | CLI | Slash command (where supported) | What it does |
|---|---|---|---|
| **Negotiate** | `lope negotiate <goal>` | `/lope-negotiate` | Primary CLI drafts a structured sprint doc. Other CLIs independently review. Majority vote. Iterates until consensus or escalation. Writes the sprint doc to disk. |
| **Execute** | `lope execute <sprint_doc>` | `/lope-execute` | Runs the sprint phase by phase. Each phase: primary implements, then two-stage validator review (spec compliance, then code quality). NEEDS_FIX retries with fix instructions (3 attempts). PASS advances. FAIL escalates. |
| **Implement** | `lope implement <sprint_doc>` | `/lope-implement` | High-level zero-human sprint execution. First selects implementation agents and escalation agents, then runs phases without asking the human again. v1 uses one writing lead for checkout safety while the selected team drives validation/escalation context. |
| **Audit** | `lope audit <sprint_doc>` | `/lope-audit` | Generates a scorecard from executed sprint results — per-phase verdicts, confidence scores, duration, overall status. Appends to lope's journal. |
| **Ask** | `lope ask "<question>"` | `/lope-ask` | Fan out one question to every validator; collect N raw answers (one per model). No VERDICT parsing, no phase retry. |
| **Review** | `lope review <file>` | `/lope-review` | Send a file + optional `--focus` to every validator; collect N critiques. With `--consensus`, merges, dedupes, and ranks findings; supports `--format text\|json\|markdown\|markdown-pr\|sarif`. |
| **Vote** | `lope vote "<q>" --options A,B,C` | `/lope-vote` | Each validator picks exactly one option label. Tally + winner. Whole-token strict parsing. |
| **Compare** | `lope compare <a> <b>` | `/lope-compare` | Each validator picks between two files against explicit `--criteria`. Tally + winner. |
| **Pipe** | `<cmd> \| lope pipe` | `/lope-pipe` | Read stdin as the prompt; fan out; per-validator sections. Default per-validator isolation; `--require-all` for strict. |
| **Team** | `lope team {list,enable,disable,add,remove,test}` | `/lope-team` | Manage the validator roster — enable built-ins, add local CLI binaries or OpenAI-compatible HTTP endpoints, disable/drop teammates, smoke-test keys/URLs/binaries. No JSON editing. |
| **Gate** | `lope gate {save,check}` | — | Run project-defined objective evidence gates, save a baseline, and compare later runs for regressions. |
| **Check** | `lope check` | — | CI-friendly one-shot run of project-defined objective evidence gates. |
| **Memory** | `lope memory {stats,search,file,hotspots,forget}` | — | Query the persistent finding store written by `lope review --remember`. See [docs/memory.md](memory.md). |
| **Deliberate** | `lope deliberate <template> <scenario>` | — | Run a 7-stage Agent-Order-style council on an ADR / PRD / RFC / build-vs-buy / migration-plan / incident-review. See [docs/deliberation.md](deliberation.md). |
| **Flow** *(v0.11)* | `lope flow {run,validate,render,init,list}` | `/lope-flow` | Run a declarative DOT **graph** workflow — agent / ensemble-review / shell-gate / judge-router nodes, conditioned edges, fan-out + fix-loops. Autonomous (no human gates), bounded by per-node and graph-wide visit caps. See [Flow](#flow--declarative-graph-workflows). |
| **Update** | `lope update` (`lope upgrade` alias) | — | Self-update Lope. Git installs pull with `--ff-only` and refresh host skills. Pip mode exists for future package installs, but the supported server path today is the `~/.lope` git checkout until PyPI Trusted Publisher is configured. |
| **Headroom** | — | `/lope-headroom` | Configure, install, verify, or troubleshoot Headroom MCP compression for Lope and Lope-installed agent hosts. |
| **Help** | `lope docs` | `/lope-help` | Print the complete reference containing all modes, flags, and hard rules. |

Default flow for multi-phase work: **negotiate → implement/execute → audit**. For single-prompt / single-file / piped work, the single-shot verbs run in one pass without a sprint doc. `team` is runtime-independent — it only edits `~/.lope/config.json` and runs 0 validators (except on `test`).

### Cross-cutting flags

These flags layer on top of the existing modes. They are **opt-in** — commands keep their simple default behavior unless one is passed.

| Flag | Available on | Effect |
|---|---|---|
| `--consensus` / `--structured` | `review` | Merge, dedupe, and consensus-rank findings across validators. Drives the structured renderer. |
| `--format text\|json\|markdown\|markdown-pr\|sarif` | `review` | Pick the consensus output shape. SARIF is v2.1.0 and uploads cleanly to GitHub code-scanning. |
| `--include-raw` | `review` | Append per-validator raw responses under the consensus block (collapsible `<details>` in PR mode). |
| `--similarity FLOAT` / `--min-consensus FLOAT` | `review` | Tune dedup threshold and minimum consensus_score filter. |
| `--remember` | `review` | Persist consensus findings to the local SQLite memory. |
| `--divide files\|hunks` | `review` | Walk a directory or split a unified diff before review. Mutually exclusive with `--roles`. |
| `--roles security,performance,tests,...` | `review` | Round-robin role lenses across validators (8 built-in lenses + 13 aliases). |
| `--synth` | `ask`, `review`, `pipe`, `vote`, `compare` | Roll N answers (or merged findings) into one executive-summary synthesis. |
| `--anonymous` | `ask`, `review`, `pipe`, `vote`, `compare` | Strip validator names from the synthesis prompt (`Response A/B/C` labels). |
| `--brain-context QUERY` | `ask`, `review`, `pipe`, `negotiate`, `deliberate` | Pull `makakoo search QUERY` and prepend to the validator prompts. |
| `--brain-budget N` | same as above | Approximate token budget for brain context (default 1200). |
| `--brain-log` | same as above | Append a `[[Lope]]` / `[[Makakoo OS]]` bullet to today's Brain journal. |

---

## Flow — declarative graph workflows

`lope flow` (v0.11) runs a workflow defined as a **Graphviz DOT graph**. Where `negotiate`/`execute`/`implement` are fixed linear pipelines, `flow` lets you *draw the topology* — fan out to N proposers, consolidate, implement, fan out reviewers, loop on failure — and version-control it. Every node dispatches into lope's existing executors, so there is **no new agent runtime**: any CLI implements, the ensemble votes.

It is built for **autonomous, no-human runs**: human gates are optional, and every loop is bounded so an unsupervised run can never spin forever.

### Subcommands

```bash
lope flow init <consensus|judge-loop|review-gate> [--out PATH]   # write a starter graph
lope flow validate <file.dot>                                    # alias: lint
lope flow render <file.dot> [-o out.svg] [-T svg|png|dot]        # needs system graphviz
lope flow run <file.dot> --task "<goal>" [pool flags] [--out DIR]
                         [--max-node-visits N] [--dry-run] [--no-journal] [--cwd DIR]
lope flow list                                                   # bundled templates
```

`run` accepts the same pool flags as the other modes (`--validators`, `--primary`, `--timeout`, `--parallel`/`--sequential`). `--task` is substituted for `$task` in the graph. `--out` writes a redacted `trace.jsonl` + `report.md`. Flow runs are scored by the `Auditor` and appended to the `[[lope]]` journal (skip with `--no-journal`).

### Node types

| `type=` | Runs | Outcome token(s) |
|---|---|---|
| `start` / `exit` | entry / terminal (`exit` may set `status="fail"`) | `started` / `exited` |
| `agent` | `Validator.generate()` — single-writer implementer turn | `succeeded` / `failed` |
| `review` | `EnsemblePool.validate()` — majority vote | `succeeded` / `needs_fix` / `failed` |
| `judge` | router: ensemble vote (`mode="ensemble"`) or `generate` + `outcome:` block (`mode="generate"`, declare `outcomes="a,b"`) | a declared label |
| `script` | `gates.run_gate()` — inline `cmd="..."` or named `gate="..."` from `.lope/rules.json` | `succeeded` / `failed` |
| `gate` | optional human approval pause (omit for autonomous runs) | `succeeded` / `failed` |

Node shapes also imply kinds for pasted fabro graphs (`Mdiamond`=start, `Msquare`=exit, `parallelogram`=script, `hexagon`=gate). Aliases `implement→agent`, `ensemble→review`, `consensus/steer→judge`, `verify→script` are accepted.

### Edges and routing

Edges carry `condition="outcome=<token>"` (or `outcome!=<token>`), an optional `label`, and `loop_restart="true"` for back-edges. A node's outcome selects **all** out-edges whose condition matches — so a judge's single decision (`outcome=ok`) can fan out to N proposers. Mark a fan-in node `join="true"` so it barriers until all its non-loop predecessors complete.

### Bounded autonomy

`max_visits` per node (default 3) and graph-level `max_node_visits` (default `max(50, 8*nodes)`) are enforced **before** each node runs. A non-converging loop terminates with an `EscalationRequired` recorded in the report — never an infinite loop or unbounded cost. `lope flow validate` rejects any cycle with no visit bound.

### `cli_stylesheet`

lope's analog of fabro's `model_stylesheet`, routing a node's class/id → which CLI plays the role:

```
cli_stylesheet="
  *          { primary: opencode; }
  .frontier  { primary: claude; }
  #Reviewers { validators: claude,codex,gemini; }
"
```

Cascade lowest→highest: global config → `*` → `.class` → `#id` → inline node attrs. `model_stylesheet` is accepted as an alias.

### Example

```bash
lope flow init consensus
lope flow validate .lope/flow/consensus.dot
lope flow render   .lope/flow/consensus.dot -o consensus.svg
lope flow run      .lope/flow/consensus.dot --task "Add a /health endpoint with a test"
```

The `consensus` template is fully autonomous: `Start → CheckDoD → 3 proposers (parallel) → Consolidate → Implement → ensemble Review → pass:Exit / fail:Postmortem → replan loops back`, with no human gates and every loop bounded.

---

## CLI reference

### `lope negotiate <goal>`

Draft a sprint doc via multi-round validator review.

```
Usage: lope negotiate [-h] [--out OUT] [--max-rounds MAX_ROUNDS]
                     [--context CONTEXT] [--context-file CONTEXT_FILE]
                     [--domain {engineering,business,research}]
                     [--validators VALIDATORS] [--primary PRIMARY]
                     [--timeout TIMEOUT] [--parallel | --sequential]
                     goal

Positional:
  goal                        Sprint goal description (one sentence to one paragraph).

Flags:
  --out OUT                   Output path for sprint doc (default: ./SPRINT-<slug>.md).
  --max-rounds MAX_ROUNDS     Max negotiation rounds before escalation (default: 3).
  --context CONTEXT           Additional inline context string.
  --context-file CONTEXT_FILE
                              Read large context from a file; can be combined with --context.
  --domain DOMAIN             engineering (default) / business / research.
  --validators VALIDATORS     Comma-separated validator list, e.g. opencode,gemini (overrides config).
  --primary PRIMARY           Primary validator name (must be in --validators or global config).
  --timeout TIMEOUT           Per-validator timeout in seconds (overrides config).
  --parallel / --sequential   Force all validators to run in parallel or one-by-one, then synthesize the ensemble vote.
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

### `lope implement <sprint_doc>`

Run a sprint with zero-human swarm orchestration. `implement` is a higher-level wrapper around the same phase executor used by `execute`: it still validates every phase with Lope, but it adds an explicit roster-selection step and a stricter no-human prompt contract.

```
Usage: lope implement [-h] [--agents AGENTS] [--escalate-to ESCALATE_TO]
                      [--phase PHASE] [--gates] [--gate-config GATE_CONFIG]
                      [--dry-run] [--interactive]
                      [--validators VALIDATORS] [--primary PRIMARY]
                      [--timeout TIMEOUT] [--parallel | --sequential]
                      sprint_doc

Positional:
  sprint_doc                  Path to the sprint doc produced by `lope negotiate`.

Flags:
  --agents AGENTS             Comma-separated implementation agents. The first agent is the writing lead. Required in non-interactive mode.
  --escalate-to ESCALATE_TO   Comma-separated escalation/review agents. Required in non-interactive mode.
  --phase PHASE               Run only one phase.
  --gates                     Run objective evidence gates after each implementation attempt.
  --gate-config PATH          Path to `.lope/rules.json` gate config.
  --dry-run                   Resolve and print the roster without running any agent.
  --interactive               Force the roster picker even when stdin/stdout is not a TTY.
```

Interactive TTY flow asks for implementation agents first, then escalation agents. After that, Lope must not ask the human again. Non-interactive runs must pass both `--agents` and `--escalate-to` so CI and host agents are deterministic.

Engineering `execute` and `implement` runs include the minimality discipline in audit mode by default: implementation prompts prefer existing code, stdlib/native features, and the smallest safe custom code; quality-stage validators flag material over-engineering without failing spec-compliant work for style alone. Disable with `LOPE_MINIMALITY=off`; make material bloat blocking with `LOPE_MINIMALITY=enforce`. Business and research domains stay off unless explicitly enabled.

Examples:

```bash
lope implement SPRINT.md
lope implement SPRINT.md --agents pi,antigravity --escalate-to claude,opencode
lope implement SPRINT.md --agents pi --escalate-to claude,opencode --phase 2 --gates
lope implement SPRINT.md --agents pi --escalate-to claude,opencode --dry-run
```

Safety model: v1 is a **single-writer swarm**. It does not ask multiple CLIs to edit the same checkout concurrently because that creates patch races. The first `--agents` entry writes. The selected implementation/escalation roster is injected into the prompt and validator pool. Literal parallel implementation belongs in a future worktree-backed mode.

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

### `lope ask "<question>"`

Fan out one question to every configured validator; print raw answers in `━━━ <name> ━━━` sections. No VERDICT block, no phase retry, no majority vote.

```
Usage: lope ask [-h] [--json] [--context CONTEXT]
                [--validators VALIDATORS] [--primary PRIMARY]
                [--timeout TIMEOUT] [--parallel | --sequential]
                question

Positional:
  question                    The question to fan out (quoted).

Flags:
  --context CONTEXT           Optional context prepended to every validator's prompt.
  --json                      Emit JSON `[{"validator": ..., "answer": ..., "error": ...}]`.
  --validators VALIDATORS     Comma-separated validator list (overrides config).
  --primary PRIMARY           Not used by ask, accepted for symmetry with the other verbs.
  --timeout TIMEOUT           Per-validator timeout in seconds.
```

Errors are per-validator: one timeout shows `[ERROR]` in that section and the rest continue. Exit code 0 unless no validators were available.

### `lope review <file>`

Send a file's content to every validator with a review prompt.

```
Usage: lope review [-h] [--focus FOCUS] [--json]
                  [--validators VALIDATORS] [--primary PRIMARY]
                  [--timeout TIMEOUT] [--parallel | --sequential]
                  file

Positional:
  file                        Path to the file to review (plain text only).

Flags:
  --focus FOCUS               Focus area — 'security', 'perf', 'tests', etc.
                              Aliases 'over-engineering', 'minimality', and
                              'lazy-build' expand to the minimality rubric.
                              Default: "bugs, code-smells, design issues,
                              improvements with line references".
  --json                      Emit JSON `[{"validator": ..., "review": ..., "error": ...}]`.
```

The full file content is embedded in every validator's prompt — large files multiply tokens. Binary, PDF, and image files are not supported (use `harvey_describe_*` tools for those).

### `lope vote "<prompt>" --options A,B,C`

Each validator picks exactly one of the provided options. Tally + winner.

```
Usage: lope vote [-h] --options OPTIONS [--json] [--context CONTEXT]
                 [--validators VALIDATORS] [--primary PRIMARY]
                 [--timeout TIMEOUT] [--parallel | --sequential]
                 prompt

Positional:
  prompt                      The question / proposal to vote on.

Flags:
  --options OPTIONS           REQUIRED. Comma-separated option labels.
                              Min 2. Must be unique (case-insensitive).
  --context CONTEXT           Optional context prepended to the prompt.
  --json                      Emit JSON with per-voter picks + tally.
```

**Option drift prevention.** Every validator sees the IDENTICAL option list inside one prompt block. Reply shape is pinned ("reply with ONLY the label"). Parsing is whole-token strict (`A` won't match inside `ALGORITHM`). Longest-first resolution for overlapping labels (`3.13` beats `3.1`).

**Tie handling.** If two or more options tie for most votes, output says "No winner — tie" and the user decides. `[UNPARSEABLE]` entries (replies that didn't match any label) and `[ERROR]` entries do not count toward the tally.

### `lope compare <file_a> <file_b>`

Each validator picks which of two files is better against explicit criteria.

```
Usage: lope compare [-h] [--criteria CRITERIA] [--json]
                    [--validators VALIDATORS] [--primary PRIMARY]
                    [--timeout TIMEOUT] [--parallel | --sequential]
                    file_a file_b

Positional:
  file_a                      First file path (labelled 'A' in voting).
  file_b                      Second file path (labelled 'B').

Flags:
  --criteria CRITERIA         Comma-separated evaluation dimensions.
                              Default: "correctness and clarity".
  --json                      Emit JSON with per-voter picks + tally.
```

**Criteria opacity fix.** `--criteria` is injected into every validator's prompt explicitly so "better" is bound to real dimensions, never model-invented. Always pass it if the user had specific dimensions in mind (security, performance, ergonomics, readability…).

**File size caveat.** Both files ride inline in the prompt. Large files multiply tokens and can exceed context windows — affected validators surface as `[UNPARSEABLE]`, the rest still vote.

### `lope pipe`

Read stdin as the prompt; fan out to every validator; print per-validator sections. The composable shell verb.

```
Usage: lope pipe [-h] [--require-all] [--json]
                 [--validators VALIDATORS] [--primary PRIMARY]
                 [--timeout TIMEOUT] [--parallel | --sequential]

Flags:
  --require-all               Exit non-zero if ANY validator errors.
                              Default: fire-and-forget (exit 0, errors in sections).
  --json                      Emit JSON instead of human sections.
```

**Partial-failure semantics.** The default is per-validator isolation — one timeout does not kill the others; `[ERROR]` appears only in that validator's section. `--require-all` opts in to strict exit-non-zero for CI pipelines or workflows where you need all-N assurance.

**Stdin validation.** If stdin is a TTY (no pipe), lope pipe exits 2 with a usage hint. If stdin is empty, exits 2.


### `lope gate {save,check}` and `lope check`

Objective evidence gates are user-authored project commands. Lope does not analyze code itself; it runs deterministic checks (tests, lint, coverage, build, custom JSON score scripts), stores a baseline, compares later runs, and can feed regressions into `lope execute --gates`.

Project config lives at `./.lope/rules.json` by default:

```json
{
  "gates": [
    {"name": "tests", "cmd": "python -m pytest tests -q", "type": "exit", "required": true},
    {"name": "coverage", "cmd": "python -m coverage json -o -", "type": "json_number", "path": "totals.percent_covered", "min_delta": 0.0}
  ]
}
```

```
Usage: lope gate save  [--config PATH] [--baseline PATH] [--timeout SECS] [--json] [--remember] [--trust]
       lope gate check [--config PATH] [--baseline PATH] [--timeout SECS] [--json] [--remember] [--trust]
       lope check      [--config PATH] [--timeout SECS] [--json] [--remember] [--trust]
```

Gate types:

- `exit` — pass when the command exits 0.
- `json_number` — parse stdout as JSON and extract a numeric dot-path (`path`).
- `regex_number` — extract a numeric value from stdout/stderr using the first regex capture.

Thresholds:

- `min_value` / `max_value` constrain the current value.
- `min_delta` constrains `after - before` during `gate check`.
- `max_delta_drop` constrains `before - after` during `gate check`.
- `required: false` records an optional gate without failing the run.

Exit codes: `0` all required gates pass; `1` at least one required gate fails/regresses; `2` config or usage error. `--json` keeps stdout parseable for CI and agentic apps. `--remember` stores the run in Lope memory; `lope memory gates` lists recent gate sessions.

Security note: gate commands are project-authored shell commands and run with the caller's permissions. Treat `.lope/rules.json` like CI configuration — **only run gates from a repository you trust.** Because the commands come from the repo, Lope gates their execution behind an explicit trust decision: the first time a repo's gate commands would run, it lists them and asks for confirmation, remembering the choice per repository and per command-set (changing the commands re-prompts; trust is stored in `$LOPE_HOME/trusted_gates.json`). In a non-interactive session Lope **refuses** to run untrusted gate commands unless you pass `--trust` or set `LOPE_TRUST_GATES=1`. Lope redacts captured output before display/storage, but it does not sandbox the commands.

`lope execute --gates [--gate-config PATH]` saves a baseline before execution, runs gates after each implementation attempt, includes the gate report in the quality-validation prompt, and downgrades a validator PASS to NEEDS_FIX when a required gate regresses. Default `lope execute` behavior is unchanged unless `--gates` is passed.

### `lope team {list,enable,disable,add,remove,test}`

Manage the validator roster from the command line. Every edit is one call; no JSON file editing required. Built-in teammates (`claude`, `codex`, `opencode`, `gemini`, `aider`) are enabled by name; custom teammates use **subprocess** (any local CLI binary) or **HTTP** (any OpenAI-compatible or custom REST endpoint).

```
Usage: lope team {list,enable,disable,add,remove,test} ...
```

**`lope team list`** (default if no subcommand) — show active validators with source tags (`(built-in)` / `(custom subprocess|http)` / `(auto)` / `(?)`), disabled providers, and installed built-ins that are available but inactive.

**`lope team enable NAME...`** — turn on an existing built-in or saved custom teammate without redefining it.

```
lope team enable codex opencode
lope team enable codex opencode --primary codex
```

**`lope team disable NAME...`** — remove validators from the active roster without deleting custom provider config.

```
lope team disable claude
```

**`lope team add NAME`** — upsert a provider and (unless `--disabled`) enable it in the active validators list.

Built-in names are reserved. Do not `team add codex`; run `lope team enable codex`.

```
Usage: lope team add [-h]
                     [--cmd CMD] [--stdin]
                     [--url URL] [--model MODEL]
                     [--key-env KEY_ENV] [--key-header KEY_HEADER] [--key-prefix KEY_PREFIX]
                     [--response-path RESPONSE_PATH] [--body-json BODY_JSON]
                     [--from-curl CURL]
                     [--wrap WRAP] [--timeout TIMEOUT]
                     [--primary] [--disabled] [--force]
                     name

Mode flags (mutually exclusive — pick one):
  --cmd CMD           Subprocess mode. Command string, shlex-split.
                      `{prompt}` is a placeholder (auto-appended as the last
                      argv token if omitted and `--stdin` is off).
  --url URL           HTTP mode. Endpoint URL; must start with http:// or https://.
  --from-curl CURL    HTTP mode, curl-paste flavor. Accepts the entire curl
                      command as a quoted string; auto-extracts URL, headers,
                      body; auto-injects {prompt} into the user-content field
                      (messages[], prompt, input, message, query, text); auto-
                      infers response_path from hostname + headers.

Subprocess-only flag:
  --stdin             Feed the prompt via stdin instead of argv.

HTTP --url flags:
  --model MODEL       Model name used in the default OpenAI-compatible body.
                      Required unless --body-json overrides the body shape.
  --key-header H      Auth header name (default: Authorization).
  --key-prefix P      Token prefix (default: "Bearer ", trailing space included).
                      Use "" for APIs that take a raw key.
  --body-json JSON    Raw JSON body — replaces the OpenAI-compatible default
                      entirely. Use for non-OpenAI APIs.

HTTP shared (--url and --from-curl):
  --key-env VAR       Env var name holding the API key. Stored as ${VAR} —
                      expanded at call time, never written to disk plaintext.
                      With --from-curl, also swaps a literal credential
                      detected in the pasted curl.
  --response-path P   Dot-path to walk into the JSON response for the answer.
                      With --url: defaults to choices.0.message.content.
                      With --from-curl: defaults to an inferred path based on
                      hostname (anthropic → content.0.text, cohere → text,
                      else → choices.0.message.content). Flag wins either way.

Shared:
  --wrap TEMPLATE     Prompt wrapper template (e.g. "Respond tersely: {prompt}").
  --timeout SECS      Per-call timeout override.
  --primary           Make this validator the primary.
  --disabled          Save the provider config but don't add to the validators list.
  --force             Overwrite an existing provider with the same name.
```

**`--from-curl` semantics** (summary; full SKILL.md has the decision tree):

- **Body parsing.** The pasted curl's `-d/--data/--data-raw` value is parsed as JSON. If it's not valid JSON, lope errors and tells the user to use `--body-json` directly. If it parses, `{prompt}` is injected into the user-content field. Preserved shapes: `messages[]` with `{role, content}` (last `role=user` wins; system + assistant messages untouched), top-level `prompt`/`input`/`message`/`query`/`text`.
- **Credential handling.** Headers matching `Authorization`, `X-API-Key`, `api-key`, `x-auth-token`, `x-access-token` are checked for `${VAR}` templating. If a literal value is found, the call refuses unless `--key-env VAR` is passed — in which case the literal (with its optional `Bearer`/`Basic`/`Token` prefix preserved) is rewritten to `${VAR}`. The error message suggests a hostname-derived env name (`api.openai.com` → `OPENAI_API_KEY`, etc.).
- **Unsupported curl forms.** `-u user:pass`, `-F/--form`, `--data-binary @file`, `-X GET`, and malformed headers all refuse with a fix recipe. Ignored (safe): `-s`, `-S`, `--compressed`, `-L`, `-v`, `-i`, `-o <file>`, `--connect-timeout N`, and other common curl ergonomics flags.

**`lope team remove NAME`** — drop the teammate from providers, validators, and primary (primary falls back to the first remaining validator).

**`lope team test NAME [PROMPT]`** — call `generate()` once on the named teammate and print the raw response. Default prompt: `"Say hello in one word."`. `--timeout SECS` overrides (default: 60).

**Safety rules enforced by `_validate_provider_config`:**

- `${VAR}` substitution is accepted in `--body-json` and header values, **rejected** in `--url` and `--cmd` (prevents key leakage to `ps`, shell history, and server logs).
- Subprocess commands always run with `shell=False`.
- Built-in validator names (`claude`, `opencode`, `gemini`, `codex`, `aider`) are refused — pick a different custom name.
- Invalid JSON in `--body-json`, bad env-var names in `--key-env`, and non-http(s) URLs exit with a clear error before touching the config.

**Common recipes:**

```bash
# Paste a curl (the recommended path — zero flag memorization)
lope team add openai --from-curl "curl https://api.openai.com/v1/chat/completions \
  -H 'Authorization: Bearer \${OPENAI_API_KEY}' \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"

# Paste a curl with a literal API key — --key-env swaps it for ${VAR}
lope team add groq --from-curl "curl https://api.groq.com/openai/v1/chat/completions \
  -H 'Authorization: Bearer gsk_RAW1234567890' \
  -d '{\"model\":\"llama-3.3-70b-versatile\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'" \
  --key-env GROQ_API_KEY

# Paste an Anthropic curl (response_path auto-detected as content.0.text)
lope team add anthropic --from-curl "curl https://api.anthropic.com/v1/messages \
  -H 'x-api-key: \${ANTHROPIC_API_KEY}' \
  -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"claude-sonnet-4-5\",\"max_tokens\":4096,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"

# Local Ollama with Qwen3 8B
lope team add my-ollama --cmd "ollama run qwen3:8b {prompt}"

# Local binary that reads from stdin and emits JSON
lope team add hermes --cmd "hermes chat --json" --stdin --timeout 180

# OpenAI-compatible cloud API — flag form (no curl handy)
lope team add groq --url https://api.groq.com/openai/v1/chat/completions \
    --model llama-3.3-70b-versatile --key-env GROQ_API_KEY

# Private Tytus pod running OpenClaw
lope team add openclaw --url http://10.42.42.1:18080/v1/chat/completions \
    --model openclaw --key-env OPENAI_API_KEY

# Anthropic Messages API — flag form with custom shape
lope team add anthropic-raw --url https://api.anthropic.com/v1/messages \
    --key-env ANTHROPIC_API_KEY --key-header "x-api-key" --key-prefix "" \
    --body-json '{"model":"claude-sonnet-4-5","max_tokens":4096,"messages":[{"role":"user","content":"{prompt}"}]}' \
    --response-path "content.0.text"

# Promote an existing teammate to primary (just re-add with --force --primary)
lope team add openclaw --url ... --model openclaw --key-env OPENAI_API_KEY --force --primary
```

### `lope status`

Show detected validators on this machine and the active config. Run this first if lope is acting up.

### `lope configure`

Interactive validator picker. Writes to `~/.lope/config.json`.

### `lope install`

Engine-level installer pointer. Prefer the top-level `./install` bash script or the paste-a-prompt flow (see below).

### `lope update` / `lope upgrade`

Self-update Lope. Default mode auto-detects whether the running copy is a git checkout or a pip install. The supported server path today is the `~/.lope` git checkout; PyPI publishing is not live until Trusted Publisher is configured.

```bash
lope update
lope update --dry-run
lope update --host codex       # update code, then refresh only Codex skills
lope update --skip-install     # pull code only
lope upgrade                   # legacy alias
```

For a git checkout, Lope refuses tracked dirty files unless `--allow-dirty` is passed, validates the requested host before mutating the checkout, then runs `git fetch --tags <remote>` and `git pull --ff-only <remote> <branch>`. Unless `--skip-install` is passed, it then runs `./install --host <host>`. Untracked runtime state such as `~/.lope/config.json`, journals, and memory databases does not block the update. Pip mode exists for future package installs, but do not use it as the current server update path until the package publishing workflow is configured.

`--host` scopes the installer run after the pull. For install-only host refresh without code update, run `~/.lope/install --host <host>` directly.

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

15 built-in CLI adapters, auto-detected on `$PATH`:

Claude Code · OpenCode · Gemini CLI · Codex · Mistral Vibe · Aider · Ollama · Goose · Open Interpreter · llama.cpp · GitHub Copilot CLI · Amazon Q · **pi (Traylinx)** · **Qwen Code** · **Agy**

pi and Qwen were added in v0.5.0, and **Agy** (a multi-model agent CLI — Gemini / Claude / GPT-OSS) in v0.11.0, as first-class built-in validators via the generic subprocess path (`pi -p "{prompt}"`, `qwen -p "{prompt}"`, `agy -p "{prompt}"`). They appear in `lope status` automatically when the binary is on PATH — no config.json hack needed. Use any of them as an ensemble validator or a `flow` node, e.g. `--validators codex,agy`.

**You need at least two different validators for the ensemble to have signal.** A pool of one is not an ensemble. For `ask` / `review` / `vote` / `compare` / `pipe` a single validator still works (fan-out of 1), but the whole point is multi-model perspective.

Custom providers via `~/.lope/config.json` — subprocess or HTTP. Schema in the README.

---

## Environment variables

| Var | Effect |
|---|---|
| `LOPE_CAVEMAN` | `full` (default) / `lite` / `off`. Caveman mode token compression on validator prompts. |
| `LOPE_MINIMALITY` | `audit` by default for engineering `execute`/`implement`; non-engineering default `off`. Set `off` to disable, `enforce` to let validators NEEDS_FIX material bloat with a concrete safer replacement. |
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
| **v0.16.0 budget awareness** | |
| `LOPE_LATENCY` | `off` to disable the per-validator latency ledger entirely — no recording, no pre-launch budget advice. |
| `LOPE_RESPECT_PROVIDER_TIMEOUT` | `1` to let a provider's configured `timeout` outrank `--timeout`. Same effect as `--respect-provider-timeout`. |

## Budget awareness (v0.16.0)

Lope records how long each validator actually takes and uses that history to
tell you, **before** a run starts, when the budget you asked for cannot work.

The ledger lives at `$LOPE_HOME/latency.json` and holds a bounded window of
recent call durations per validator. Timed-out calls are kept as censored
lower bounds — a call killed at 240s proves the real duration exceeds 240s —
so estimates are biased upward, which is the safe direction for a budget.

Before launch the request plan reports two distinct problems:

- **clamp** — the call ceiling is silently cutting a provider's own configured
  timeout. Previously invisible: `--timeout 240` against a provider configured
  for 600s enforced 240 and said nothing.
- **misfit** — the enforced ceiling is below the validator's observed p90 plus
  variance room, so the call is predicted to be killed at the wall.

```text
Request plan: direct · 22 bytes · 1 chunk(s) · 1 call(s) · nominal ceiling 15s
  budget advice: claude-safe-review ceiling 15s; clamps its configured 600s
  (pass --respect-provider-timeout to honour the config); is below observed
  p90 13s over 3 call(s) — predicted timeout; raise --timeout to >= 20s
```

The advice **prints, it does not block** — a first-ever call has no history and
must still be allowed to run. Everything is advisory and failure-contained: a
corrupt or unwritable ledger degrades to "no advice", never to a failed run.

### Timeout precedence

When both a provider config timeout and a call timeout are present, **the
stricter one wins by default.** This is deliberate: provider timeouts exist as
shorter safety caps, and a bounded probe like `team test --timeout 10` must
stay bounded to 10s.

`--respect-provider-timeout` inverts that for one invocation, for the opposite
case — a provider configured slow *on purpose* (a high-reasoning-effort model)
being clamped by a generic ceiling somebody guessed.

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
/lope-implement SPRINT-JWT-AUTH.md
/lope-audit SPRINT-JWT-AUTH.md
/lope              # umbrella explaining the sprint modes
/lope-help         # prints this reference into your context
```

Gemini CLI uses namespaced syntax: `/lope:negotiate`, `/lope:execute`, `/lope:implement`, `/lope:audit`, `/lope:ask`, `/lope:review`, `/lope:vote`, `/lope:compare`, `/lope:pipe`, `/lope:team`, `/lope:flow`, `/lope:memory`, `/lope:deliberate`, `/lope:headroom`, `/lope:help`.

### 2. Natural language (any CLI, including Codex and Vibe)

Most users will describe multi-phase work in prose — *"plan the auth refactor"*, *"scope the data migration"*, *"negotiate the Q4 campaign, needs to be right"*. Your job as the host agent is to **recognize the shape** and invoke lope on the user's behalf by running `lope <mode> <args>` in a shell.

**Trigger words** for the natural-language path: *plan, negotiate, scope, draft, roll out, work through, carefully, don't break things, needs to be right.*

When you recognize one of those + multi-phase work, construct the goal string from the user's prose and run `lope negotiate "<goal>" --domain <engineering|business|research>` directly. Do not wait for the user to type a slash command.

**Do not** trigger on: single-edit tasks (rename a var, fix a typo), pure Q&A ("what does this do?"), trivial operations (add a print statement), urgent fire-fighting.

---

## Per-host support matrix

Different CLIs have different slash-command mechanisms (or lack thereof). Current installer support:

| Host | Slash commands | Natural language | Install path |
|---|---|---|---|
| **Claude Code** | ✅ full `/lope-*` skill set plus `/lope` and `/using-lope` | ✅ | `~/.claude/skills/lope*/` |
| **Codex** | Content skills, not guaranteed native slash commands | ✅ — skill content loaded, agent invokes via bash | `~/.codex/skills/lope*/` |
| **Gemini CLI** | ✅ `/lope:<verb>` for negotiate, execute, implement, audit, ask, review, vote, compare, pipe, team, flow, memory, deliberate, headroom, help | ✅ | `~/.gemini/commands/lope/*.toml` |
| **OpenCode** | ✅ full `/lope-*` command set plus `/lope` and `/using-lope` | ✅ | `~/.config/opencode/commands/*.md` (plural) |
| **Cursor** | ⚠️ unverified slash UX; skills install as agent files | ✅ | `~/.cursor/agents/lope*.md` |
| **Mistral Vibe** | Content skills, not native slash commands | ✅ — skill content loaded, agent invokes via bash | `~/.vibe/skills/lope*/` |
| **Qwen Code** | ✅ standard skill dirs, same Lope skill set as Claude | ✅ | `~/.qwen/skills/lope*/` |
| **pi (Traylinx)** | ✅ standard skill dirs in the shared agents tree | ✅ | `~/.agents/skills/lope*/` |

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

Separates "clever slop that misses the requirement" from "meets spec but rough around the edges." Engineering quality passes also include minimality audit by default; spec passes and objective gates do not. Disable two-stage review by setting `LOPE_SINGLE_STAGE=1`.

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
if [ -d "$HOME/.lope/.git" ]; then
  git -C "$HOME/.lope" fetch --tags origin
  git -C "$HOME/.lope" pull --ff-only origin main
elif [ -e "$HOME/.lope" ]; then
  echo "$HOME/.lope exists but is not a Lope git checkout. Move it aside manually, then rerun install." >&2
  exit 1
else
  git clone https://github.com/traylinx/lope.git "$HOME/.lope"
fi
"$HOME/.lope/install"
alias lope='PYTHONPATH=$HOME/.lope python3 -m lope'
```

If the installed copy is older than v0.12.0 and does not know `lope update`, use this git pull block once, then use `lope update` for future updates.

**Restart your CLI after install.** Every host caches its skill list at session start — freshly-installed commands won't appear until you quit and reopen the CLI.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/lope*` doesn't autocomplete after install | Host caches skill list at session start | Quit and reopen the CLI |
| `/lope*` doesn't autocomplete after restart in Claude Code | Skills were installed to the wrong path | Check `ls ~/.claude/skills/ \| grep lope` — should list `lope`, `using-lope`, and the current `lope-*` skill dirs |
| `/lope*` doesn't appear in Vibe or Codex | Vibe/Codex don't support user slash commands (by design) | Invoke via natural language: *"use lope to negotiate the auth refactor"* |
| `lope status` shows 0 detected CLIs | No AI CLIs on `$PATH` | Install at least 2 of the 15 supported CLIs |
| `lope negotiate` crashes with a traceback | Engine bug | Capture the full traceback and open an issue — do NOT patch lope source as the fix |
| `LOPE_LLM_URL` returns 401 | `LOPE_LLM_API_KEY` not set | `export LOPE_LLM_API_KEY=sk-...` |
| Negotiate escalates on round 1 | Validator pool disagreement, or lint caught a placeholder | Read the escalation message — it names the issue |

---

## Hard rules for agents invoking lope

1. **Do not invent flags.** Run `lope <verb> --help` if unsure. Current flag families:
   - Shared pool flags: `--validators`, `--primary`, `--timeout`, `--parallel`, `--sequential`
   - Brain flags where supported: `--brain-context`, `--brain-budget`, `--brain-log`
   - Sprint flags: `negotiate --out/--max-rounds/--context/--context-file/--domain`; `execute --phase/--manual/--gates/--gate-config/--trust`; `implement --agents/--escalate-to/--phase/--gates/--gate-config/--trust/--dry-run/--interactive`; `audit --no-journal`
   - Single-shot flags: `ask --context/--json/--synth/--anonymous`; `review --focus/--json/--consensus/--structured/--min-consensus/--similarity/--format/--include-raw/--remember/--divide/--roles/--synth/--anonymous`; `vote --options/--json/--synth/--anonymous`; `compare --criteria/--json/--synth/--anonymous`; `pipe --require-all/--json/--synth/--anonymous`
   - Maintenance and gates: `gate save/check`, `check --config/--timeout/--json/--remember/--trust`, `update --dry-run/--method/--host/--skip-install/--allow-dirty`

2. **Do not write a wrapper script around lope.** Lope is already a CLI. Never create `lope_runner.py`, `generate_with_lope.sh`, or any Python/bash scaffold that imports or wraps lope. Invoke `lope <verb> <args>` directly in a shell. The one exception: legitimate library use of `EnsemblePool` (see [Library usage](#library-usage)).

3. **Do not commit lope state to the user's project git repo** unless they explicitly ask.

4. **Pick the right verb.** Don't force everything into `negotiate`. Questions go to `ask`; file critiques go to `review`; choices-from-a-list go to `vote`; A/B file comparisons go to `compare`; stdin-fed fan-out goes to `pipe`. `negotiate/implement/execute/audit` is only the right shape for genuinely multi-phase planned work.

5. **For external writing** (emails, board memos, published content), set `LOPE_CAVEMAN=off` before running so validator prose stays polished. Default `full` mode is for internal terse work.

---

## Library usage

As of v0.5.0, the parallel fan-out primitive is importable as a Python library:

```python
from lope.ensemble import EnsemblePool, synthesize
from lope.validators import build_validator_pool
from lope.config import load_layered

cfg = load_layered()
pool = build_validator_pool(cfg)  # returns EnsemblePool or ValidatorPool

# Parallel ensemble — fan out, return a synthesized majority-vote verdict.
result = pool.validate("<your review prompt here>", timeout=60)
print(result.verdict.status, result.verdict.confidence)
```

Three public primitives:

| Import | Purpose |
|---|---|
| `lope.ensemble.EnsemblePool` | Parallel validator fan-out with ThreadPoolExecutor; majority-vote synthesis. |
| `lope.ensemble.synthesize` | Pure function — aggregate a list of `ValidatorResult`s into one. Useful when the results came from somewhere other than a live ThreadPool (cached run, HTTP API, etc.). |
| `lope.validators.build_validator_pool(cfg)` | The same config-driven pool builder used by the CLI. Returns `EnsemblePool` when `cfg.parallel=True`, `ValidatorPool` (sequential fallback chain) otherwise. |

Back-compat: `from lope.validators import EnsemblePool` still resolves (re-export). `from lope import EnsemblePool` also works via the package root.

**Use this path when:** you want fan-out but not the CLI harness (you're building a library on top), you want to call the synthesis logic on results from a non-subprocess source, or you're writing a programmatic smoke test for a custom provider.

**Do not use this path when:** you want lope's end-to-end behaviour (sprint negotiation, phase retry, journal) — that belongs in the CLI surface. Library use is for the narrow fan-out primitive, not the full orchestration.

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
# Runtime safety and request planning

See [job-lifecycle.md](job-lifecycle.md) for ownership, cancellation, and safe stale-job reconciliation. Every multi-call command accepts `--run-timeout`; `--timeout` remains a per-call ceiling. Request admission reports a deterministic plan before launch and bounds direct input, chunk count, calls, and output bytes.
