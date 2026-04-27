---
name: lope-negotiate
description: "Draft a sprint doc via multi-round negotiation with AI validators. Lope sends your plan to other AI CLIs (Claude, Gemini, OpenCode, Codex, Mistral Vibe, Aider, Ollama, Goose, llama.cpp, Open Interpreter, Copilot, Amazon Q, or any HTTP API) for independent review. Majority vote. No single-model blindspot. Works for engineering, business (marketing, finance, ops), and research domains."
---

# Lope Negotiate

Lope uses independent AI validators to critique your sprint plan across multiple rounds until consensus. No single-model blindspot.

This skill is invoked **two ways**:

- **Explicit:** the user typed `/lope-negotiate "goal"` and expects you to run that exact goal
- **Implicit (via `using-lope` auto-trigger skill):** the user described multi-phase work in natural language and the `using-lope` skill mapped it to this command. In this case you construct the goal string yourself from the user's prose.

Either way, the flow below is the same.

## How it works

1. Drafter LLM (the primary validator in the pool) proposes a sprint doc
2. Validator pool (2+ different AI CLIs) independently reviews
3. On NEEDS_FIX: drafter refines with validator feedback
4. Repeats up to 3 rounds until PASS or escalation

## Steps

1. **Determine the goal.** If the user typed an explicit `/lope-negotiate "..."`, use that string. Otherwise extract the goal from the user's natural-language prompt — one sentence, imperative or noun-phrase form, specific enough that a drafter can scope it.
2. **Pick the domain:**
   - `engineering` (default) — code, software, technical work
   - `business` — marketing, finance, ops, management, consulting
   - `research` — studies, academic work, systematic reviews
3. Run negotiation:

```bash
# Engineering sprint (default)
PYTHONPATH=~/.lope python3 -m lope negotiate "$GOAL" --out "$SPRINT_PATH" --max-rounds 3

# Marketing / business sprint
PYTHONPATH=~/.lope python3 -m lope negotiate "Q2 product launch campaign" --domain business

# Research sprint
PYTHONPATH=~/.lope python3 -m lope negotiate "Systematic review of X" --domain research
```

4. Read and present the resulting sprint doc
5. If passed: ask if user wants to proceed to `/lope-execute`
6. If escalated: present validator feedback and discuss with user

## Examples by domain

**Engineering:**
- `/lope-negotiate "Add JWT auth middleware with refresh token rotation"`
- `/lope-negotiate "Migrate from REST to gRPC for internal services"`

**Business:**
- `/lope-negotiate "Q4 enterprise launch campaign" --domain business`
- `/lope-negotiate "Quarterly financial close process" --domain business`
- `/lope-negotiate "Digital transformation roadmap for retail client" --domain business`

**Research:**
- `/lope-negotiate "Systematic review of LLM alignment techniques" --domain research`
- `/lope-negotiate "Ethnographic study of remote team collaboration" --domain research`

## v0.7 superpowers (opt-in)

```bash
# Brain-aware negotiate (Makakoo OS only) — Brain context flows into every drafter+reviewer round
lope negotiate "Add JWT auth" --brain-context "prior auth incidents" --brain-log

# Export an AGTX task spec next to the sprint doc
lope negotiate "Add JWT auth" --export agtx
# → SPRINT-ADD-JWT-AUTH.md  +  SPRINT-ADD-JWT-AUTH.agtx.md
```

`--brain-context` exits 2 outside Makakoo with an actionable error — public
Lope still works without Makakoo OS. `--export agtx` is a deterministic text
transform; no AGTX dependency at runtime.

## Dynamic sprint mode

Treat the sprint as dynamic. If during work you discover a better approach or have an "aha" moment — do not silently expand scope and do not ignore it. Raise it with your lope teammates, negotiate whether it belongs, and fold it in if agreed.
