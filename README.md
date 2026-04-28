# Lope

```
 ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
 ████████████████████████████████████████████████
                                                 
    ██      ██████    ██████   ███████           
    ██     ██◉  ◉██   ██   ██  ██                
    ██     ██ ▽▽ ██   ██████   █████             
    ██     ██ ◡◡ ██   ██       ██                
    ██████  ██████    ██       ███████           
                                                 
 ████████████████████████████████████████████████
 ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
     any cli implements  ·  any cli validates
```

**Multi-CLI validator ensemble for AI work.**

One AI CLI drafts. Others validate. No single-model blindspot. Works for multi-phase sprints (negotiate → execute → audit) **and** for single-shot multi-model tasks: ask a question to N CLIs, review a file across models, vote on options, A/B-compare two files, or pipe stdin to every validator. Add or remove teammates from any chat window — no JSON editing.

> **v0.7 — Superpowers.** Lope is now a **multi-agent judgment engine**. `lope review --consensus` merges, deduplicates, and consensus-ranks findings across N validators with SARIF export for CI. `--synth` rolls those findings into one executive summary. `lope memory` remembers recurring issues across sessions. `lope deliberate` runs Agent-Order-style councils on ADR / PRD / RFC / build-vs-buy / migration / incident decisions. `--brain-context` and `--brain-log` plug into Makakoo OS Brain. `--divide files` walks directories; `--divide hunks` reviews diffs; `--roles` runs the same artifact through security / performance / tests lenses. See **[v0.7 superpowers](#v07-superpowers)** below.

> **Not just for code.** Lope works for **engineering, business (marketing, finance, ops, consulting), and research (systematic reviews, protocols, academic work)**. The same validator loop that catches bugs in code also catches gaps in budgets, timeline assumptions, methodology rigor, and audience targeting. See [Use cases](#use-cases) for 9 worked examples across all three domains.

> Zero external dependencies. Pure Python stdlib. MIT license.

### v0.7 superpowers

```bash
# Consensus review — merge, dedupe, rank, export
lope review auth.py --consensus --synth --remember
lope review src/ --divide files --consensus --format sarif > review.sarif
lope review patch.diff --divide hunks --consensus --format markdown-pr
lope review auth.py --roles security,performance,tests --consensus

# Cross-session memory
lope memory stats
lope memory hotspots --days 30
lope memory search "rate limit"
lope memory file auth.py

# Makakoo Brain bridge (optional; activates only when MAKAKOO_BIN/MAKAKOO_HOME set)
lope review auth.py --consensus --brain-context "auth decisions" --brain-log
lope ask "What should we do next?" --brain-context "lope roadmap" --synth

# Council deliberation
lope deliberate adr scenario.md --depth quick
lope deliberate prd scenario.md --depth standard
lope deliberate build-vs-buy scenario.md --synth
```

Every v0.7 feature is **opt-in**. Default `lope review file.py` and friends behave exactly as v0.6.

---

### What it looks like

```
You (in Claude Code):    /lope-negotiate "Add JWT auth with refresh tokens"

  Round 1  drafter proposes sprint doc (4 phases)
  Round 1  opencode + vibe + gemini review... NEEDS_FIX (0.78)
           - Missing rate limiting on refresh endpoint
           - No test for token expiry edge case
  Round 2  drafter revises... applies both fixes
  Round 2  opencode + vibe + gemini review... PASS (0.93)

  Saved: SPRINT-JWT-AUTH.md

You:    /lope-execute SPRINT-JWT-AUTH.md

  Phase 1  scaffold ................ PASS  0.95  12s
  Phase 2  core-middleware ......... NEEDS_FIX  0.71
           - Token expiry check missing in middleware/auth.go:142
           → applying fix...
  Phase 2  re-validate ............. PASS  0.89  41s
  Phase 3  refresh-rotation ........ PASS  0.88  28s
  Phase 4  integration-tests ....... PASS  0.94  19s

  4/4 PASS  |  avg confidence 0.91  |  134s
```

---

## You don't have to type slash commands

Once lope is installed, your AI agent knows lope exists. You can just **talk to it** and it will invoke lope on your behalf when the request fits. No command memorization, no flag lookup.

```
You:    Let's plan the auth refactor with JWT refresh rotation. I want
        gemini and opencode to both sign off before I touch any code.

Agent:  [recognizes multi-phase work → invokes /lope-negotiate]
        Running: lope negotiate "JWT auth refactor with refresh rotation" \
                   --domain engineering --max-rounds 3

        Drafter: claude-code  ·  Reviewers: gemini-cli, opencode
        Round 1  drafter proposes sprint doc (3 phases)
        Round 1  gemini-cli + opencode review... NEEDS_FIX (0.74)
                 - Refresh endpoint missing rate limiting
                 - Phase 2 criteria are vague about refresh-token reuse
        Round 2  drafter revises
        Round 2  gemini-cli + opencode review... PASS (0.91)

        Saved: SPRINT-JWT-AUTH-REFRESH-ROTATION.md
```

**Natural language → lope invocation** examples:

| You say | Agent runs |
|---|---|
| "Draft 3 blog posts about lope for the launch" | `lope negotiate "Draft 3 launch blog posts" --domain business` |
| "Plan the Q4 marketing campaign carefully" | `lope negotiate "Q4 marketing campaign" --domain business` |
| "Scope the data ingestion rewrite" | `lope negotiate "Data ingestion pipeline rewrite" --domain engineering` |
| "Systematic review of post-training RL papers" | `lope negotiate "Systematic review of post-training RL" --domain research` |
| "What do gemini and claude say about this approach?" | `lope ask "<the question>"` |
| "Get a second opinion across models on X" | `lope ask "<question about X>"` |
| "Review auth.py across models for security" | `lope review auth.py --focus security` |
| "Multi-model review of my PR diff" | `lope review <diff path>` |
| "Yes/no from all the CLIs — is this safe?" | `lope vote "Is this safe?" --options yes,no` |
| "Pick 3.12 or 3.13 for the new project" | `lope vote "Python version" --options 3.12,3.13` |
| "Which file is better — old or new?" | `lope compare old.py new.py --criteria "correctness and readability"` |
| "Before/after bake-off for security" | `lope compare before.py after.py --criteria security` |
| "Pipe this diff into every model" | `gh pr diff \| lope pipe` |
| *(pastes a curl)* "add this to lope as openai" | `lope team add openai --from-curl "<paste>"` |
| "Add openclaw to lope using my Tytus pod" | `lope team add openclaw --url <URL> --model openclaw --key-env OPENAI_API_KEY` |
| "Add my local ollama (qwen3:8b) as a teammate" | `lope team add my-ollama --cmd "ollama run qwen3:8b {prompt}"` |
| "Remove codex from the team" | `lope team remove codex` |
| "Who's on lope?" / "list validators" | `lope team list` |
| "Test if my new mistral teammate works" | `lope team test mistral` |

The trigger words your agent watches for: **plan / negotiate / scope / draft / roll out** → `negotiate`; **ask / what do the CLIs think / second opinion** → `ask`; **review / critique / audit this file** → `review`; **yes-no / A-B-C / pick one** → `vote`; **which is better / compare / bake-off** → `compare`; **pipe / send output / `cmd | lope`** → `pipe`; **add / remove / list / test a validator** → `team`. The agent maps the shape of your request to the right verb without you having to remember slash syntax.

Explicit slash commands still work — `/lope-negotiate`, `/lope-execute`, `/lope-audit`, `/lope-ask`, `/lope-review`, `/lope-vote`, `/lope-compare`, `/lope-pipe`, `/lope-team` (Gemini uses `/lope:negotiate`, etc.). Natural language is the lazy path when you just want to do something multi-model.

### What happens under the hood

When you talk to your agent, the `using-lope` auto-trigger skill fires. It's a meta-skill installed alongside the explicit slash commands. Its job is to recognize the shape of your request and invoke the right lope mode for you. If the request is a single edit, a trivial fix, or pure conversation, `using-lope` **stays out of the way** — it's specifically scoped to multi-phase consequential work. You will not get a sprint negotiation for "rename this variable".

See [`skills/using-lope/SKILL.md`](skills/using-lope/SKILL.md) for the full trigger logic and anti-patterns.

---


### Objective evidence gates

Lope can now run project-defined evidence gates without becoming a code analyzer. Put deterministic checks in `./.lope/rules.json`:

```json
{
  "gates": [
    {"name": "tests", "cmd": "python -m pytest tests -q", "type": "exit"},
    {"name": "coverage", "cmd": "python -m coverage json -o -", "type": "json_number", "path": "totals.percent_covered", "min_delta": 0}
  ]
}
```

Then use them as a harness signal:

```bash
lope gate save
# ... agent changes code ...
lope gate check --json
lope execute SPRINT.md --gates
```

Gates are opt-in, stdlib-only, and command-based: tests, lint, typecheck, build, coverage, or custom scripts provide the evidence; Lope coordinates baselines, comparisons, retries, and memory.

## Install — paste one line into any AI agent

Open your AI agent (Claude Code, Codex, Cursor, Gemini CLI, OpenCode, GitHub Copilot CLI — whichever you already use) and paste this prompt:

```
Read https://raw.githubusercontent.com/traylinx/lope/main/INSTALL.md and follow the instructions to install lope on this machine natively.
```

That's it. Your agent fetches a single markdown file, follows six short steps, and reports back when lope is live. The install recipe is CLI-agnostic — it writes lope's slash commands into each host's **native** command directory using the format that host expects. Restart your CLI once to pick up the new slash commands.

**Requirements:** `git`, `python3 ≥ 3.9`, `bash ≥ 3.2`. That's all.

**What gets installed:**

| Host | Path | Format |
|---|---|---|
| Claude Code | `~/.claude/skills/lope*/` | skill dirs |
| Codex | `~/.codex/skills/lope*/` | skill dirs |
| Gemini CLI | `~/.gemini/commands/lope/*.toml` | TOML commands |
| OpenCode | `~/.config/opencode/commands/lope*.md` | flat markdown |
| Cursor | `~/.cursor/agents/lope*.md` | flat markdown |
| Mistral Vibe | `~/.vibe/skills/lope*/` | skill dirs |
| Qwen Code | `~/.qwen/skills/lope*/` | skill dirs |
| pi (Traylinx) | `~/.agents/skills/lope*/` | skill dirs (shared `@agents` tree) |

Hosts you don't have installed are skipped silently. Eight hosts are supported today.

### Manual install (for the 1% who prefer to read bash)

```bash
git clone --depth 1 https://github.com/traylinx/lope.git ~/.lope
~/.lope/install
```

Target a single host:

```bash
~/.lope/install --host codex
~/.lope/install --host gemini
```

Then add a shell alias so you can just type `lope`:

```bash
echo "alias lope='PYTHONPATH=~/.lope python3 -m lope'" >> ~/.zshrc
```

**Check what validators lope found on your machine:**

```bash
lope status
```

**Pick which ones to use:**

```bash
lope configure
```

---

## How it works

Lope has two shapes: **structured sprint mode** (negotiate → execute → audit, with phase retry) and **single-shot verbs** (ask, review, vote, compare, pipe — one prompt, N responses, done).

### Sprint mode — planned work with phase retries

```
  NEGOTIATE              VALIDATE              EXECUTE              AUDIT
  ─────────              ────────              ───────              ─────
  LLM drafts    ───>   Other CLIs    ───>   Phase by       ───>   Scorecard
  sprint doc           review & vote         phase with            + journal
                       (majority vote)       retry on
                                             NEEDS_FIX

                  <─── NEEDS_FIX ────┘
```

**Negotiate:** An LLM drafts a structured sprint doc (phases, goals, criteria). Validators push back on scope creep, missing edge cases, unverified assumptions. The LLM revises until PASS or max rounds.

**Execute:** Phase-by-phase implementation with validation after each phase. PASS advances. NEEDS_FIX retries with specific fix instructions (up to 3 attempts). FAIL escalates to you.

**Audit:** Scorecard with per-phase verdicts, confidence scores, duration, and overall status.

### Single-shot verbs — one prompt, N responses

```
  ASK / REVIEW / VOTE / COMPARE / PIPE
  ────────────────────────────────────
        ┌──────────────────────┐
  You ──>│ fan-out to every     │─────> N raw responses
         │ configured validator │        (one section per CLI)
        │ in parallel threads   │        or tally + winner (vote/compare)
        └──────────────────────┘
```

Each verb shares the same parallel fan-out primitive (`EnsemblePool.validate`). This fan-out already runs concurrently; v0.7 builds consensus and synthesis on top of it rather than adding parallelism from scratch. No sprint doc, no phase retries, no majority-vote on verdicts. You get each model's actual response; synthesis is your call (or optional with `--json`).

**Nine modes in total:** `negotiate`, `execute`, `audit`, `ask`, `review`, `vote`, `compare`, `pipe`, `team`.

---

## Supported validators

**Auto-detected built-in CLIs** — run `lope status` and lope finds whatever is on your PATH:

| CLI | Binary | Command |
|-----|--------|---------|
| Claude Code | `claude` | `claude --print` |
| OpenCode | `opencode` | `opencode run --format json` |
| Gemini CLI | `gemini` | `gemini --prompt` |
| Codex (OpenAI) | `codex` | `codex exec` |
| Mistral Vibe | `vibe` | `vibe run "{prompt}"` |
| Aider | `aider` | `aider --message --no-git --yes` |
| Ollama | `ollama` | local, zero auth |
| Goose (Block) | `goose` | `goose run --text` |
| Open Interpreter | `interpreter` | `interpreter --fast -y` |
| llama.cpp | `llama-cli` | fastest local inference |
| GitHub Copilot CLI | `gh copilot` | `gh copilot suggest` |
| Amazon Q | `q` | `q chat` |
| pi (Traylinx) | `pi` | `pi -p "{prompt}"` |
| Qwen Code | `qwen` | `qwen -p "{prompt}"` |

You need at least one. Install whatever you already use.

### Add any AI in 30 seconds — paste a curl

If the AI provider publishes a quickstart curl (every major one does), **paste it into lope**. Lope parses the URL, headers, and body; auto-injects `{prompt}` into the user-content field; and infers where the response lives. Zero flag memorization.

```bash
# Paste a curl straight from OpenAI's docs — done.
lope team add openai --from-curl "curl https://api.openai.com/v1/chat/completions \
  -H 'Authorization: Bearer \${OPENAI_API_KEY}' \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"

# Curl had the literal key? Let lope swap it for an env var reference.
lope team add groq --from-curl "curl https://api.groq.com/openai/v1/chat/completions \
  -H 'Authorization: Bearer gsk_RAW12345' \
  -d '{\"model\":\"llama-3.3-70b-versatile\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'" \
  --key-env GROQ_API_KEY

# Anthropic, Cohere, Together, Deepinfra, Tytus pods, vLLM servers, self-hosted gateways —
# same single-command paste-and-go. Response path is auto-inferred.

# Confirm it works
lope team test openai
```

**Safety guarantees**, enforced on every paste:

- Literal API keys in the pasted curl are **refused** unless you pass `--key-env` (lope then swaps them for `${VAR}` references). Keys never touch the config file in plaintext.
- `{prompt}` substitution is a real placeholder — never shell-interpolated. No injection vector.
- Unsupported shapes (`-u` basic auth, `-F` multipart, `@file` body, `-X GET`) are rejected with a clear fix.

### Add any CLI or HTTP API — flag form

Prefer describing the provider in flags (no curl handy)?

```bash
# Local CLI binary
lope team add my-ollama --cmd "ollama run qwen3:8b {prompt}"

# HTTP endpoint (OpenAI-compatible shape)
lope team add openclaw --url http://10.42.42.1:18080/v1/chat/completions \
    --model openclaw --key-env OPENAI_API_KEY

# Drop a teammate
lope team remove codex

# See who's on the team (active + disabled + source tag)
lope team list
```

Your agent recognizes natural language — **"add openclaw to lope", "here's a curl, add it", "remove codex from the team", "test if the new mistral works"** — and runs the right `lope team` invocation. Built-in names (`claude`, `opencode`, `gemini`, `codex`, `aider`) can't be shadowed. Full decision tree + all supported body shapes + unsupported-curl error recipes in [`skills/lope-team/SKILL.md`](skills/lope-team/SKILL.md).

### Add any CLI or HTTP API — via config (advanced)

Prefer editing JSON? `~/.lope/config.json`:

```json
{
    "version": 1,
    "validators": ["claude", "ollama-qwen"],
    "providers": [
        {
            "name": "ollama-qwen",
            "type": "subprocess",
            "command": ["ollama", "run", "qwen3:8b", "{prompt}"]
        }
    ]
}
```

Two provider types cover everything:

| Type | Use for |
|------|---------|
| `subprocess` | CLI tools — Ollama, Goose, llama.cpp, any binary |
| `http` | API endpoints — OpenAI, Anthropic, Groq, self-hosted |

HTTP example (Anthropic):

```json
{
    "name": "anthropic-api",
    "type": "http",
    "url": "https://api.anthropic.com/v1/messages",
    "headers": {
        "x-api-key": "${ANTHROPIC_API_KEY}",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    },
    "body": {
        "model": "claude-sonnet-4-5",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "{prompt}"}]
    },
    "response_path": "content.0.text"   // dot-path into the JSON response
}
```

**The only contract:** the response must contain a `---VERDICT---...---END---` block. Add a `prompt_wrapper` if the model needs explicit instructions:

```json
{
    "name": "my-llm",
    "prompt_wrapper": "Respond with a VERDICT block at the end:\n{prompt}",
    "type": "http",
    ...
}
```

**Security:** `subprocess` runs with `shell=False`. `${VAR}` is forbidden in command args and URLs (prevents key leakage into `ps` or server logs). HTTP body encoding prevents injection.

### Python API

```python
from lope import Negotiator, PhaseExecutor, Auditor, ValidatorPool
from lope.validators import ClaudeCodeValidator, OpencodeValidator

pool = ValidatorPool(
    validators=[ClaudeCodeValidator(), OpencodeValidator()],
    primary="claude",
)
```

---

## Slash commands

After install, these work in any supported CLI host (Gemini uses the `/lope:<verb>` namespaced form):

| Command | What it does |
|---------|-------------|
| `/lope-negotiate` | Draft a sprint doc with multi-round validator review |
| `/lope-execute` | Run sprint phases with validator-in-the-loop retry |
| `/lope-audit` | Generate scorecard from sprint results |
| `/lope-ask` | Fan out one question to every validator; collect N raw answers |
| `/lope-review` | Fan out a file review to every validator; collect N critiques |
| `/lope-vote` | Each validator picks from `--options`; tally + winner |
| `/lope-compare` | Each validator picks between two files given `--criteria`; tally + winner |
| `/lope-pipe` | Read stdin as the prompt; fan out; per-validator sections |
| `/lope-team` | Add / remove / list / test teammates — no JSON editing |
| `/lope-help` | Print the full reference into the current session |

---

## CLI reference

### `lope status`
Show detected CLIs and current config.

### `lope configure`
Interactive validator picker. Auto-detects installed CLIs.

### `lope negotiate <goal>`
```bash
lope negotiate "Add rate limiting to the API gateway" \
    --out SPRINT-RATE-LIMIT.md \
    --max-rounds 3 \
    --context "Express.js, Redis"
```

Pass `--domain business` or `--domain research` to switch the validator role and review criteria.

### `lope execute <sprint_doc>`
```bash
lope execute SPRINT-RATE-LIMIT.md
```

### `lope audit <sprint_doc>`
Generate scorecard. `--no-journal` skips writing to the journal file.

### `lope ask "<question>"`
Fan out one question to every configured validator; collect raw answers. No VERDICT parsing, no phase retry.

```bash
lope ask "What's the cleanest way to retry idempotently across models?"
lope ask "<q>" --validators claude,gemini   # restrict the pool
lope ask "<q>" --context "We use asyncio."  # shared context prepended
lope ask "<q>" --json                       # machine-readable
```

### `lope review <file>`
Send a file to every validator with a review prompt; collect N critiques.

```bash
lope review auth.py                                     # default review focus
lope review auth.py --focus security
lope review auth.py --focus "test coverage, edge cases"
lope review auth.py --validators claude,opencode
```

Focus text is injected explicitly into the prompt — "better" is never model-invented.

### `lope vote "<prompt>" --options A,B,C`
Each validator picks exactly one option label. Tally + winner.

```bash
lope vote "Should we ship today?" --options "ship,hold,escalate"
lope vote "Python 3.12 or 3.13?" --options 3.12,3.13
lope vote "<q>" --options "A,B,C" --json                # structured tally
```

Option parsing is whole-token strict: `A` won't match inside `ALGORITHM`. Longest-first resolution handles overlaps (`3.13` beats `3.1`).

### `lope compare <file_a> <file_b>`
Each validator picks which file wins against explicit `--criteria`.

```bash
lope compare old_auth.py new_auth.py
lope compare before.md after.md --criteria security
lope compare a.py b.py --criteria "correctness, performance, ergonomics"
```

Default criteria: `"correctness and clarity"`. Criteria are named explicitly in every validator's prompt so the comparison dimensions are never model-invented.

### `lope pipe`
Read stdin as the prompt; fan out; per-validator sections.

```bash
cat plan.md | lope pipe
gh pr diff | lope pipe --validators claude,gemini
jq '.' events.json | lope pipe --timeout 60
echo "<prompt>" | lope pipe --require-all       # exit 1 if any validator errors
```

Default is per-validator isolation — one timeout doesn't kill the others. `--require-all` opts in to strict failure for CI.

### `lope team`
Manage the validator roster from a chat window — no JSON editing required.

```bash
# List current team (active + disabled + source tags: built-in / custom / auto)
lope team
lope team list

# Add by pasting a curl (easiest — works with any provider's quickstart)
lope team add openai --from-curl "curl https://api.openai.com/v1/chat/completions \
  -H 'Authorization: Bearer \${OPENAI_API_KEY}' \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"

# Paste a curl that has a literal API key — --key-env swaps it for ${VAR}
lope team add groq --from-curl "curl ... -H 'Authorization: Bearer gsk_RAW123' ..." \
  --key-env GROQ_API_KEY

# Add a local CLI binary (subprocess)
lope team add my-ollama --cmd "ollama run qwen3:8b {prompt}"
lope team add hermes --cmd "hermes chat --json --prompt {prompt}" --timeout 180

# Add an HTTP endpoint via flags (no curl handy — OpenAI-compatible shape)
lope team add openclaw --url http://10.42.42.1:18080/v1/chat/completions \
    --model openclaw --key-env OPENAI_API_KEY

# Custom HTTP body shape (non-OpenAI)
lope team add cohere --url https://api.cohere.ai/v1/chat --key-env COHERE_API_KEY \
    --body-json '{"message":"{prompt}","model":"command-r-plus"}' --response-path "text"

# Make a teammate the primary / save-but-disabled / overwrite
lope team add openclaw --url ... --primary
lope team add openclaw --url ... --disabled
lope team add openclaw --url ... --force

# Remove
lope team remove codex

# Smoke-test
lope team test openclaw
lope team test openclaw "What's 2+2?" --timeout 120
```

**Safety:** `{prompt}` is a real placeholder — never shell-interpolated. API keys live as `${ENV_VAR}` references, expanded only at call time (they never land in argv, URLs, or config files). Literal credentials inside `--from-curl` are refused unless you pass `--key-env` (lope swaps them). Built-in validator names (`claude`, `opencode`, `gemini`, `codex`, `aider`) can't be shadowed.

**Natural language works too** — if your AI CLI is loaded with the `lope-team` skill, say *"here's a curl from OpenAI's docs, add it to lope"* or *"add openclaw to lope with my Tytus pod"* and the agent runs the right invocation.

---

## Configuration

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOPE_HOME` | `~/.lope` | Config and journal directory |
| `LOPE_WORKDIR` | Current directory | Working directory for validators |
| `LOPE_TIMEOUT` | `480` | Validator timeout (seconds) |
| `LOPE_CAVEMAN` | `full` | Token compression: `full`, `lite`, or `off` |
| `LOPE_LLM_URL` | _(unset)_ | **Optional fallback** — hosted OpenAI-compatible endpoint, used only if the primary validator does not support drafting. Normally you do not need this. |
| `LOPE_LLM_MODEL` | `gpt-4o-mini` | Model name when `LOPE_LLM_URL` fallback is used. |
| `LOPE_LLM_API_KEY` | _(unset)_ | Bearer token for the fallback endpoint. Falls back to `OPENAI_API_KEY`. |
| `LOPE_RUN_LOCK` | _(on)_ | Set to `off` to disable the run lock (CI, deliberate parallelism). |
| `LOPE_RUN_LOCK_WAIT` | _(unset)_ | Seconds to block when another lope run holds the lock. `0` = wait forever. Default: fail fast. |
| `LOPE_RUN_LOCK_PATH` | `$LOPE_HOME/run.lock` | Override the lockfile path (used by tests). |

> **No separate LLM required.** Lope's premise is *any CLI implements, any CLI validates*. Drafting is just the primary CLI implementing. `lope negotiate` calls the primary validator (`claude`, `opencode`, `gemini-cli`, `codex`, or `aider`) as a subprocess to draft the sprint doc, then routes the draft to the other validators for review. You only need to set `LOPE_LLM_URL` if your primary validator cannot draft (e.g. a custom HTTP provider that only reviews).

### Ensemble vs. fallback

**Ensemble** (`parallel: true`, default): all validators run concurrently. Majority vote. Any FAIL vetoes. Ties resolve to NEEDS_FIX.

**Fallback** (`parallel: false`): primary first, next on infra error. First PASS/NEEDS_FIX/FAIL halts chain.

### Run lock (concurrent invocation safety)

Lope holds a file lock (`$LOPE_HOME/run.lock`) for the lifetime of every `negotiate` and `execute` command. Without it, two parallel runs each spawn 3–4 validator CLIs, fight over the same auth tokens, and stall out with fake `INFRA_ERROR` timeouts.

Default behavior: a second caller fails fast with exit 75 (`EX_TEMPFAIL`) and a clear message showing the holder's pid and command.

```bash
# Queue the second caller instead of failing — block up to 5 minutes
LOPE_RUN_LOCK_WAIT=300 lope negotiate "second goal"

# Disable the lock entirely (CI, tests, deliberate parallelism)
LOPE_RUN_LOCK=off lope execute SPRINT.md
```

Read-only commands (`status`, `configure`, `audit`, `docs`, `version`, `install`) do not touch the lock.

### Token compression (caveman mode)

By default, lope tells validators to respond in terse fragments — drop articles, filler, hedging. Code, paths, line numbers, and error messages stay exact. This cuts validator response tokens by 50-65%, which matters when you're running N validators × M phases × up to 3 retries per sprint.

```bash
LOPE_CAVEMAN=off lope negotiate "..."   # verbose responses
LOPE_CAVEMAN=lite lope negotiate "..."  # drops filler only, keeps full sentences
```

Adapted from [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) (MIT).

---

## VERDICT format

Every validator response must contain:

```
---VERDICT---
status: PASS | NEEDS_FIX | FAIL
confidence: 0.0-1.0
rationale: 1-3 sentences
required_fixes:
  - fix 1
  - fix 2
---END---
```

`confidence < 0.7` on a PASS is automatically demoted to NEEDS_FIX. Missing or malformed block → `INFRA_ERROR` (never raises, falls through to next validator).

---

## Use cases

Lope is **not just for code**. The `--domain` flag switches the validator role, artifact labels, and review task for the context you're working in:

- `engineering` (default) — code, software, infra, devops
- `business` — marketing, finance, ops, consulting, management
- `research` — studies, systematic reviews, academic work

Nine worked examples below. Each is a real sprint goal you can paste into `lope negotiate`.

### 💻 Software engineering

```bash
lope negotiate "Add JWT auth with refresh token rotation" --domain engineering
lope negotiate "Migrate from REST to gRPC for internal services" --domain engineering
lope negotiate "Rate-limit the public API gateway — per-user + per-IP" --domain engineering
```

Validators check: file paths, test coverage, edge cases, error handling, backward compatibility, rollback plan.

### 📢 Marketing & campaigns

```bash
lope negotiate "Q4 product launch campaign for SaaS enterprise tier" --domain business
lope negotiate "LinkedIn thought leadership sequence — 8 posts over 4 weeks" --domain business
lope negotiate "Rebranding sprint: logo, site, positioning, migration" --domain business
```

Validators check: target audience, message-market fit, channel mix, success metrics, timeline realism, budget allocation.

### 💰 Finance & accounting

```bash
lope negotiate "Q2 budget rebuild across 4 cost centers with runway analysis" --domain business
lope negotiate "Month-end close process redesign — target 3-day close" --domain business
lope negotiate "R&D tax credit claim for FY2026 — scoping + documentation" --domain business
```

Validators check: reconciliation gaps, audit trail, control points, compliance, variance analysis, stakeholder sign-offs.

### 🎯 Management & operations

```bash
lope negotiate "Reorg engineering into 3 squads with clear ownership boundaries" --domain business
lope negotiate "Onboarding overhaul — first 90 days for new hires" --domain business
lope negotiate "Q1 OKR planning across 5 teams with cross-team dependencies" --domain business
```

Validators check: dependencies, stakeholder coverage, rollout risk, rollback plan, success metrics, communication plan.

### 🔬 Research & academic

```bash
lope negotiate "Systematic review of post-training RL techniques for small LMs" --domain research
lope negotiate "Ethnographic study of remote team collaboration — 12 week protocol" --domain research
lope negotiate "Replication study: attention-head pruning claims in recent paper" --domain research
```

Validators check: methodology rigor, sampling bias, reproducibility, ethical considerations, pre-registration, data management plan.

### 🏢 Consulting engagements

```bash
lope negotiate "Digital transformation scoping for retail client — 6 week discovery" --domain business
lope negotiate "Technology due diligence for $50M acquisition — 10 day turnaround" --domain business
lope negotiate "Strategic roadmap for CTO — 18 month technical strategy" --domain business
```

Validators check: client success criteria, stakeholder mapping, deliverable quality, timeline realism, scope boundaries.

### ⚖️ Legal & compliance

```bash
lope negotiate "GDPR compliance audit for data pipeline — retention, SAR, deletion" --domain business
lope negotiate "SOC 2 Type II readiness sprint — 12 week preparation" --domain business
lope negotiate "Employee handbook redesign — remote-first policies" --domain business
```

Validators check: regulatory coverage, risk assessment, control mapping, documentation completeness, evidence of enforcement.

### 🎓 Teaching & mentorship

```bash
lope negotiate "Bootcamp curriculum redesign — full-stack 16 weeks" --domain business
lope negotiate "Internal engineering onboarding — first 30 days" --domain business
lope negotiate "Workshop: shipping your first LLM-powered feature — 3 hours" --domain business
```

Validators check: learning objectives, progression, assessment, practical exercises, prerequisites, time budgets.

### 🚀 DevOps & CI/CD

```bash
lope negotiate "Migrate from Jenkins to GitHub Actions across 12 repos" --domain engineering
lope negotiate "Zero-downtime database migration from Postgres 13 to 16" --domain engineering
lope negotiate "Kubernetes cluster hardening — secrets, RBAC, network policies" --domain engineering
```

Validators check: blast radius, rollback plan, monitoring coverage, alerting, runbook completeness.

### Why the same loop works for code and non-code

The validator ensemble doesn't care whether it's reviewing a Python diff or a Q4 marketing brief. What it cares about is: is the plan specific, does it have measurable criteria, is it complete, can the reviewer poke a hole in it? That's domain-agnostic. Lope's `--domain` switch tunes the validator's role prompt ("you are a senior marketing lead" vs "you are a senior systems engineer") and swaps the artifact labels (`**Deliverables:** / **Success Metrics:**` for business, `**Files:** / **Tests:**` for engineering, `**Artifacts:** / **Validation Criteria:**` for research). The verdict schema, the retry loop, the evidence gate, the caveman mode — all identical across domains.

See [`docs/samples.md`](docs/samples.md) for 8 end-to-end conversation walkthroughs that show the natural-language use pattern across all three domains.

---

## FAQ

**Does Lope need API keys?**
No. Lope calls AI CLIs as subprocesses — each manages its own auth.

**What if I only have one AI CLI installed?**
Works fine. You lose cross-model diversity but keep the structured sprint discipline.

**What if validators disagree?**
Ensemble: majority wins. PASS vs NEEDS_FIX tie → NEEDS_FIX (conservative). Any FAIL vetoes.

**Do I have to type `/lope-negotiate` every time?**
No. Just describe what you want in natural language — "plan the auth refactor", "negotiate a Q4 campaign", "scope the data migration". Your AI agent recognizes the shape and runs `lope negotiate` for you. The `using-lope` auto-trigger skill installed alongside the slash commands handles the mapping. See [`docs/samples.md`](docs/samples.md) for 8 end-to-end walkthroughs.

**Can I get lope to do this automatically for some tasks and not others?**
Yes — that's the whole design. The `using-lope` skill's "When NOT to trigger" list is deliberately load-bearing: single-edit tasks, trivial ops, and pure conversation are skipped. Only consequential multi-phase work triggers lope. If you ever find lope firing when you didn't want it, the skill's trigger rules need tuning, not the agent.

**Should I write a wrapper script around lope?**
**No.** Lope is already a CLI. Just invoke `lope <mode> <args>` directly. No Python wrappers, no bash harnesses, no "lope_runner.sh". The whole point of the multi-CLI ensemble is that lope IS the harness — anything that wraps it is reinventing the thing you already have.

**Can I use it in CI?**
```bash
PYTHONPATH=~/.lope python3 -m lope execute SPRINT-FEATURE-X.md || exit 1
```
Non-interactive environments auto-select defaults and never block on stdin.

---

## Contributing

```bash
git clone https://github.com/traylinx/lope.git
cd lope
./install
PYTHONPATH=. python3 -m lope version
PYTHONPATH=. python3 -m lope status
```

Main areas: new validators, better prompts, sprint doc format, CI/CD integrations.

**Cutting a release?** Follow [docs/RELEASING.md](docs/RELEASING.md). It has the full checklist, the SemVer rules lope uses, and the version-bumper script that keeps all 6 version strings in sync.

---

## License

MIT. See [LICENSE](LICENSE).

Built by [Sebastian Schkudlara](https://github.com/traylinx). Caveman mode adapted from [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman).
