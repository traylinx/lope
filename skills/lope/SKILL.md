---
name: lope
description: "Autonomous sprint runner with multi-CLI validator ensemble. Any AI CLI implements, any AI CLI validates, majority vote decides. Use for any multi-phase work: code sprints, marketing campaigns, research protocols, budgets, legal reviews. Three modes — negotiate a spec, execute phase by phase, audit the scorecard. 12 built-in CLI adapters plus infinite custom providers via JSON config."
---

# Lope — multi-CLI validator ensemble sprint runner

Lope is an autonomous sprint runner. One AI CLI implements. Others independently review. Majority vote decides if a phase ships. No single-model blindspot.

**Works for any domain:** engineering, business (marketing, finance, ops, consulting), research.

## The three modes

| Mode | Slash command | What it does |
|------|---------------|--------------|
| Negotiate | `/lope-negotiate` | Draft a structured sprint doc via multi-round validator review. Validators push back on scope creep, missing criteria, unverified assumptions, until majority consensus. |
| Execute | `/lope-execute` | Run sprint phases one at a time. After each phase, validators independently vote PASS / NEEDS_FIX / FAIL. NEEDS_FIX retries with specific fix instructions (3 attempts). |
| Audit | `/lope-audit` | Generate a scorecard from the executed sprint — per-phase verdicts, confidence scores, durations, overall status — and append to the journal. |

## How to pick which mode to run

- User has a fresh idea and no sprint doc yet → **`/lope-negotiate`**
- User has a sprint doc and wants to run it → **`/lope-execute`**
- User has already run a sprint and wants the scorecard → **`/lope-audit`**
- User just wants to know what validators lope found on their machine → `lope status` (plain shell)
- User wants to pick which validators to use → `lope configure` (plain shell)

Unless the user tells you otherwise, the default flow is: **negotiate → execute → audit**.

## The user probably won't type slash commands

Most users won't remember `/lope-negotiate` syntax. They'll just **describe what they want** in natural language — "plan the auth refactor", "negotiate the Q4 campaign carefully", "scope the data migration". Your job as the agent is to recognize those shapes and invoke lope on their behalf.

Trigger words: **plan, negotiate, scope, draft, roll out, work through**, or phrases like **"needs to be right"**, **"don't break things"**, **"let's be careful here"**.

When you recognize one of those + multi-phase work, construct the goal string from the user's prose and run `lope negotiate "<goal>" --domain <engineering|business|research>` directly. Do not wait for the user to type a slash command.

See `docs/samples.md` in the lope repo for 8 end-to-end conversation walkthroughs that show this pattern.

## Domains

Pass `--domain <name>` on negotiate to switch validator role, artifact labels, and review task.

| Domain | Use for | What it checks |
|--------|---------|----------------|
| `engineering` (default) | code, software, infra, devops | files, tests, acceptance criteria |
| `business` | marketing campaigns, budgets, ops, consulting, finance | deliverables, success metrics, stakeholder alignment |
| `research` | academic work, systematic reviews, studies | artifacts, validation criteria, methodology rigor |

## Examples

```bash
# Engineering
/lope-negotiate "Add JWT auth with refresh token rotation"

# Business
/lope-negotiate "Q2 product launch campaign for SaaS tier" --domain business

# Research
/lope-negotiate "Systematic review of LLM alignment techniques" --domain research
```

## Supported validators

12 built-in CLI adapters, auto-detected on `$PATH`:

Claude Code · OpenCode · Gemini CLI · Codex · Mistral Vibe · Aider · Ollama · Goose · Open Interpreter · llama.cpp · GitHub Copilot CLI · Amazon Q

Plus infinite custom providers via `~/.lope/config.json` (subprocess or HTTP). You need at least two different ones for the ensemble to have signal.

## Token efficiency — intelligent caveman mode

On by default. Validator prompts get compressed by dropping articles, filler, and hedging, while keeping code, paths, line numbers, and error messages exact. 50-65% token savings per validator call. Disable with `LOPE_CAVEMAN=off` for tasks that need full prose (external writing, papers).

## Dynamic sprint mode

Treat the sprint as dynamic. If during work you discover a better approach or have an "aha" moment, do not silently expand scope and do not ignore it. Raise it with your lope teammates, negotiate whether it belongs, and fold it in if agreed.

## When NOT to use lope

- One-off refactors that are a single edit → just do the edit
- Exploratory questions ("what could we do about X?") → have a conversation first, then lope the agreed plan
- Anything where the user has already named the exact files and lines — they want the change, not a sprint
- Urgent fire-fighting (production is broken, fix now) — patch the bug, don't negotiate a sprint

## Do not wrap lope

Lope is already a CLI. If the user says "use lope to do X", you invoke `lope <mode> <args>` in a shell. You do **not** write a Python wrapper script, a bash harness, or any other scaffolding that imports or calls lope. The whole point of the multi-CLI ensemble is that lope is the harness — you don't need another one. One command, no wrapper.

## Install / self-check

```bash
lope status      # what validators are detected?
lope configure   # pick which ones to use
lope version     # show version + lion-face banner
```

If `lope` isn't on PATH, prefix commands with `PYTHONPATH=~/.lope python3 -m lope` or tell the user to add `alias lope='PYTHONPATH=~/.lope python3 -m lope'` to their shell rc.
