---
name: lope-pipe
description: "Read stdin as the prompt, fan out to every configured validator, print per-model answers. The composable shell verb — anything that produces text on stdout can feed lope in a pipeline. Per-validator isolation by default: one timeout doesn't kill the run. Pass --require-all for strict exit-non-zero-on-any-error semantics."
---

# Lope Pipe

Read the prompt from stdin. Fan out to every validator in parallel. Print N responses with per-model section headers. Same output shape as `/lope-ask`, different input source.

This is the composable verb — designed for shell pipelines where the prompt comes from another command's stdout (`cat`, `gh pr diff`, `git log`, `jq`, `curl`, anything).

## Invocation

Two paths:

1. **Explicit slash command.** User types something like `/lope-pipe < plan.md` or uses it from a shell directly: `cat plan.md | lope pipe`. Route accordingly.
2. **Natural language.** User says:
   - "Pipe this output into all the CLIs"
   - "Send the diff to every model"
   - "Fan out this log to lope"

   Infer the source and the pipe shape. Prefer running the shell command directly when the user gave you one explicit input (file, command output) rather than cat-ing it yourself.

## Command shape

```bash
cat plan.md | lope pipe                                  # fire-and-forget per-validator errors
gh pr diff | lope pipe --validators claude,gemini
jq '.' events.json | lope pipe --timeout 60
echo "review this" | lope pipe --json                    # structured output for scripts
cat file.txt | lope pipe --require-all                   # strict: exit 1 if any validator errors
```

## Output shape

Same as `/lope-ask` — one `━━━ <name> ━━━` section per validator. In `--json` mode, you get `[{"validator": ..., "answer": ..., "error": ...}]`.

## Partial-failure semantics

- **Default: fire-and-forget.** If gemini times out and claude succeeds, you see claude's answer and a `[ERROR]` section for gemini. Exit code 0. One slow CLI doesn't block the others.
- **`--require-all`: strict.** If any validator errors, exit code 1. Use for CI pipelines where you need assurance that every model saw the prompt.

This behaviour was pinned by pi's v0.5.0 design review — partial-failure is the right default because it preserves the signal from working validators; `--require-all` is there for when that's actually what the caller wants.

## Stdin validation

- If stdin is a TTY (no pipe), lope pipe exits 2 with a usage hint — you don't want it accidentally waiting for interactive input.
- If stdin is empty, exits 2. No empty prompts to the validators.

## When NOT to use pipe

- You have the prompt as a string literal, not piped from another command → `/lope-ask "<prompt>"`.
- You want to review a file → `/lope-review <file>` (better framing; includes a review prompt).
- You want a vote → `/lope-vote` or `/lope-compare` (structured, parsed output).

## Cost

N validators × prompt tokens. Piping large files or long diffs multiplies fast. Use `--validators X,Y` to cap.
