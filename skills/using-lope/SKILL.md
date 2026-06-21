---
name: using-lope
description: "Use Lope when cross-model perspective helps: multi-phase sprints, flow graphs, ask/review/vote/compare/pipe, team, lope memory, lope deliberate, gate/check, update, or Headroom setup. Trigger on 3+ phases, consequential multi-file work, second opinions, artifact review, consensus/SARIF, ADR/PRD/RFC/build-vs-buy/migration, A/B or yes/no choices, pipes, roster changes, updates, or Headroom. Skip trivial single edits, pure conversation, urgent firefighting."
---

# Using Lope

Lope is a multi-CLI ensemble for AI work. Any AI CLI drafts, any AI CLI validates, multiple perspectives cover each other's blind spots. Core philosophy: **no single-model blindspot**. Current command surface covers structured sprints, single-shot fan-out, team management, autonomous graphs, persistent judgment, evidence gates, self-update, and optional Headroom setup:

| Mode | Skill | Shape of input/output |
|---|---|---|
| `negotiate` | [lope-negotiate] | Goal → sprint doc with phases + verdicts |
| `execute`   | [lope-execute]   | Sprint doc → implemented deliverables with per-phase review |
| `implement` | [lope-implement] | Sprint doc + selected roster → zero-human sprint execution |
| `audit`     | [lope-audit]     | Sprint doc → scorecard |
| `flow`      | [lope-flow]      | DOT graph → autonomous multi-agent run (fan-out, consensus, fix-loops), bounded by visit caps |
| `ask`       | [lope-ask]       | One question → N raw answers (one per model) |
| `review`    | [lope-review]    | One file + focus → N raw critiques **or** consensus-ranked findings (`--consensus`) |
| `vote`      | [lope-vote]      | Question + options → tally + winner |
| `compare`   | [lope-compare]   | Two files + criteria → tally + winner |
| `pipe`      | [lope-pipe]      | stdin → N raw answers (composable shell verb) |
| `team`      | [lope-team]      | Natural-language roster management (add/list/remove/test) |
| `memory` | [lope-memory] | Persistent finding store: stats / search / file / hotspots / forget |
| `deliberate` | [lope-deliberate] | Scenario file + template (ADR/PRD/RFC/build-vs-buy/migration/incident) → 7-stage council artifact |
| `gate`      | shell: `lope gate` | Run project-defined objective evidence gates and compare against a baseline |
| `check`     | shell: `lope check` | CI-friendly one-shot run of objective evidence gates |
| `update` / `upgrade` | shell: `lope update` | Self-update a git checkout and refresh installed host skills |
| `headroom`  | [lope-headroom] | Configure optional Headroom compression for bulky Lope / agent outputs |
| `help`      | [lope-help] | Print the complete reference via `lope docs` |

`ask`, `review`, `vote`, `compare`, and `pipe` are the lightweight verbs — no sprint, no phases, no validator retry loop. `team` manages the roster. `flow` shapes autonomous collaboration. `lope memory` and `lope deliberate` turn raw fan-out into durable judgment. `gate` / `check` run deterministic evidence commands from the project. `update` keeps the git checkout and host skills current. The cross-cutting flags `--consensus`, `--synth`, `--remember`, `--brain-context`, `--brain-log`, `--divide`, and `--roles` are opt-in.

When this skill triggers, consider which command shape fits — don't force every request into `negotiate`.

## How the user will invoke lope

Two paths — you must handle both:

**1. Explicit slash command.** The user types `/lope-negotiate "Add JWT auth"`. Straightforward. Route to `/lope-negotiate` and follow that skill's steps.

**2. Natural language.** The user describes what they want in prose. They do NOT type a slash command. You recognize the shape of the request and invoke lope on their behalf. This is the common case — users don't remember slash commands, they just talk. Your job is to map the natural language to the right lope invocation.

Examples of natural-language triggers and the invocation you should run:

**Plan/structure work → `negotiate`:**

| User says | You invoke |
|---|---|
| "Let's plan the auth refactor with JWT refresh rotation" | `lope negotiate "Add JWT auth with refresh token rotation" --domain engineering` |
| "Negotiate a sprint with gemini and opencode to draft 3 blog posts about lope" | `lope negotiate "Draft 3 blog posts about lope" --domain business` |
| "I want to plan the Q4 marketing campaign carefully" | `lope negotiate "Q4 marketing campaign" --domain business` |
| "Let's do a systematic review of post-training RL papers" | `lope negotiate "Systematic review of post-training RL papers" --domain research` |

**Cross-model Q&A → `ask`:**

| User says | You invoke |
|---|---|
| "What do gemini and claude say about this approach?" | `lope ask "<their question>"` |
| "Get a second opinion across models" | `lope ask "<the question from context>"` |
| "Ask all the CLIs if X is safe" | `lope ask "Is X safe? <details>"` |
| "Check with the other models before I commit this" | `lope ask "<what they're about to do>"` |

**Cross-model file critique → `review`:**

| User says | You invoke |
|---|---|
| "Review this file across models" | `lope review <file>` |
| "Have claude, gemini, and opencode check auth.py for security" | `lope review auth.py --focus security` |
| "Multi-model review of this PR diff" | `lope review <path/to/diff>` |
| "What would the other CLIs say about my config?" | `lope review <config file>` |

**Structured vote with options → `vote`:**

| User says | You invoke |
|---|---|
| "Yes/no from all the models — is X safe?" | `lope vote "Is X safe?" --options "yes,no"` |
| "Take a vote: 3.12 or 3.13 for a new project?" | `lope vote "Python version for new project" --options "3.12,3.13"` |
| "Ship, hold, or escalate — what do the CLIs say?" | `lope vote "<context>" --options "ship,hold,escalate"` |

**A/B file comparison → `compare`:**

| User says | You invoke |
|---|---|
| "Compare these two implementations" | `lope compare <a> <b>` |
| "Which is better for security — old or new?" | `lope compare old.py new.py --criteria security` |
| "Before/after bake-off across models" | `lope compare <before> <after> --criteria "correctness, readability"` |

**Piped input → `pipe`:**

| User says | You invoke |
|---|---|
| "Send this diff to every model" | `gh pr diff \| lope pipe` |
| "Pipe the output into lope" | `<command> \| lope pipe` |
| "Have every CLI look at this log" | `cat log.txt \| lope pipe` |

**Roster management → `team`:**

| User says | You invoke |
|---|---|
| "Add openclaw to lope with my Tytus pod" | `lope team add openclaw --url $OPENAI_BASE_URL/chat/completions --model openclaw --key-env OPENAI_API_KEY` |
| "Add my local ollama (qwen3:8b) as a teammate" | `lope team add my-ollama --cmd "ollama run qwen3:8b {prompt}"` |
| "Remove codex from the team" | `lope team remove codex` |
| "Who's on lope?" / "list validators" | `lope team list` |
| "Test if the new mistral teammate works" | `lope team test mistral` |
| "Make openclaw the primary" | `lope team add openclaw --url ... --force --primary` |

**Autonomous multi-agent graph → `flow`:**

| User says | You invoke |
|---|---|
| "Run agents autonomously to build X, let them negotiate the approach" | `lope flow init consensus` then `lope flow run .lope/flow/consensus.dot --task "build X"` |
| "I want a propose → consolidate → implement → review loop, no babysitting" | `lope flow run .lope/flow/consensus.dot --task "<goal>"` |
| "Set up a fix-loop: implement, ensemble-review, retry until it passes" | `lope flow init judge-loop` then `lope flow run .lope/flow/judge-loop.dot --task "<goal>"` |
| "Show me / draw what the agents will do" | `lope flow render <file>.dot -o flow.svg` |
| "Change how the agents collaborate" | edit the `.dot` graph, then `lope flow validate` + `lope flow run` |

Pattern: **plan → negotiate**, **ask → ask**, **critique artifact → review**, **predefined choices → vote**, **A/B files → compare**, **piped from shell → pipe**, **manage roster → team**, **shape an autonomous agent graph → flow**, **objective proof → gate/check**, **update Lope → update**, **configure compression → headroom**. Don't force an `ask`-shaped request through `negotiate` — it wastes tokens and produces a sprint doc the user didn't want. Reach for `flow` when the *shape of the collaboration* (fan-out, consensus, loops) is the point and the run must be autonomous + bounded.

## When to trigger

Reach for lope (any mode) whenever the user's request matches any of these:

- **Multi-phase work.** "Add auth with JWT refresh token rotation" has phases. → `negotiate` then `execute`.
- **Multi-file refactor.** "Refactor the billing module to use events" touches many files. → `negotiate`.
- **Cross-model verification needed.** Security-sensitive middleware, API contracts, migration scripts. If the user wants multiple models to sign off, use `negotiate` for plans, `review` for finished artifacts.
- **One-off cross-model question.** "What do the other CLIs think of this approach?" → `ask`. Fast, no sprint doc, no phases.
- **Review an artifact.** "Check this file / PR / spec across models." → `review <file>`.
- **Non-code domains.** `--domain business` (marketing, finance, ops) and `--domain research` (systematic reviews) work on negotiate. For business/research `ask` + `review` also apply — nothing is domain-locked.

## When NOT to trigger

Skip lope — just do the work directly — when:

- **Single-edit tasks.** Fix a typo, rename a variable, add a missing import, change a hardcoded value. No sprint.
- **Pure conversation.** "What does this function do?", "Why did you choose X?", "Explain the architecture". No sprint.
- **Trivial one-step operations.** "Add a print statement here", "Remove this comment", "Update the version string". No sprint.
- **The user already has a plan.** If they said "here's the plan, now implement phase 2", execute that phase directly — don't re-negotiate.
- **Exploratory questions.** "What could we do about X?", "How should we approach this?". Have the conversation first. Only lope the agreed plan.
- **Urgent fire-fighting.** Production is down, user needs a fix in 10 minutes. Don't negotiate a sprint — patch the bug. Lope is for planned work.

## Command surface

| Mode | Slash command / shell | When |
|---|---|---|
| Negotiate | `/lope-negotiate <goal>` | Before any multi-phase work. Drafts a structured sprint doc via multi-round validator review. |
| Execute | `/lope-execute <sprint_doc>` | After negotiation. Runs phases with validator-in-the-loop retry. |
| Implement | `/lope-implement <sprint_doc>` | After negotiation. Selects implementation/escalation agents once, then runs the sprint without human input. |
| Audit | `/lope-audit <sprint_doc>` | After execution. Generates the scorecard. |
| Ask | `/lope-ask "<question>"` | One question → N raw answers. No sprint, no phases. |
| Review | `/lope-review <file>` | Fan out a file review to all validators. `--focus` narrows the critique; `--consensus` ranks findings. |
| Vote | `/lope-vote "<q>" --options A,B,C` | Predefined choices → tally + winner. |
| Compare | `/lope-compare <a> <b>` | A/B file comparison. `--criteria` binds "better" to dimensions. |
| Pipe | `<cmd> \| lope pipe` | stdin-fed fan-out. Composable shell verb. |
| Team | `/lope-team add NAME ...` | Roster management. Add/remove/list/test validators without editing JSON. |
| Flow | `/lope-flow run <file.dot>` | Autonomous DOT graph workflow. Agent / ensemble / shell-gate / judge nodes, conditioned edges, fan-out + loops, bounded by visit caps. |
| Memory | `/lope-memory ...` | Search persistent review findings, hotspots, and per-file history. |
| Deliberate | `/lope-deliberate <template> <scenario>` | 7-stage council for ADR / PRD / RFC / build-vs-buy / migration / incident decisions. |
| Gate | `lope gate save/check` | Save or compare project-defined objective evidence gates. |
| Check | `lope check` | CI-friendly one-shot gate run. |
| Update | `lope update` / `lope upgrade` | Pull the git checkout and refresh installed host skills. |
| Headroom | `/lope-headroom` | Configure optional Headroom MCP compression. |
| Help | `/lope-help` | Print the complete reference via `lope docs`. |

Default flow for a *planned* task: **negotiate → implement/execute → audit**. Skip to one of the single-shot verbs (`ask`/`review`/`vote`/`compare`/`pipe`) when the user just wants multi-model output on a single prompt or artifact. Use `team` whenever the user's intent is to change who is ON the ensemble, not run it.

## Domains

Pass `--domain <name>` on negotiate to switch validator role, artifact labels, and review task:

- `engineering` (default) — code, software, infra, devops
- `business` — marketing, finance, ops, consulting, management
- `research` — studies, systematic reviews, academic work

## Supported validators

Lope auto-detects these on `$PATH`: Claude Code, OpenCode, Gemini CLI, Codex, Mistral Vibe, Aider, Ollama, Goose, Open Interpreter, llama.cpp, GitHub Copilot CLI, Amazon Q, pi (Traylinx), Qwen Code, and Agy. Custom providers via JSON config. At least two different ones are needed for a real ensemble — lope will still run with one but you lose the cross-model check.

### Codex validator hygiene

Codex is useful in Lope, but only when isolated from interactive Codex startup config. If `lope ask --validators codex` times out while trivial prompts work, suspect Codex config pollution before declaring the validator down. Broken/slow MCP startup, high default reasoning, and large prompts can turn a validator call into an 8-minute run.

Fast diagnosis:

```bash
which lope codex
PYTHONPATH=$HOME/.lope python3 -m lope team test codex "Reply exactly: CODEX-OK" --timeout 60
PYTHONPATH=$HOME/.lope python3 -m lope ask --validators codex --timeout 120 "Reply exactly: CODEX-OK"
```

Expected Lope-side Codex invocation for validator work: `codex exec --ignore-user-config --skip-git-repo-check -s read-only -c 'model_reasoning_effort="low"' <prompt>`. This avoids loading user MCPs and keeps Codex as a validator, not a full interactive agent. If the installed adapter does not do this, patch `~/.lope/lope/validators.py` before trusting Codex timings.

Use Codex with `--timeout 600` only for slow specialist/API research, or split the question into narrow prompts. For fast ensemble checks, keep prompts small or use another validator.

## If you invoke lope

Route through the dedicated slash commands, not by calling the Python module directly:

- `/lope-negotiate` for drafting
- `/lope-execute` for normal phase running
- `/lope-implement` for zero-human sprint execution after roster selection
- `/lope-audit` for scoring
- `/lope-ask` for multi-model Q&A
- `/lope-review` for multi-model file critique
- `/lope-vote` for structured votes with predefined options
- `/lope-compare` for A/B file comparison with explicit criteria
- `/lope-pipe` for stdin-fed fan-out in shell pipelines
- `/lope-team` for adding, removing, listing, or testing validators
- `/lope-flow` for autonomous declarative graph workflows (fan-out, consensus, fix-loops)
- `/lope-memory` for persistent finding search / hotspots
- `/lope-deliberate` for ADR / PRD / RFC / build-vs-buy / migration / incident councils
- `/lope-headroom` for optional Headroom compression setup
- `/lope-help` for the full reference
- shell `lope gate`, `lope check`, and `lope update` for evidence gates and maintenance

Each slash command has its own SKILL.md with the full flow. Read that skill when you invoke it, don't paraphrase.

## Caveman mode

Lope compresses validator prompts 50-65% by default via intelligent caveman mode. This is ON unless the user explicitly asks for verbose output. Don't disable it unless the task requires full prose (external writing, papers).

## Do not

- **Do not write wrapper scripts around lope.** Lope is already a CLI. If the user says "draft me 3 blog posts using lope", you invoke `lope negotiate ...` directly in a shell. You do NOT create a `generate_blog_posts.py` that imports lope, you do NOT build a pipeline harness, you do NOT "extend lope for this use case". Just run the CLI with the right arguments. The whole point of the multi-CLI ensemble is that lope is the harness — you don't need another one.
- **Do not invent flags.** `lope negotiate` accepts `--domain`, `--out`, `--max-rounds`, `--context`, `--context-file`, `--validators`, `--primary`, `--timeout`, `--parallel`, and `--sequential`. There is no `--host`, no `--title`, no `--output-format`. If unsure, run `lope negotiate --help` — do not fabricate a flag.
- **Do not invoke lope on single-edit tasks.** It's overkill and wastes the user's time.
- **Do not rubber-stamp a phase.** Lope's entire point is catching what one model would miss — if you're the validator, be critical.
- **Do not silently expand a phase's scope.** If during work you discover a better approach, raise it with the user before folding it in.
- **Do not ignore the lint.** If `lope negotiate` rejects your draft for placeholder language or empty artifact lists, fix the draft — do not try to bypass the lint with `LOPE_LINT=off` unless the user explicitly asks for it.
