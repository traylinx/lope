# Lope deliberate — council mode

`lope deliberate <template> <scenario>` is Lope's structured adversarial-reasoning verb. It is **not** code execution: nothing under `lope deliberate` modifies source files, touches git, or runs commands beyond `Validator.generate`. The goal is to take a scenario file (an ADR question, a PRD draft, a build-vs-buy debate) and produce a synthesis artifact that has survived an anonymized peer-critique round and a rubric review by the council itself.

---

## The 7-stage protocol

```
1. Scenario intake          — read the input file (or stdin)
2. Independent positions    — each validator drafts blind
3. Anonymized critique      — Response A/B/C labels, names stripped
4. Revision                 — each defends or folds in critiques
5. Synthesis                — primary writes the final artifact
6. Rubric review            — every validator scores PASS / NEEDS_FIX
7. Minority report          — high-severity NEEDS_FIX preserved
```

Stages 3 + 4 are skipped under `--depth quick`. Under `--depth deep`, the minority report preserves every dissent (not just high severity).

A single label map is built once at session start so the same validator gets the same `Response X` across **every** stage — positions, critiques, revisions, synthesis source list, rubric, and minority report. There is no path through the protocol where validator identity can leak.

---

## Templates

Six built-in templates ship in `lope/deliberation.py`. Each carries section list + rubric + per-stage prompt fragments.

| Name | Title | Required sections |
|---|---|---|
| `adr` | Architecture Decision Record | Context, Decision, Consequences, Alternatives Considered |
| `prd` | Product Requirements Document | Problem, Users and Use Cases, Goals and Non-Goals, Solution Sketch, Acceptance Criteria, Risks |
| `rfc` | Request for Comments | Summary, Motivation, Detailed Design, Drawbacks, Rationale and Alternatives, Open Questions |
| `build-vs-buy` | Build vs Buy Analysis | Decision Statement, Requirements, Build Option, Buy Options, Total Cost of Ownership, Recommendation |
| `migration-plan` | Migration Plan | Migration Goal, Source and Target State, Phases and Sequence, Rollback Strategy, Validation Gates, Risks |
| `incident-review` | Incident Review | Incident Summary, Timeline, Root Cause, Contributing Factors, Action Items, Lessons Learned |

Templates are Python `TemplateSpec` data, not markdown frontmatter — the prompt fragments and rubric live in code so tests can monkey-patch them and the rubric stays load-bearing rather than narrative.

---

## Output directory layout

Every run writes to `lope-runs/<timestamp>-<template>/` (or wherever you point `--out`):

```
lope-runs/20260427-153022-adr/
  scenario.md           # input copy (redacted)
  trace.jsonl           # one line per turn (redacted)
  turns/
    01-positions/
      Response-A.md
      Response-B.md
      Response-C.md
    02-critiques/
      Response-A.md
      Response-B.md
      Response-C.md
    03-revisions/
      Response-A.md
      Response-B.md
      Response-C.md
  final/
    report.md           # synthesis with template sections
    minority-report.md  # preserved dissent (or "unanimous" note)
    decision-log.md     # template + council size + verdicts + timestamps
```

In anonymous mode (default) the trace `validator` field is replaced with `(anonymous)` while the `label` is retained for debugging. With `--no-anonymize` the trace and minority report carry validator names verbatim — useful for a postmortem of "which model was the lone dissenter."

---

## Quick examples

```bash
# Quick ADR (skip critique + revision rounds)
lope deliberate adr docs/samples/jwt-rotation.md --depth quick

# Standard PRD with the default council
lope deliberate prd specs/agent-stream-v2.md

# Build-vs-buy with a synthesis pass on top of the council outputs
lope deliberate build-vs-buy decisions/secrets-vault.md --synth

# Incident review pulling Brain context for prior incidents
lope deliberate incident incidents/2026-04-21.md \
  --brain-context "prior auth incidents 2025-2026" --brain-log

# Re-run with names visible (postmortem mode)
lope deliberate adr decisions/storage.md --no-anonymize
```

`--out custom-dir/` overrides the default `lope-runs/<timestamp>-<template>/` location. `--human-questions never|blocking|always` controls whether the synthesizer surfaces clarifying questions to the human (default: never).

---

## Rubric scoring contract

Each validator in the council scores the synthesis with a strict reply shape:

```
VERDICT: PASS|NEEDS_FIX
SEVERITY: low|medium|high
- objection 1
- objection 2
```

The parser is tolerant — case insensitive, `critical`/`blocker` fold into `high`, unparseable replies default to `NEEDS_FIX` so a confused validator never silently passes the synthesis. Every objection bullet runs through `redact_text` before storage.

A council that votes unanimous PASS still gets a minority report file — it just contains the line "No council member dissented; the synthesis passed the rubric unanimously." That keeps every run shape-stable for downstream scripts.

---

## What deliberate does NOT do

- **No source-file modification.** The verb is read-only against your tree. Tests pin this with a sentinel file.
- **No git mutations.** No commit, no branch, no stash.
- **No Brain writes unless asked.** `--brain-log` and Brain auto-memory are explicit opt-ins.

For broader v0.7 context, see [reference.md](reference.md). For implementation deep-dive, see `lope/deliberation.py` and `tests/test_deliberation.py`.
