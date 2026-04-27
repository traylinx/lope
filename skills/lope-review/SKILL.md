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

## v0.7 superpowers (opt-in)

The default invocation above is the v0.6 raw fan-out. v0.7 layers a structured-mode
pipeline on top. None of these flags change behavior unless explicitly passed.

```bash
# Consensus review — merge, dedupe, and rank findings across N validators
lope review auth.py --consensus
lope review auth.py --consensus --format markdown-pr   # PR-comment-shaped output
lope review auth.py --consensus --format sarif > review.sarif   # CI upload
lope review auth.py --consensus --json
lope review auth.py --consensus --include-raw          # also dump raw answers

# Tune the merger
lope review auth.py --consensus --similarity 0.9 --min-consensus 0.5

# Synthesis — primary rolls N answers into one executive summary
lope review auth.py --consensus --synth
lope review auth.py --consensus --synth --anonymous    # strip validator names

# Persistent memory — stores findings for cross-session recall
lope review auth.py --consensus --remember
# Then: lope memory hotspots / lope memory file auth.py / lope memory search "..."

# Divide a directory into per-file reviews, merge findings globally
lope review src/ --divide files --consensus

# Diff hunks — review each hunk and re-anchor findings onto post-change lines
lope review pr.diff --divide hunks --consensus --format sarif

# Role lenses — round-robin security/performance/tests/etc. across validators
lope review auth.py --roles security,performance,tests --consensus

# Brain-aware review (Makakoo OS only)
lope review auth.py --consensus --brain-context "auth decisions" --brain-log
```

`--divide` and `--roles` are mutually exclusive. Pass one or the other; the
combination is reserved for a future phase and the CLI rejects it.

When the user says "give me the consensus view", "rank findings across the
council", "post this as a PR comment", "upload to GitHub code-scanning",
"which file keeps getting flagged?", "executive summary across models",
"strip the model names" — recognize and invoke the relevant flags above.

For the formats, see [docs/ci.md](../../docs/ci.md) (SARIF + PR comment).
For memory, see [skill: lope-memory](../lope-memory/SKILL.md).
For Brain integration, see [docs/makakoo.md](../../docs/makakoo.md).

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
