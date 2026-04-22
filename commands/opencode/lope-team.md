---
name: lope-team
description: Manage the lope validator team — add, list, remove, or smoke-test teammates. Subprocess binaries and OpenAI-compatible HTTP endpoints both supported. Translate natural-language asks ("add openclaw to lope", "remove codex", "list validators", "test if mistral works") into the right `lope team` call.
agent: build
---

# Lope team

Manage the lope validator roster from a chat window. No JSON editing — translate the user's natural language into a single `lope team` invocation.

## What to do

1. **Figure out the verb**: `list` (default), `add NAME [flags]`, `remove NAME`, or `test NAME [PROMPT]`.

2. **For `add`, pick the path in this order**:
   - **User pasted a curl block** → `--from-curl "<entire curl>"`. This is the easiest path — no flag memorization.
   - **Binary or CLI command mentioned** → `--cmd "binary --flag {prompt}"` (subprocess).
   - **URL + model + key described in prose** → `--url <URL> --model <MODEL> --key-env <ENV_VAR>` (HTTP).
   - If the user hasn't given you a curl, URL, binary, or env var name, **ask** — don't invent.

3. **Run the command**:

```bash
# List
lope team

# Add by pasting a curl (easiest — works with API docs quickstarts)
lope team add openai --from-curl "curl https://api.openai.com/v1/chat/completions \
  -H 'Authorization: Bearer \${OPENAI_API_KEY}' \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"

# If the pasted curl has a literal key, add --key-env so lope swaps it
lope team add openai --from-curl "curl ... 'Bearer sk-proj-...' ..." --key-env OPENAI_API_KEY

# Add subprocess (any local CLI)
lope team add my-ollama --cmd "ollama run qwen3:8b {prompt}"

# Add HTTP (OpenAI-compatible, flag form)
lope team add openclaw --url http://10.42.42.1:18080/v1/chat/completions \
    --model openclaw --key-env OPENAI_API_KEY

# Add HTTP (custom body shape)
lope team add cohere --url https://api.cohere.ai/v1/chat --key-env COHERE_API_KEY \
    --body-json '{"message":"{prompt}","model":"command-r-plus"}' \
    --response-path "text"

# Flags
--primary      # make this the primary validator
--disabled     # save config but don't add to active validators yet
--force        # overwrite an existing provider of the same name
--wrap TMPL    # prompt wrapper, e.g. "Be terse: {prompt}"
--timeout N    # per-call timeout in seconds

# Remove
lope team remove <name>

# Smoke-test
lope team test <name>
lope team test <name> "Your custom prompt."
```

4. **After every successful add, tell the user to test**: `lope team test <name>`. That's how they confirm the key/URL/binary works. If the test fails, which flag to fix.

## Hard rules

- Built-in names (`claude`, `opencode`, `gemini`, `codex`, `aider`) cannot be shadowed. Pick a different name for the user's custom wrapper.
- API keys are **always** stored as `${ENV_VAR}` via `--key-env`. Never put a raw API key on the command line — it leaks into shell history.
- `{prompt}` substitution works in `--cmd`, `--body-json`, and `--wrap`. `${VAR}` substitution works in headers and body (not URL or command).
- If the user's wording is ambiguous, ask before inventing.

## When NOT to trigger

- User asks "what does lope do" → use the help skill, not team.
- User wants to run a sprint or ask a question → use the matching lope verb.
- User wants to tweak a single run (`--validators` override) → pass that flag to the verb itself, not via `team`.
