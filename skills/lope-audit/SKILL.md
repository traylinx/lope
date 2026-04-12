---
name: lope-audit
description: "Generate a scorecard from sprint execution results — per-phase verdicts, confidence scores, duration, overall status. Optionally writes to the lope journal for historical tracking."
---

# Lope Audit

Generate a detailed scorecard showing per-phase verdicts, confidence scores, durations, and overall status.

This skill is invoked **two ways**:

- **Explicit:** the user typed `/lope-audit <sprint_doc>`
- **Implicit:** the user said something like "what's the score on that sprint?" or "audit the auth sprint we just ran" and you mapped it to this skill. Find the most recently executed `SPRINT-*.md` in the working directory as your default target if the user doesn't name one explicitly.

## Steps

1. Determine the sprint doc path (explicit arg OR infer from context).
2. Run the audit:

```bash
PYTHONPATH=~/.lope python3 -m lope audit "$SPRINT_PATH"
```

3. Present the scorecard to the user
4. If there were escalations: discuss what to do next

## Options

- `--no-journal` — skip writing to the lope journal at `~/.lope/journal/YYYY_MM_DD.md`

```bash
PYTHONPATH=~/.lope python3 -m lope audit "$SPRINT_PATH" --no-journal
```

## Scorecard format

```
Sprint: SPRINT-AUTH-MIDDLEWARE
Phases: 4  (PASS=4, NEEDS_FIX=0, FAIL=0, INFRA=0)
Total duration: 93.4s
Avg confidence: 0.92
---
P1 scaffold: PASS conf=0.95 dur=12s
P2 core-middleware: PASS conf=0.91 dur=34s
P3 refresh-rotation: PASS conf=0.88 dur=28s
P4 integration-tests: PASS conf=0.94 dur=19s
---
Overall: OK
```

Works for any domain — the scorecard format is domain-neutral.
