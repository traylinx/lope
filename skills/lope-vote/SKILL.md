---
name: lope-vote
description: "Send a question with a fixed list of options to every validator. Each one replies with exactly one option label. Tally the picks; print the winner. Use for decisions where you want multi-model consensus on a pre-defined set of choices — 'should we ship X or Y?', 'pick 3.12 or 3.13', 'yes / no / needs-more-info'. Per-validator isolation: one timeout doesn't kill the tally."
---

# Lope Vote

Each configured validator receives the SAME prompt with the SAME option list and is asked to reply with exactly one label. Lope parses each reply, tallies the picks, and prints the winner. No sprint, no phases, no prose from the models.

## Invocation

Two paths — you must handle both:

1. **Explicit slash command.** User types `/lope-vote "Should we use 3.12 or 3.13?" --options 3.12,3.13`. Route to `lope vote` with the same args.
2. **Natural language.** User says:
   - "Take a vote across models on X"
   - "Let's have the CLIs decide between A and B"
   - "Yes/no from all my models — is this safe?"
   - "Which of these 3 options should we pick?"

   Extract the question and the options, invoke `lope vote "<question>" --options "<comma-separated>"`.

## Command shape

```bash
lope vote "<prompt>" --options "A,B,C"       # min 2 options, must be unique
lope vote "<prompt>" --options "yes,no"      # the simple case
lope vote "<prompt>" --options "A,B,C" --json  # machine-readable tally
lope vote "<prompt>" --options "A,B" --validators claude,gemini  # restrict pool
lope vote "<prompt>" --options "A,B" --timeout 60
```

## Output shape

Human by default:

```
━━━ claude ━━━
  chose: yes
━━━ gemini ━━━
  chose: no
━━━ opencode ━━━
  chose: yes

Tally:
  yes    2  ██
  no     1  █

Winner: yes
```

If a validator's reply doesn't match any option label, it's marked `[UNPARSEABLE]` and does not count toward the tally. If a tie happens, the output says "No winner — tie" and the user decides.

## Design constraints

- **Option drift prevention** (pi design review, v0.5.0): every validator sees the IDENTICAL option list inside a single prompt block. Reply shape is pinned — "reply with ONLY the label".
- **Parsing is strict.** The first option label that appears as a whole token in the reply wins. Substring matches are ruled out (`A` won't match inside `ALGORITHM`). Longest-first resolution handles overlaps (`3.13` beats `3.1`).
- **Label canonicalization.** Whatever case the user passed in `--options` is what appears in the tally. So `--options yes,no` stays lowercase even if a model replies "YES".

## When NOT to use vote

- Open-ended questions with no predefined answer → use `/lope-ask` instead.
- "Better of two files" decisions → use `/lope-compare` (it's vote with built-in `A,B` options and criteria handling).
- Single-model decisions → just ask one model directly.

## Cost

One call per validator, prompt = question + options block. Short prompts, fast. Cheaper than `ask` for a given pool because the reply is ~1 token.
