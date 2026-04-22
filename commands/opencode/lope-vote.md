---
name: lope-vote
description: Send a question + fixed list of options to every validator. Each picks one label. Tally; print winner. For decisions with predefined choices.
agent: build
---

# Lope vote

Multi-model vote with a pre-defined set of options. Each validator replies with one label; lope parses, tallies, announces the winner.

## What to do

1. Extract the question and the options from the user's message. Options must be at least 2, comma-separated.

2. Run:

```bash
lope vote "<question>" --options "<A,B,C>"
```

Optional flags:
- `--validators claude,gemini` — restrict the pool.
- `--timeout 60` — per-validator timeout.
- `--json` — machine-readable tally.

3. Read the output. Each validator's pick in its own section; tally with bar chart; winner named. `[UNPARSEABLE]` means the validator's reply didn't match any label — doesn't count.

4. Surface dissent if the vote was close or one validator picked an outlier. Don't bury a split.

## When to use

Use `/lope-vote` for multi-model consensus on predefined options — yes/no, A/B/C, ship/hold/escalate. Do NOT use for open-ended questions (that's `/lope-ask`) or A/B file comparison (that's `/lope-compare`).

## Cost

Short prompts, short replies (~1 token per validator). Cheapest lope verb per call.
