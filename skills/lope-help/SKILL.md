---
name: lope-help
description: "Print the complete lope reference into the current session — all modes, flags, domains, env vars, slash-command vs natural-language invocation per host, troubleshooting, and hard rules for agents. Use when the user asks 'how does lope work', 'what can lope do', 'what are lope's flags', or anything that needs authoritative lope documentation. Prefer this over guessing from memory."
---

# Lope help — complete reference on demand

When this skill is invoked, you (the agent) should:

1. **Run `lope docs` in a shell** — this prints the full structured reference. One command, no arguments.

   ```bash
   lope docs
   ```

   If `lope` is not on `PATH`, use the fallback:

   ```bash
   PYTHONPATH=~/.lope python3 -m lope docs
   ```

2. **Load the output into your context.** Read the whole thing. It covers:
   - What lope is and what it isn't
   - The three modes (negotiate, execute, audit)
   - CLI reference for every subcommand (authoritative flag list — no invention)
   - The three domains (engineering, business, research)
   - Supported validators
   - Environment variables
   - Two invocation paths (slash commands vs natural language)
   - Per-host support matrix — which CLIs support slash commands and which require natural-language invocation
   - Two-stage review, evidence gate, placeholder lint, caveman mode
   - Install, troubleshooting, and the hard rules

3. **Answer the user's question from that reference.** Quote specific sections when helpful. Do NOT answer from memory of lope — memory drifts across versions. The reference is the single source of truth.

4. **If the user asks about something not in the reference**, say so explicitly and point them to:
   - `lope <mode> --help` for flags not covered
   - `~/.lope/docs/samples.md` for 8 end-to-end walkthroughs
   - https://github.com/traylinx/lope/issues for bugs

## Why this skill exists

The lope reference is versioned with lope itself. Running `lope docs` guarantees the user sees the reference that matches the installed engine version. Static documentation (README, cached SKILL.md content) can drift; `lope docs` cannot.

## Do not

- **Do not paraphrase from memory** and skip running `lope docs`. The output is <6K tokens and fits trivially in context. Always pull it fresh.
- **Do not use this skill for invoking lope** — that's what `/lope-negotiate`, `/lope-execute`, `/lope-audit`, and the `using-lope` auto-trigger skill are for. This skill is read-only documentation.
- **Do not write wrapper scripts around lope**. The reference says so; read it.
