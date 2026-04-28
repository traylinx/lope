---
name: lope-deliberate
description: Run an Agent-Order council deliberation on a decision artifact. Six built-in templates (adr, prd, rfc, build-vs-buy, migration-plan, incident-review). 7-stage protocol turns a scenario file into a peer-scrutinized artifact. Read-only, no git mutation.
agent: build
---

# Lope deliberate

Run the council on a decision artifact. Output is a structured directory with the synthesis, a minority report, a decision log, and a redacted JSONL trace.

## What to do

```bash
lope deliberate <template> <scenario>
```

Templates: `adr`, `prd`, `rfc`, `build-vs-buy`, `migration-plan`, `incident-review`.

Optional flags:
- `--depth quick` — skip critique + revision rounds (fast first-cut)
- `--depth deep` — preserve every dissent in the minority report
- `--no-anonymize` — show validator names (postmortem mode)
- `--out <dir>` — override default `lope-runs/<ts>-<template>/`
- `--brain-context "<query>"` / `--brain-log` — Makakoo OS Brain integration

## When to run deliberate

- "Should we adopt X?" → `lope deliberate adr <scenario>`
- "Review this ADR / PRD / RFC" → `lope deliberate <template> <file>`
- "Build vs buy this capability?" → `lope deliberate build-vs-buy <file>`
- "Plan the migration" → `lope deliberate migration-plan <file>`
- "Run the incident review" → `lope deliberate incident-review <file>`

## Do not

- Do not run deliberate without a scenario file. If the user has only prose, ask them to drop it into a markdown file or pipe via `lope deliberate <template> -`.
- Do not expect git or source-tree mutations — deliberate is purely a reasoning verb.
- Run `lope docs` for the full reference.
