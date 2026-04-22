---
name: lope-review
description: Fan out a file review to every configured lope validator. Collect N independent critiques — one per model. Optional --focus for narrower review (security, perf, tests, etc.).
agent: build
---

# Lope review

Read a file. Send it to every configured validator in parallel with a review prompt. Collect N critiques. Print side-by-side.

## What to do

1. If the user's message names a file path, use that path. If not, ask which file to review. Plain-text files only — no binaries, PDFs, or images.

2. Run:

```bash
lope review "<file>"
```

Optional flags:
- `--focus "<area>"` — narrow the critique (e.g. `--focus security`, `--focus tests`). Default focus: bugs, code-smells, design, improvements with line references.
- `--validators claude,opencode` — restrict the pool.
- `--json` — machine-readable output.
- `--timeout 120` — per-validator timeout.

3. The output shows each validator's critique in a `━━━ <name> ━━━` section. Errors show `[ERROR]` per validator; the run continues.

4. If validators contradict each other, flag it. If they converge on a bug, confirm it's worth fixing. Surface the spread; don't over-aggregate.

## When to use

Use `/lope-review` when the user has a concrete file they want critiqued — "review this", "check auth.py", "multi-model review of my PR". Do NOT use for questions without a file (that's `/lope-ask`), for implementing changes, or for non-text files.

## Cost

Full file content × N validators. Large files multiply the tokens fast. For huge files, pre-extract the section you want reviewed.
