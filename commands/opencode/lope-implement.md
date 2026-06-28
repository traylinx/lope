---
name: lope-implement
description: Run a sprint with zero-human swarm orchestration. Select implementation and escalation agents, then execute without further human input. Single-writer safety model.
agent: build
---

# Lope implement

Run a sprint doc with zero-human execution after one roster-selection step.

## What to do

```bash
lope implement <sprint_doc>
```

Interactive terminals ask which implementation agents and escalation agents to use.

For non-interactive agent runs, pass both rosters:

```bash
lope implement <sprint_doc> \
  --agents pi,antigravity \
  --escalate-to claude,opencode
```

Useful flags:

- `--phase N` — run one phase
- `--gates` — include objective evidence gates
- `--interactive` — force roster prompts even when TTY detection would not prompt
- `--dry-run` — print resolved roster only
- `--validators`, `--primary`, `--timeout`, `--parallel`, `--sequential` — same pool override flags as other Lope verbs

Engineering `implement` prompts include minimality audit by default: prefer existing code, stdlib/native features, and the smallest safe custom code; avoid duplicate helpers, one-implementation abstractions, broad rewrites, and symptom patches. Disable with `LOPE_MINIMALITY=off`; enforce material bloat with `LOPE_MINIMALITY=enforce`. Business/research domains stay off unless explicitly enabled.

## Safety model

v1 is single-writer. The first `--agents` entry edits the checkout. Other selected agents are part of the prompt/validator/escalation context. Do not claim Lope is doing parallel same-checkout patching.

## Do not

- Do not run without a sprint doc.
- Do not omit `--agents` or `--escalate-to` in non-interactive mode.
- Do not hardcode `claude,opencode` unless that is the chosen escalation team.
- Run `lope docs` for the full reference.
