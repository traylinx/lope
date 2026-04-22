---
name: using-lope
description: "You MUST consider using lope whenever cross-model perspective would help — multi-phase sprints (negotiate/execute/audit), one-off cross-model questions (ask), or cross-model file review (review). Trigger on any of: 3+ phases, consequential multi-file work, user wants a second opinion across CLIs, review/critique of an artifact, 'what do the other models think', 'check with gemini/claude/opencode'. Skip for trivial single edits, pure conversation, or urgent fire-fighting."
---

# Using Lope

Lope is a multi-CLI ensemble for AI work. Any AI CLI drafts, any AI CLI validates, multiple perspectives cover each other's blind spots. Core philosophy: **no single-model blindspot**. Five modes cover different shapes of work:

| Mode | Skill | Shape of input/output |
|---|---|---|
| `negotiate` | [lope-negotiate] | Goal → sprint doc with phases + verdicts |
| `execute`   | [lope-execute]   | Sprint doc → implemented deliverables with per-phase review |
| `audit`     | [lope-audit]     | Sprint doc → scorecard |
| `ask`       | [lope-ask]       | One question → N raw answers (one per model) |
| `review`    | [lope-review]    | One file + focus → N raw critiques (one per model) |

`ask` and `review` are the lightweight verbs — no sprint, no phases, no majority vote. Use them whenever the user wants multi-model output on a single prompt or artifact.

When this skill triggers, consider which of the five modes fits — don't force every request into `negotiate`.

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

Pattern: **plan → negotiate**, **ask → ask**, **critique artifact → review**. Don't force an `ask`-shaped request through `negotiate` — it wastes tokens and produces a sprint doc the user didn't want.

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

## The five modes

| Mode | Slash command | When |
|---|---|---|
| Negotiate | `/lope-negotiate <goal>` | Before any multi-phase work. Drafts a structured sprint doc via multi-round validator review. |
| Execute | `/lope-execute <sprint_doc>` | After negotiation. Runs phases with validator-in-the-loop retry. |
| Audit | `/lope-audit <sprint_doc>` | After execution. Generates the scorecard. |
| Ask | `/lope-ask "<question>"` | One question → N raw answers. No sprint, no phases. |
| Review | `/lope-review <file>` | Fan out a file review to all validators. `--focus` narrows the critique. |

Default flow for a *planned* task: **negotiate → execute → audit**. Skip to `ask` or `review` when the user just wants multi-model output on a single prompt or artifact — those modes are single-shot, no sprint doc, no phase retries.

## Domains

Pass `--domain <name>` on negotiate to switch validator role, artifact labels, and review task:

- `engineering` (default) — code, software, infra, devops
- `business` — marketing, finance, ops, consulting, management
- `research` — studies, systematic reviews, academic work

## Supported validators

Lope auto-detects these on `$PATH`: Claude Code, OpenCode, Gemini CLI, Codex, Mistral Vibe, Aider, Ollama, Goose, Open Interpreter, llama.cpp, GitHub Copilot CLI, Amazon Q. Custom providers via JSON config. At least two different ones are needed for a real ensemble — lope will still run with one but you lose the cross-model check.

## If you invoke lope

Route through the dedicated slash commands, not by calling the Python module directly:

- `/lope-negotiate` for drafting
- `/lope-execute` for running
- `/lope-audit` for scoring
- `/lope-ask` for multi-model Q&A
- `/lope-review` for multi-model file critique

Each slash command has its own SKILL.md with the full flow. Read that skill when you invoke it, don't paraphrase.

## Caveman mode

Lope compresses validator prompts 50-65% by default via intelligent caveman mode. This is ON unless the user explicitly asks for verbose output. Don't disable it unless the task requires full prose (external writing, papers).

## Do not

- **Do not write wrapper scripts around lope.** Lope is already a CLI. If the user says "draft me 3 blog posts using lope", you invoke `lope negotiate ...` directly in a shell. You do NOT create a `generate_blog_posts.py` that imports lope, you do NOT build a pipeline harness, you do NOT "extend lope for this use case". Just run the CLI with the right arguments. The whole point of the multi-CLI ensemble is that lope is the harness — you don't need another one.
- **Do not invent flags.** `lope negotiate` accepts `--domain`, `--out`, `--max-rounds`, `--context`. That is the complete list. There is no `--host`, no `--title`, no `--validators`, no `--primary` on the negotiate subcommand. If you need a feature that doesn't exist, tell the user it's missing — do not fabricate a flag.
- **Do not invoke lope on single-edit tasks.** It's overkill and wastes the user's time.
- **Do not rubber-stamp a phase.** Lope's entire point is catching what one model would miss — if you're the validator, be critical.
- **Do not silently expand a phase's scope.** If during work you discover a better approach, raise it with the user before folding it in.
- **Do not ignore the lint.** If `lope negotiate` rejects your draft for placeholder language or empty artifact lists, fix the draft — do not try to bypass the lint with `LOPE_LINT=off` unless the user explicitly asks for it.
