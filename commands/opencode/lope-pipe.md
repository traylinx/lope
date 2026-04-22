---
name: lope-pipe
description: Read stdin as the prompt, fan out to every validator, print per-model answers. The composable shell verb. Default is per-validator isolation; --require-all for strict exit-non-zero-on-any-error.
agent: build
---

# Lope pipe

Stdin is the prompt. Fan out to every validator. Print N responses.

## What to do

1. If the user gave you a source command (`gh pr diff`, `cat file`, etc.), pipe it through `lope pipe`. If they already know how to pipe, let them run it themselves.

2. Run:

```bash
<source_command> | lope pipe
```

Optional flags:
- `--require-all` — exit non-zero if ANY validator errors (default: continue).
- `--validators claude,gemini` — restrict the pool.
- `--timeout 60` — per-validator timeout.
- `--json` — machine-readable output.

3. Read the output. `━━━ <name> ━━━` per validator. Errors show per-section; run continues unless `--require-all` is set.

4. If the user's actual intent was a literal-string prompt (no pipe), use `/lope-ask` instead. If they wanted a file review, use `/lope-review`.

## When to use

Use `/lope-pipe` when the prompt is genuinely produced by another command — CI pipelines, `gh pr diff`, `git log`, `jq` chains. Do NOT use for literal prompts or single-file review.

## Cost

N validators × piped content tokens. Long diffs or logs multiply fast.
