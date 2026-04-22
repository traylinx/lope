---
name: lope-compare
description: A/B compare two files across every validator against explicit --criteria. Each picks A or B. Tally; print winner. For bake-offs, before/after review, migration decisions.
agent: build
---

# Lope compare

Two files + explicit criteria → each validator picks the better one → tally → winner announced.

## What to do

1. Extract the two file paths. If the user hinted at criteria (security, performance, readability), pass `--criteria` explicitly. Default is "correctness and clarity".

2. Run:

```bash
lope compare "<file_a>" "<file_b>" --criteria "<dimensions>"
```

Optional flags:
- `--validators claude,opencode` — restrict the pool.
- `--timeout 120` — larger files need larger timeouts.
- `--json` — machine-readable output.

3. Read the output. Each validator picks A or B; tally shows counts; winner names the actual file path.

4. If validators split, call it out. Explain the dissent when obvious.

## When to use

Use `/lope-compare` when the user has two concrete files and wants cross-model A/B. Do NOT use for one-file review (that's `/lope-review`) or 3+ options (that's `/lope-vote`).

## Cost

Both full file contents × N validators. Large files multiply fast.
