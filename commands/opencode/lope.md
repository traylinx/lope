---
name: lope
description: Lope — multi-CLI validator ensemble sprint runner. Umbrella command that explains the three modes (negotiate, execute, audit) and when to use each. Run lope-help for full reference.
agent: build
---

# Lope — umbrella

Lope is an autonomous sprint runner with a multi-CLI validator ensemble. Any AI CLI implements, any AI CLI validates, majority vote decides. Three modes:

- **/lope-negotiate** — draft a sprint doc with multi-round validator review
- **/lope-execute** — run sprint phases with validator-in-the-loop retry
- **/lope-audit** — generate the scorecard

For the complete reference (all flags, all domains, all env vars, troubleshooting, hard rules), run the `/lope-help` slash command in this session, or in a shell run:

```bash
lope docs
```

If `lope` is not on PATH, use `PYTHONPATH=~/.lope python3 -m lope docs`.

## When to use which mode

- User has a fresh idea and no sprint doc yet → `/lope-negotiate`
- User has a sprint doc and wants to run it → `/lope-execute`
- User has already run a sprint and wants the scorecard → `/lope-audit`
- User wants documentation → `/lope-help` or `lope docs`

## Natural language path

Most users won't type slash commands. They'll describe multi-phase work in prose — "plan the auth refactor", "scope the data migration", "negotiate the Q4 campaign carefully". Recognize those shapes and invoke `lope negotiate "<goal>" --domain <engineering|business|research>` directly in a shell.

## Hard rules

1. Do not invent flags. `lope negotiate` takes `--domain`, `--out`, `--max-rounds`, `--context`. That is the complete list.
2. Do not write a wrapper script around lope. Lope is already a CLI.
3. Run `lope docs` when you need the authoritative reference — never answer from memory.
