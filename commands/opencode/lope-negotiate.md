---
name: lope-negotiate
description: Draft a sprint doc via multi-round validator review. Primary CLI drafts, other CLIs review independently, majority vote decides. For multi-phase work in engineering, business, or research.
agent: build
---

# Lope negotiate

Draft a structured sprint doc for the user's goal via lope's negotiate mode. The primary CLI drafts, other CLIs independently review, iterates until majority consensus or escalation.

## What to do

1. Extract the sprint goal from the user's prose in one short sentence.
2. Pick the domain from the user's context:
   - `engineering` (default) — code, software, infra, devops
   - `business` — marketing, finance, ops, consulting, legal, teaching
   - `research` — studies, systematic reviews, academic work
3. Run lope negotiate in a shell:

```bash
lope negotiate "<goal>" --domain <engineering|business|research>
```

Optional flags: `--max-rounds N` (default 3), `--out <path>`, `--context "<additional context>"`.

4. After completion, read the generated sprint doc (path printed in stdout) and summarize the phases for the user.

## Run `lope docs` for the complete reference

Any flag you're unsure about, run `lope docs` (or `lope negotiate --help`) first. Do not invent flags — the complete list is: `--out`, `--max-rounds`, `--context`, `--domain`. There is no `--host`, no `--title`, no `--validators`.

## Do not

- Do not write a wrapper script around lope. Lope is already a CLI — invoke it directly.
- Do not trigger on single-edit tasks, typo fixes, or pure Q&A.
- Do not pass user prose verbatim as the goal — distill it to one clear sentence first.
