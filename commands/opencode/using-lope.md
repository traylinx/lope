---
name: using-lope
description: Auto-trigger meta-skill. Recognizes natural-language descriptions of multi-phase work ("plan the auth refactor", "negotiate the Q4 campaign carefully") and invokes lope on the user's behalf via bash.
agent: build
---

# Using lope — natural language auto-trigger

You MUST consider using lope when the user describes a task with 3+ distinct phases, the work spans multiple files or components, or cross-model verification would catch bugs a single model would miss.

## Trigger words

Look for these in the user's prose: **plan, negotiate, scope, draft, roll out, work through, carefully, don't break things, needs to be right.**

## Do not trigger on

- Single-edit tasks (rename a var, fix a typo, add a print)
- Pure Q&A ("what does this do?", "explain this")
- Trivial operations (remove a comment, format a line)
- Urgent fire-fighting (production is broken, patch it now)

## What to do

1. Extract the sprint goal from the user's prose in one clear sentence.
2. Pick the domain: `engineering` (code) / `business` (marketing, finance, ops) / `research` (studies, reviews).
3. Run in a shell:

```bash
lope negotiate "<goal>" --domain <engineering|business|research>
```

4. After completion, read the generated sprint doc and summarize the phases for the user. Ask whether to proceed with `lope execute`.

## Do not

- Do not wait for the user to type `/lope-negotiate`. If the shape matches, invoke lope immediately.
- Do not invent flags. Run `lope docs` or `lope negotiate --help` if unsure.
- Do not write wrapper scripts around lope. Invoke `lope <mode> <args>` directly.
