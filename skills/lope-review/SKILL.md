---
name: lope-review
description: "Fan out a file review to every configured validator and collect N independent critiques — one per model. Use for cross-model code review, doc review, contract review, resume review, or any review-shaped task where multiple perspectives catch what a single model would miss. Optional --focus flag targets a specific concern (security, perf, tests, clarity, etc.)."
---

# Lope Review

Read a file. Send it to every configured validator in parallel with a review prompt. Print N critiques side-by-side. No sprint, no phases, no majority vote.

This is the lightest way to get cross-model review on a single artifact — sharper than pasting into one chat, because each model reviews independently without seeing the others' takes.

## Invocation

Two paths — you must handle both:

1. **Explicit slash command.** User types `/lope-review path/to/file.py`. Route to `lope review <file>`.
2. **Natural language.** User says something like:
   - "Review this file across models" / "Get a multi-model review of auth.py"
   - "Have the other CLIs check my PR"
   - "Review this for security / performance / accessibility / correctness"
   - "What would gemini and claude say about this file?"

   Recognize the shape and invoke `lope review <file> --focus <area>` on their behalf. If no focus is obvious, leave `--focus` off — the default review prompt is "bugs, code-smells, design issues, improvements with line references."

## Command shape

```bash
lope review <file>                        # general review, all validators
lope review <file> --focus "security"     # narrow the prompt
lope review <file> --focus "test coverage"
lope review <file> --json                 # machine-readable JSON
lope review <file> --validators claude,opencode  # restrict the pool
lope review <file> --timeout 120          # per-validator timeout
```

## When to use `review` vs `execute` vs `ask`

- **`review`**: the user has an artifact (file) they want critiqued. Output = N critiques, not a code diff.
- **`execute`**: the user has a sprint doc with defined phases and wants lope to IMPLEMENT the deliverables. Output = code written + validator verdicts.
- **`ask`**: the user has a question, not a file. Output = N answers to the question.

If the user says "review", "critique", "check", "audit this file" → `lope review`. If they say "fix this file", "improve this file", "refactor this" → that's execute or a direct code edit, not review.

## File semantics

`lope review` sends the file content inline inside the review prompt. For very large files (>10k lines / very long) this may exceed some validators' context windows — results will show `[ERROR]` for affected validators, and the rest will complete normally. For huge files, pre-extract the section you want reviewed and pass that file instead.

Binary files, images, and PDFs are not supported — the command reads as UTF-8 text. Encoding errors are tolerated via `errors="replace"` but non-text files will produce garbage prompts. Use `harvey_describe_image` / `harvey_describe_video` for non-text media.

## Output shape

Same as `lope ask` — one `━━━ <validator> ━━━` section per model, printed in completion order (fastest first). Use `--json` for `[{"validator": ..., "review": ..., "error": ...}]`.

## Pairing with `execute` / `negotiate`

After a `lope execute` run, `lope review <deliverable>` is a useful sanity check — multiple models reviewing the final file, independent of the phase-review loop. Similarly, `lope review SPRINT-plan.md` before running `lope execute` catches planning bugs early.

## Cost awareness

Same as `lope ask` — N× tokens per run. Long files multiply this further. For casual reviews, `--validators claude,gemini` keeps the cost bounded to two fast models.
