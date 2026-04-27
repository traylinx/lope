---
name: lope-deliberate
description: "Run an Agent-Order-style council deliberation on a decision artifact. Six built-in templates: ADR, PRD, RFC, build-vs-buy, migration-plan, incident-review. The 7-stage protocol — independent positions, anonymized critique, revision, synthesis, rubric review, minority report — turns a scenario file into a structured artifact that has survived peer scrutiny. Use when the user asks 'should we adopt X?', 'review this ADR / PRD / RFC', 'build vs buy this capability?', 'plan the migration', 'run the incident review'. Pure adversarial reasoning — no source-file modification, no git mutations."
---

# Lope Deliberate

`lope deliberate <template> <scenario>` runs the council on a decision artifact. The output is a structured directory with the synthesis, a minority report, a decision log, and a redacted JSONL trace of every turn.

## Invocation

Two paths — you must handle both:

1. **Explicit slash command** / direct CLI. User types `lope deliberate adr scenario.md` or `lope deliberate prd specs/x.md --depth quick`.
2. **Natural language.** User says one of:
   - "Help me decide whether to adopt JWT auth" → `lope deliberate adr <scenario>`
   - "Review this ADR / PRD / RFC" → `lope deliberate <template> <file>`
   - "Build vs buy our secrets vault" → `lope deliberate build-vs-buy <file>`
   - "Plan the Postgres migration" → `lope deliberate migration-plan <file>`
   - "Run the incident review for last Friday" → `lope deliberate incident-review <file>`

   When the user supplies the prose but no file, ask them to drop the scenario into a markdown file (or pipe via `lope deliberate <template> -`).

## Templates

| Name | Title | When to use |
|---|---|---|
| `adr` | Architecture Decision Record | "Should we adopt X for our architecture?" |
| `prd` | Product Requirements Document | "Define what we're building before we build it." |
| `rfc` | Request for Comments | "Detailed design open for council critique." |
| `build-vs-buy` | Build vs Buy Analysis | "Make or buy this capability?" — 3-year TCO horizon. |
| `migration-plan` | Migration Plan | "Cut over from A to B with rollback at every phase." |
| `incident-review` | Incident Review | "Postmortem with timeline, root cause, owned action items." |

## Command shape

```bash
# Default: standard depth, anonymous critique, output to lope-runs/<ts>-<template>/
lope deliberate adr scenario.md

# Quick mode skips critique + revision rounds
lope deliberate adr scenario.md --depth quick

# Deep mode preserves every dissent in the minority report
lope deliberate prd scenario.md --depth deep

# Force a specific output directory
lope deliberate rfc scenario.md --out custom-runs/jwt-rfc/

# Postmortem mode: validator names visible (de-anonymize)
lope deliberate incident-review incident.md --no-anonymize

# Pipe a scenario from stdin
cat scenario.md | lope deliberate adr -

# Brain-aware deliberation (Makakoo OS only)
lope deliberate adr scenario.md --brain-context "prior auth decisions" --brain-log
```

## What gets written

```
lope-runs/<timestamp>-<template>/
  scenario.md           # input copy (redacted)
  trace.jsonl           # per-turn record (redacted; validator names hidden in anonymous mode)
  turns/
    01-positions/*.md
    02-critiques/*.md
    03-revisions/*.md
  final/
    report.md           # synthesis with template sections
    minority-report.md  # preserved dissent (or "unanimous" note)
    decision-log.md     # template + council size + verdicts + timestamps
```

## Hard rules

- **No source-file modification.** The verb is read-only against the user's tree. Tests pin this.
- **No git mutation.** No commit, no branch, no stash.
- **Anonymous by default.** Validator names never reach the critique prompt unless `--no-anonymize` is passed. The same validator gets the same `Response X` label across every stage (positions, critiques, revisions, synthesis source list, rubric, minority report).
- **Always emit a minority report.** Even when the rubric is unanimous PASS the file is written with a "no dissent" note so downstream scripts have a stable shape.
- **Trace JSONL is redaction-clean.** Every turn text + metadata passes through `redact_text`.

## Depth presets

- `--depth quick` — skip stages 3 (critique) and 4 (revision). Use when the user wants a fast first-cut artifact rather than a peer-reviewed one.
- `--depth standard` (default) — full 7-stage protocol; minority report keeps high-severity NEEDS_FIX objections.
- `--depth deep` — full protocol; minority report preserves **every** dissent, not just high-severity.

## Pairing with other v0.7 verbs

- `--brain-context "..."` and `--brain-log` plug into Makakoo OS Brain.

For full details: `lope docs` ([docs/deliberation.md](../../docs/deliberation.md)).
