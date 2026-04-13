---
name: lope-audit
description: Generate a scorecard from sprint execution results — per-phase verdicts, confidence scores, duration, overall status. Appends to lope's journal.
agent: build
---

# Lope audit

Generate a scorecard from a sprint that has already been executed. Reads the execution state file and produces a structured audit report.

## What to do

```bash
lope audit <sprint_doc_path>
```

Optional: `--no-journal` to skip writing to lope's journal, `--out <path>` to redirect the scorecard.

Output is a markdown scorecard with per-phase verdicts, confidence scores, durations, and overall sprint status. Print the summary to the user and point them at the full scorecard file.

## When to run audit

- After `lope execute` completes (success or escalation) and you want a structured report
- For weekly/monthly retro on past sprints — audit every sprint in a folder and compare

## Do not

- Do not run audit before execute — there's nothing to audit yet.
- Run `lope docs` for the full reference.
