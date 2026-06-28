---
name: lope-implement
description: "Run a sprint with zero-human swarm orchestration. First select implementation agents and escalation agents, then Lope executes phases without further human input. Use when the user says implement the whole sprint, stay out of the loop, use Claude/OpenCode or specific CLIs for escalations, or wants autonomous sprint backlog completion."
---

# Lope Implement

`lope implement <sprint_doc>` is the zero-human wrapper around `lope execute`.

It exists for one job: select the agent roster once, then run the sprint without asking the human again.

## Invocation

Two paths:

1. **Interactive TTY**

```bash
lope implement SPRINT.md
```

Lope asks:

1. Which implementation agents to use
2. Which escalation agents to use

After that, no human input.

2. **Agent / CI / non-interactive**

```bash
lope implement SPRINT.md \
  --agents pi,antigravity \
  --escalate-to claude,opencode
```

In non-interactive mode, both `--agents` and `--escalate-to` are required. No silent defaults.

## Common flags

```bash
# Run one phase
lope implement SPRINT.md --phase 2 --agents pi --escalate-to claude,opencode

# Include objective evidence gates
lope implement SPRINT.md --agents pi --escalate-to claude,opencode --gates

# Verify the roster without executing
lope implement SPRINT.md --agents pi --escalate-to claude,opencode --dry-run
```

## Minimality discipline

For engineering sprints, `lope implement` injects minimality audit guidance by default: prefer existing code, stdlib/native features, and the smallest safe custom code; avoid duplicate helpers, one-implementation abstractions, broad rewrites, and symptom patches. This is audit guidance unless `LOPE_MINIMALITY=enforce` is set. Disable with `LOPE_MINIMALITY=off`. Business/research domains stay off unless explicitly enabled.

## Safety model

v1 is a **single-writer swarm**.

The first `--agents` entry is the writing lead. Lope does not ask multiple CLIs to edit the same checkout concurrently because that creates patch races. The selected implementation and escalation agents are injected into the prompt and validator pool. Real parallel implementation belongs in a future worktree-backed mode.

## Rules

- Do not run without a sprint doc.
- Do not invent agent names. Run `lope team list` if unsure.
- In non-interactive mode, always pass `--agents` and `--escalate-to`.
- Prefer `--dry-run` before long autonomous runs.
- Use `--interactive` to force the roster picker when TTY detection would otherwise treat the run as non-interactive.
- If validators return NEEDS_FIX, let Lope retry. Do not ask the human.
