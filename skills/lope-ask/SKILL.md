---
name: lope-ask
description: "Ask every configured validator the same question and return N independent answers — one per model. No sprint framing, no phases, no verdict parsing. Use for any multi-perspective query: 'what do these 5 CLIs think about X?', 'compare how 3 models solve this problem', 'get a second opinion across models before I commit'. Works for any domain (engineering, research, writing, business)."
---

# Lope Ask

Fan out one question to every configured validator in parallel. Collect N raw answers. Print them side-by-side. No sprint, no phases, no majority vote.

This is the lightest lope surface — use it when the user wants multiple perspectives on a single prompt, not a structured multi-phase run.

## Invocation

Two paths — you must handle both:

1. **Explicit slash command.** User types `/lope-ask "What's the best way to index JSONL for fast lookup?"`. Route to `lope ask`.
2. **Natural language.** User says something like:
   - "Ask gemini, claude, and opencode what they think about X"
   - "What do the other models say about this?"
   - "Get me a second opinion across models"
   - "Let's check if all the CLIs agree on X before I commit"

   Recognize the shape and invoke `lope ask "<their question>"` on their behalf. Quote the question carefully.

## Command shape

```bash
lope ask "<question>"                   # fan out to all configured validators
lope ask "<question>" --context "<ctx>" # prepend context to every call
lope ask "<question>" --json            # machine-readable JSON output
lope ask "<question>" --validators claude,gemini  # override the pool
lope ask "<question>" --timeout 60      # per-validator timeout
```

## v0.7 superpowers (opt-in)

```bash
# Synthesis — primary rolls N answers into one executive summary
lope ask "Should we adopt X?" --synth
lope ask "Should we adopt X?" --synth --anonymous   # strip validator names

# Brain-aware ask (Makakoo OS only)
lope ask "What should we do next?" --brain-context "lope roadmap" --synth
lope ask "Is this still the plan?" --brain-context "auth decisions" --brain-log
```

`--synth` produces a 4-section executive summary (Consensus, Disagreements,
Highest-risk item, Recommended action) plus optional Follow-up questions.
`--anonymous` rewrites validator names to `Response A/B/C` so the synthesizer
cannot bias on identity.

## When to use `ask` vs `negotiate`

- **`ask`**: user has a single question or wants multi-model perspectives. Short-to-medium prompts. No structured deliverable expected.
- **`negotiate`**: user wants a *plan* — a sprint doc with phases, deliverables, and verdicts. Multiple rounds of revision expected.

If the user says "plan the X", "draft a sprint", or "negotiate" → `lope negotiate`. If the user says "ask", "what do they think", "check with the other CLIs" → `lope ask`.

## Output shape

Human-readable by default. Each validator's answer is wrapped in a `━━━ <name> ━━━` header:

```
━━━ claude ━━━
<claude's answer>

━━━ gemini ━━━
<gemini's answer>

━━━ opencode ━━━
<opencode's answer>
```

Pass `--json` for a JSON array of `{validator, answer, error}` objects — useful for piping into other tools or for agentic chains.

## Errors

If a validator times out or errors, its block shows `[ERROR] <message>` instead of the answer. The run continues — one slow/broken CLI does not blank the whole request. If every validator fails, exit code 1 and a "No validators available" hint.

## Cost awareness

`lope ask` sends the same prompt to N validators in parallel. Each call costs roughly what a single CLI call costs. If the user has 5 validators configured, expect 5× the tokens of a single call. For very long prompts or long context, pass `--validators claude,gemini` to cap the fan-out to a specific subset.
