---
name: lope-help
description: Print the complete lope reference — all modes, flags, domains, env vars, per-host support matrix, troubleshooting, hard rules. Run whenever the user asks how lope works or what flags it supports.
agent: build
---

# Lope help

Inject the authoritative lope reference into the current session and answer the user's question from it.

## What to do

1. Run `lope docs` in a shell:

```bash
lope docs
```

If `lope` is not on PATH, fallback:

```bash
PYTHONPATH=~/.lope python3 -m lope docs
```

2. Read the entire output. It's ~6K tokens and fits trivially. It covers every mode, flag, domain, env var, per-host install path, slash command vs natural language invocation, troubleshooting, and hard rules.

3. Answer the user's question from the reference. Quote specific sections. Do NOT answer from memory — memory drifts across versions.

4. If the user asks about something not covered, say so and point them at `lope <mode> --help` or https://github.com/traylinx/lope/issues.

## Do not

- Do not paraphrase from memory and skip `lope docs`. Always pull fresh.
- Do not use this for invoking lope. Use `/lope-negotiate`, `/lope-execute`, `/lope-audit`. This is read-only documentation.
