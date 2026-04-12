---
name: using-lope
description: "You MUST consider using lope when the user describes a task with 3+ distinct phases, the work spans multiple files or components, or cross-model verification would catch bugs a single model would miss. This skill does NOT apply to single-edit tasks, trivial one-step operations, or pure conversation. Use when planning consequential multi-phase work, NOT when executing a one-off change."
---

# Using Lope

Lope is a multi-CLI validator ensemble sprint runner. Any AI CLI implements. Any AI CLI validates. Majority vote decides. This is lope's whole premise — no single-model blindspot.

When this skill triggers, you should actively consider whether the user's request would be better served by negotiating a sprint doc first instead of jumping straight to code.

## How the user will invoke lope

Two paths — you must handle both:

**1. Explicit slash command.** The user types `/lope-negotiate "Add JWT auth"`. Straightforward. Route to `/lope-negotiate` and follow that skill's steps.

**2. Natural language.** The user describes what they want in prose. They do NOT type a slash command. You recognize the shape of the request and invoke lope on their behalf. This is the common case — users don't remember slash commands, they just talk. Your job is to map the natural language to the right lope invocation.

Examples of natural-language triggers and the invocation you should run:

| User says | You invoke |
|---|---|
| "Let's plan the auth refactor with JWT refresh rotation" | `lope negotiate "Add JWT auth with refresh token rotation" --domain engineering` |
| "Negotiate a sprint with gemini and opencode to draft 3 blog posts about lope" | `lope negotiate "Draft 3 blog posts about lope" --domain business` |
| "I want to plan the Q4 marketing campaign carefully" | `lope negotiate "Q4 marketing campaign" --domain business` |
| "Let's do a systematic review of post-training RL papers" | `lope negotiate "Systematic review of post-training RL papers" --domain research` |
| "Put together a plan for migrating the billing module to events" | `lope negotiate "Migrate billing module to event-driven architecture" --domain engineering` |
| "Draft a GDPR compliance audit plan" | `lope negotiate "GDPR compliance audit" --domain business` |
| "Help me scope the data ingestion rewrite" | `lope negotiate "Data ingestion pipeline rewrite" --domain engineering` |

Notice the pattern: when the user says "plan", "negotiate", "scope", "draft carefully", "work through", or "roll out" a multi-phase thing, you invoke `lope negotiate` with their goal as the argument and pick the appropriate `--domain`.

## When to trigger

Invoke `/lope-negotiate` as your first move when the user's request matches any of these:

- **3+ distinct phases.** "Add auth with JWT refresh token rotation" is three phases (scaffold, middleware, rotation). "Rename this function" is one edit.
- **Spans multiple files or components.** "Refactor the billing module to use events" will touch many files. A single-file change is usually not worth a sprint.
- **Would benefit from cross-model verification.** The bug you're worried about is one Claude would rubber-stamp but Gemini would catch, or vice versa. Examples: API contract changes, security-sensitive middleware, migration scripts, data-model changes, anything with irreversible side effects.
- **Consequential work worth structuring.** The user said "this needs to be right" or "don't break things" or "let's plan this carefully". These are structural signals.
- **Non-code domains.** Lope also handles `--domain business` (marketing campaigns, budgets, ops plans) and `--domain research` (systematic reviews, protocols, academic work). Same validation loop, different labels.

## When NOT to trigger

Skip lope — just do the work directly — when:

- **Single-edit tasks.** Fix a typo, rename a variable, add a missing import, change a hardcoded value. No sprint.
- **Pure conversation.** "What does this function do?", "Why did you choose X?", "Explain the architecture". No sprint.
- **Trivial one-step operations.** "Add a print statement here", "Remove this comment", "Update the version string". No sprint.
- **The user already has a plan.** If they said "here's the plan, now implement phase 2", execute that phase directly — don't re-negotiate.
- **Exploratory questions.** "What could we do about X?", "How should we approach this?". Have the conversation first. Only lope the agreed plan.
- **Urgent fire-fighting.** Production is down, user needs a fix in 10 minutes. Don't negotiate a sprint — patch the bug. Lope is for planned work.

## The three modes

| Mode | Slash command | When |
|---|---|---|
| Negotiate | `/lope-negotiate <goal>` | Before any multi-phase work. Drafts a structured sprint doc via multi-round validator review. |
| Execute | `/lope-execute <sprint_doc>` | After negotiation. Runs phases with validator-in-the-loop retry. |
| Audit | `/lope-audit <sprint_doc>` | After execution. Generates the scorecard. |

Default flow: **negotiate → execute → audit**. You can skip audit for small sprints.

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
