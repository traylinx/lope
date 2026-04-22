---
name: lope-team
description: "Manage the validator team (add, list, remove, smoke-test) via CLI flags. Trigger on any user request about adding, removing, configuring, enabling, disabling, or testing a validator/teammate/CLI on lope — including 'add openclaw to lope', 'remove ollama from the team', 'list validators', 'is my new provider working', 'hook up my mistral pod', 'what CLIs are configured', OR the user pasting a curl command and saying 'add this'. Works for subprocess binaries (any local CLI), OpenAI-compatible HTTP endpoints (pods, gateways, cloud APIs), and pasted curl commands via `--from-curl`. The LLM translates natural language into the right invocation — the end user never edits JSON."
---

# Lope Team

Manage the lope validator roster without touching any config file. Every edit is one CLI call; the LLM running in the chat window is expected to translate the user's natural-language ask into the right invocation.

Lope has two kinds of teammate:

- **Subprocess**: any local AI CLI (ollama, an in-house binary, a wrapper script). Lope invokes it with argv or stdin. No network assumptions.
- **HTTP**: any OpenAI-compatible REST endpoint (cloud APIs, self-hosted gateways, Tytus-style private pods). Lope POSTs the prompt and reads back a dot-pathed JSON field.

Three ways to register an HTTP teammate, easiest-first:

1. **Paste a curl** — `--from-curl "<entire curl command>"`. Lope parses URL, headers, body; auto-injects `{prompt}`; infers `response_path`. Use this whenever the user pastes a curl block from API docs. Zero flag memorization required.
2. **Flag form** — `--url URL --model MODEL --key-env ENV_VAR`. Use when the user describes the API but doesn't have a curl handy.
3. **Config JSON** — hand-edit `~/.lope/config.json`. Reserve for advanced users. Rarely needed.

Grandma-friendly rule: if the user **pastes a curl** → `--from-curl`. If they give you a **command or binary name** → `--cmd`. If they describe a **URL + model + key** → `--url ...`.

## When to trigger

The user wants to change who is on the lope team. Patterns:

| User says | You invoke |
|---|---|
| *(user pastes a curl block)* "add this to lope as openai" | `lope team add openai --from-curl "<paste the whole curl>"` |
| *(user pastes curl with a literal API key)* "add this" | `lope team add <name> --from-curl "..." --key-env <SUGGESTED_ENV>` — pick env name from hostname (OpenAI → `OPENAI_API_KEY`, Anthropic → `ANTHROPIC_API_KEY`, etc.). Always prefer this to editing the curl. |
| "Add openclaw to lope" (they mention a URL + model + key) | `lope team add openclaw --url <URL> --model <MODEL> --key-env <ENV_VAR>` |
| "Add my local ollama with qwen3:8b" | `lope team add my-ollama --cmd "ollama run qwen3:8b {prompt}"` |
| "Hook up my Tytus pod as a teammate" | `lope team add tytus-pod --url $OPENAI_BASE_URL/chat/completions --model <MODEL> --key-env OPENAI_API_KEY` |
| "Remove codex from the team" | `lope team remove codex` |
| "List the lope validators" / "who's on lope" | `lope team list` |
| "Is my new mistral teammate working?" | `lope team test mistral "Say hello in one word."` |
| "Make openclaw the primary" | `lope team add openclaw --url ... --force --primary` (re-add with `--primary`) |
| "Disable ollama for now but keep the config" | `lope team remove ollama` (simplest) — or re-add another with `--disabled` |

## When NOT to trigger

- User asks "what does lope do?" → use `using-lope` or the help skill, not team.
- User is running a sprint or asking a question → use the relevant lope verb (negotiate/execute/audit/ask/review/vote/compare/pipe).
- User wants to change a *flag* on the current run (timeout, validators override) → use `--timeout` / `--validators` on the verb itself, not team.

## Verb shape

```bash
lope team                         # == `lope team list`
lope team list                    # show active + disabled + source tags
lope team add NAME [flags]        # upsert + enable
lope team add NAME --from-curl "<curl>"    # paste curl, zero flag memorization
lope team remove NAME             # drop from providers, validators, and primary
lope team test NAME [PROMPT]      # call generate() once, print answer
```

## Add — paste a curl (easiest)

When the user pastes a curl command (or says *"here's the quickstart curl from the docs, add it"*), use `--from-curl`:

```bash
lope team add <name> --from-curl "<paste the entire curl>"
```

What lope does automatically:

- Extracts the URL, HTTP method, headers, and body from the curl.
- Auto-injects `{prompt}` into the user-content field — OpenAI-style `messages[].content`, Anthropic's messages shape, top-level `prompt`/`input`/`message`/`query`/`text`. Multi-turn history is preserved (only the **last** `role=user` message is swapped).
- Infers `response_path`: Anthropic URLs or `anthropic-version` header → `content.0.text`; Cohere URL → `text`; everything else → OpenAI-compatible `choices.0.message.content`.
- Trusts `${VAR}` inside the pasted curl (e.g. `Bearer ${OPENAI_API_KEY}`) as a templated credential — kept as-is.
- **Refuses to save a literal API key.** If the curl has `Bearer sk-abc123...` inline, lope errors with a hostname-derived env name suggestion (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, etc.). Two fixes: (a) re-paste with `${OPENAI_API_KEY}` in the curl, or (b) pass `--key-env OPENAI_API_KEY` and lope swaps the literal for you.

Example — OpenAI:

```bash
lope team add openai --from-curl "curl https://api.openai.com/v1/chat/completions \
  -H 'Authorization: Bearer \${OPENAI_API_KEY}' \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"
```

Example — Anthropic (custom auth header + non-default response path, inferred):

```bash
lope team add anthropic --from-curl "curl https://api.anthropic.com/v1/messages \
  -H 'x-api-key: \${ANTHROPIC_API_KEY}' \
  -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"claude-sonnet-4-5\",\"max_tokens\":4096,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"
```

Example — user pasted a curl with a literal key (most common):

```
User pastes:
  curl https://api.groq.com/openai/v1/chat/completions \
    -H 'Authorization: Bearer gsk_ABCDEFG1234' \
    -d '{"model":"llama-3.3-70b-versatile","messages":[{"role":"user","content":"hi"}]}'

You run:
  lope team add groq --from-curl "curl ... 'Bearer gsk_ABCDEFG1234' ..." --key-env GROQ_API_KEY
```

**Unsupported curl shapes** (lope refuses with a clear message, tell the user which to fix):

- `-u user:pass` basic auth → pre-encode with `echo -n user:pass | base64` and use a templated `Authorization: Basic ${VAR}` header instead.
- `-F / --form` multipart → not supported; only JSON bodies work with chat endpoints.
- `-d @file` or `--data-binary @file` → paste the body contents inline instead of the file reference.
- `-X GET` → chat endpoints are POST-only.

**Body-shape edge case:** if lope can't auto-detect where `{prompt}` goes (exotic body shape), it errors and tells the user to either put `{prompt}` in the body before pasting or use `--body-json` directly.

## Add — subprocess

```bash
lope team add <name> --cmd "binary [args...] [{prompt}]"
lope team add <name> --cmd "binary [args...]" --stdin    # feed via stdin
lope team add <name> --cmd "..." --wrap "Be terse: {prompt}"
lope team add <name> --cmd "..." --timeout 300
```

- `{prompt}` is a placeholder. If absent and `--stdin` is off, it's auto-appended as the last argv token — so `--cmd "mybin --json"` works without the user having to think about placeholders.
- Quoted args in `--cmd` are parsed via `shlex.split`.
- No shell is ever invoked — arg injection is impossible by design.

Example: hook up a local Hermes (Nous Research) binary:

```bash
lope team add hermes --cmd "hermes chat --json --prompt {prompt}" --timeout 180
```

## Add — HTTP (OpenAI-compatible)

```bash
lope team add <name> --url <URL> --model <MODEL> [--key-env <ENV_VAR>]
```

Defaults that match 95% of APIs:
- Body: `{"model": "<model>", "messages": [{"role": "user", "content": "{prompt}"}]}`
- Response path: `choices.0.message.content`
- Auth header: `Authorization: Bearer ${ENV_VAR}`

Examples:

```bash
# Sebastian's Tytus private pod
lope team add tytus --url http://10.42.42.1:18080/v1/chat/completions \
    --model ail-compound --key-env OPENAI_API_KEY

# Any OpenAI-compatible gateway (Together, Groq, Deepinfra, etc.)
lope team add groq --url https://api.groq.com/openai/v1/chat/completions \
    --model llama-3.3-70b --key-env GROQ_API_KEY

# Custom auth header + no prefix
lope team add anthropic-raw --url https://api.anthropic.com/v1/messages \
    --model claude-opus-4-7 --key-env ANTHROPIC_API_KEY \
    --key-header "x-api-key" --key-prefix ""
```

### Non-OpenAI shapes

If the endpoint is NOT OpenAI-compatible, use `--body-json` to supply a custom payload + `--response-path` to walk into the response:

```bash
lope team add cohere --url https://api.cohere.ai/v1/chat --key-env COHERE_API_KEY \
    --body-json '{"message": "{prompt}", "model": "command-r-plus"}' \
    --response-path "text"
```

`{prompt}` inside `--body-json` is substituted at call time. `${VAR}` is substituted at call time in headers and body (not in URL or command — prevents API keys from leaking into `ps`, shell history, or server logs).

## Remove

```bash
lope team remove <name>
```

Idempotent-ish: removes from `providers`, removes from `validators`, and if the removed name was `primary` it falls back to the first remaining validator (or empty if none left). Exits non-zero only if the name is not on the team at all.

## Test

```bash
lope team test <name>                     # default prompt: "Say hello in one word."
lope team test <name> "your custom prompt"
lope team test <name> --timeout 120
```

Runs `validator.generate()` (the same codepath `ask`/`review`/`vote` use) and prints the raw response. Useful to confirm API keys, URLs, and binary paths before relying on the teammate in a real sprint.

## Flags reference

`lope team add`:

| Flag | Meaning |
|---|---|
| `--from-curl "<curl>"` | **Paste a full curl command.** Auto-extracts URL, headers, body; auto-injects `{prompt}`; infers `response_path`. Mutually exclusive with `--cmd`, `--url`, and `--body-json`. |
| `--cmd "..."` | Subprocess command. `{prompt}` substitutes as an argv token. |
| `--stdin` | Pipe prompt via stdin instead of argv. |
| `--url URL` | HTTP endpoint. Implies HTTP type. |
| `--model NAME` | Model field in OpenAI-shape body (required unless `--body-json`). |
| `--key-env VAR` | Env var holding the API key. Stored as `${VAR}` — expanded at call time. With `--from-curl`, also swaps a literal credential found in the pasted curl. |
| `--key-header HEADER` | Auth header name (default: `Authorization`). Used with `--url`, not with `--from-curl` (the curl already specifies the header). |
| `--key-prefix PREFIX` | Auth token prefix (default: `Bearer `). Same scope as `--key-header`. |
| `--response-path PATH` | JSON dot-path (default: `choices.0.message.content`). Overrides `--from-curl` auto-detection. |
| `--body-json JSON` | Raw JSON body — replaces the OpenAI-compatible shape. Mutex with `--from-curl`. |
| `--wrap TEMPLATE` | Prompt wrapper, e.g. `"Respond concisely: {prompt}"`. |
| `--timeout SECS` | Per-call timeout override. |
| `--primary` | Make this the primary validator. |
| `--disabled` | Save provider but don't add to active validators yet. |
| `--force` | Overwrite an existing provider with the same name. |

## Hard rules

- **Don't invent providers.** If the user hasn't given you a binary path or URL, ask — don't fabricate `openclaw` paths or URLs. Wrong config is worse than no config.
- **API keys never in argv or URL.** `${VAR}` substitution works in headers and body only. This is enforced by `_validate_provider_config` — a bad config fails fast.
- **Built-in names are reserved.** `claude`, `opencode`, `gemini`, `codex`, `aider` can't be shadowed. Pick a different name if the user's custom wrapper happens to share one.
- **`--disabled` is rare.** Default behavior is "add = enable". Only pass `--disabled` if the user explicitly says "save it but don't use it yet".
- **Smoke-test after add.** Always tell the user to run `lope team test <name>` after a successful add — that's how they confirm the key/URL/binary actually works.
