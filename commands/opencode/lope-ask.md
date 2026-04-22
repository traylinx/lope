---
name: lope-ask
description: Ask every configured lope validator the same question and collect N raw answers — one per model. No sprint, no phases, no voting. For cross-model Q&A.
agent: build
---

# Lope ask

Fan out one question to every configured validator. Collect N raw answers. Print side-by-side.

## What to do

1. If the user's message contains a question, treat the whole question as the argument. If not, ask what they want to ask.

2. Run:

```bash
lope ask "<the question>"
```

Optional flags:
- `--context "<ctx>"` — prepend shared context to every validator's prompt.
- `--validators claude,gemini` — restrict the pool.
- `--json` — machine-readable JSON output.
- `--timeout 60` — per-validator timeout.

3. The output shows each validator's answer in a `━━━ <name> ━━━` section. Errors show `[ERROR]` per validator; the run continues.

4. If the models disagree sharply, call that out. If they agree, confirm the consensus. Don't bury the spread.

## When to use

Use `/lope-ask` when the user wants multi-model perspectives on one prompt — "get a second opinion", "what do the other CLIs think", "check with gemini and claude". Do NOT use for multi-phase planning (that's `/lope-negotiate`), file critique (that's `/lope-review`), or single-model quick answers (just answer directly).

## Cost

N validators × prompt tokens. Restrict with `--validators X,Y` for cheap runs.
