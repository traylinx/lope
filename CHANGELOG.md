# Changelog

## 0.3.0 — Initial public release

Lope is an autonomous sprint runner with a multi-CLI validator ensemble. Any AI CLI implements. Any AI CLI validates. Majority vote decides.

### What's in the box

- **Three modes:** `/lope-negotiate` (draft a sprint doc via multi-round validator review), `/lope-execute` (run phases with validator-in-the-loop retry), `/lope-audit` (generate the scorecard). Plus `/lope` for the umbrella and `using-lope` for natural-language auto-triggering.

- **Any CLI drafts, any CLI validates.** The primary validator in your pool drafts the sprint via subprocess (its own CLI — `claude --print`, `opencode run`, `gemini --prompt`, `codex exec`, `aider --message`). Other validators independently review. No separate hosted LLM required; lope's entire reasoning runs through the CLIs you already have.

- **12 built-in CLI adapters:** Claude Code, OpenCode, Gemini CLI, Codex, Mistral Vibe, Aider, Ollama, Goose, Open Interpreter, llama.cpp, GitHub Copilot CLI, Amazon Q. Plus infinite custom providers via JSON config — any HTTP endpoint or subprocess you can describe in five lines becomes a validator.

- **Three domains — not just for code.** `engineering` (code, software, infra), `business` (marketing campaigns, budgets, ops, consulting, legal, teaching), `research` (studies, systematic reviews, academic work, replication studies). Same validator loop, different role prompts and artifact labels. Business sprints use `**Deliverables:** / **Success Metrics:**`, research sprints use `**Artifacts:** / **Validation Criteria:**`, engineering sprints use `**Files:** / **Tests:**`. The ensemble checks what it always checks: specific plan, measurable criteria, complete scope, poke-a-hole review. See the [Use cases section in README](README.md#use-cases) for 9 worked examples across all three domains.

- **Two-stage validator review.** Each phase gets validated twice per retry: first spec compliance ("does this match the Goal?"), then code quality ("is this well-built?"). Spec NEEDS_FIX short-circuits the quality pass. Spec FAIL escalates immediately. Separates "clever slop that misses the requirement" from "meets spec but rough around the edges".

- **Verification-before-completion gate.** Any validator returning PASS with a rationale that lacks evidence (no file:line reference, no test output, no code fence, no explicit verification phrase) gets auto-downgraded to NEEDS_FIX with a synthesized "provide evidence" fix. Kills rubber-stamping architecturally. Word-boundary matching prevents false positives on substrings like "looks" or "passage".

- **No-placeholder lint on drafts.** Negotiator rejects drafts containing `TBD`, `TODO`, `XXX`, `FIXME`, bare prose ellipsis, `<placeholder>` tokens, or phases with empty artifact/check lists. On lint failure, the drafter loops back with specific fix instructions before any validator sees the draft — cheaper than a validator round.

- **Intelligent caveman mode.** Token-efficient validator prompts: drops articles, filler, and hedging while keeping code, paths, line numbers, and error messages exact. 50-65% token savings per validator call. Adapted from [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman). Controlled via `LOPE_CAVEMAN=full|lite|off`.

- **SessionStart hook.** One-paragraph briefing injected into your agent's context on every new session, so agents know lope exists and when to suggest it without the user having to remember a slash command. Specific about when NOT to trigger (single edits, pure conversation, trivial ops). Opt-out via `LOPE_HOOK=off`.

- **`using-lope` auto-trigger skill.** Meta-skill that recognizes natural-language descriptions of multi-phase work ("plan the auth refactor", "negotiate the Q4 campaign carefully") and invokes `lope negotiate` on your behalf. You don't have to type slash commands.

- **Install in one prompt.** Paste one line into any AI agent:

  ```
  Read https://raw.githubusercontent.com/traylinx/lope/main/INSTALL.md and follow the instructions to install lope on this machine natively.
  ```

  Your agent fetches `INSTALL.md`, follows six short steps, reports back when lope is live. CLI-agnostic — writes skills and commands into each host's native command directory using the format that host expects.

- **Cross-CLI slash commands.** `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit` autocomplete natively in Claude Code, Codex, Cursor, OpenCode. Gemini CLI uses namespaced syntax: `/lope:negotiate`, `/lope:execute`, `/lope:audit`. Plugin manifests ready for marketplace publication.

- **Zero external Python dependencies.** Pure stdlib. Works out of the box on Python 3.9+ without a venv. The entire engine is ~2000 lines of readable Python.

- **MIT licensed.**

### Escape hatches

| Env var | Effect |
|---|---|
| `LOPE_LINT=off` | Skip the no-placeholder lint on drafts |
| `LOPE_EVIDENCE_GATE=off` | Skip the PASS-needs-evidence downgrade |
| `LOPE_SINGLE_STAGE=1` | Revert execute mode to legacy single-pass validation |
| `LOPE_HOOK=off` | Suppress the SessionStart briefing |
| `LOPE_CAVEMAN=off` | Disable token compression on validator prompts |
| `LOPE_LLM_URL` | Optional hosted LLM fallback when primary validator can't draft |
| `LOPE_LLM_API_KEY` | Bearer token for the fallback endpoint |
| `LOPE_WORKDIR` | Working directory for validator subprocesses |
| `LOPE_TIMEOUT` | Validator timeout in seconds (default 480) |
