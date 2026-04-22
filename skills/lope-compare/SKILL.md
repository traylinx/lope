---
name: lope-compare
description: "Compare two files across every validator. Each model picks which file is better against explicit criteria (--criteria flag). Tally the picks; print the winner. Use for A/B review, before/after diff evaluation, migration decisions, bake-offs. Criteria is mandatory in the prompt — 'better' is never model-invented."
---

# Lope Compare

Send two file contents + explicit evaluation criteria to every validator. Each one picks A or B. Tally the picks; print the winner.

This is `vote` specialized for two-file A/B comparison with file content embedded in the prompt.

## Invocation

Two paths:

1. **Explicit slash command.** User types `/lope-compare auth_old.py auth_new.py`. Route to `lope compare`. Pass `--criteria "..."` if the user gave one.
2. **Natural language.** User says:
   - "Which file is better — A or B?"
   - "Compare this old version to the new one"
   - "Run a bake-off between these two implementations"
   - "Before/after review across models"

   Infer the two file paths from the conversation context and invoke `lope compare <a> <b>`. If the user hinted at criteria ("for security", "for performance", "for ergonomics"), pass `--criteria`.

## Command shape

```bash
lope compare <file_a> <file_b>                           # default criteria
lope compare <a> <b> --criteria "security and readability"
lope compare <a> <b> --criteria "correctness, performance, ergonomics"
lope compare <a> <b> --json                              # machine-readable tally
lope compare <a> <b> --validators claude,opencode
lope compare <a> <b> --timeout 120
```

Default criteria: `"correctness and clarity"`. Always pass `--criteria` if the user hinted at specific dimensions — it's what keeps "better" from being model-invented.

## Output shape

```
Lope compare:
  A: auth_old.py  (2341 chars)
  B: auth_new.py  (1887 chars)
  Criteria: correctness and readability
  Validators: claude, gemini, pi

━━━ claude ━━━
  chose: B
━━━ gemini ━━━
  chose: B
━━━ pi ━━━
  chose: A

Tally:  A=1  B=2

Winner: B  (auth_new.py)
```

Ties print "No winner — tie" and the user decides.

## Design constraints

- **Criteria opacity fix** (pi design review, v0.5.0): `--criteria` is injected into every validator's prompt explicitly so "better" is bound to real dimensions, not each model's private interpretation. This is the single most important knob.
- **File size caveat.** Both files ride inline in the prompt. Large files multiply tokens and can exceed context windows — affected validators surface as `[UNPARSEABLE]`, the rest still vote. For huge artifacts, pre-extract the relevant sections.
- **Non-text files not supported.** Binary, PDF, image — not handled. Use describe tools for those.

## When NOT to use compare

- One file to review → `/lope-review`.
- 3+ options → `/lope-vote` with `--options A,B,C`.
- Open-ended "which is best?" without concrete files → `/lope-ask`.

## Cost

Two full file contents × N validators. Long files multiply this fast. `--validators claude,gemini` caps the fan-out.
