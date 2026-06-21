# Install Lope — any AI CLI, one prompt

You (the AI agent reading this) are going to install lope for your user. **Do not improvise. Do not skip steps. Do not invent flags.**

## Context

Lope is a multi-CLI validator ensemble for AI work. Any AI CLI drafts; any AI CLI validates. Current command surface as of v0.12.0:

- **Sprint commands**: `/lope-negotiate`, `/lope-execute`, `/lope-implement`, `/lope-audit`
- **Single-shot commands**: `/lope-ask`, `/lope-review`, `/lope-vote`, `/lope-compare`, `/lope-pipe`
- **Autonomous graph workflows**: `/lope-flow`
- **Memory and council review**: `/lope-memory`, `/lope-deliberate`
- **Evidence gates**: `lope gate`, `lope check`
- **Roster management**: `/lope-team`
- **Optional compression setup**: `/lope-headroom`
- **Meta and help**: `/lope-help`, `/lope`, `/using-lope`
- **Terminal maintenance**: `lope update` and `lope upgrade`

Gemini uses the `/lope:<verb>` namespaced form; Codex and Vibe load skills as content and invoke lope via natural language. Source: https://github.com/traylinx/lope (MIT, zero external Python deps, pure stdlib). The standard install is a git checkout in `~/.lope`; PyPI publishing is not live yet.

## Prerequisites

- `git` on `PATH`
- `python3` >= 3.9 on `PATH`
- `bash` 3.2+ (stock macOS works)

Verify with `git --version && python3 --version && bash --version | head -1`. If any are missing, stop and tell the user.

---

## Honest per-host support matrix

**Read this first.** Not every AI CLI supports user-installed slash commands. Pick your row before copying commands.

| Host | Slash commands? | Natural-language invocation? | Notes |
|---|---|---|---|
| **Claude Code** | ✅ full Lope skill set: `/lope`, `/lope-*`, `/using-lope` | ✅ | Scans `~/.claude/skills/` — symlinks just work |
| **Gemini CLI** | ✅ namespaced: `/lope:negotiate`, `/lope:execute`, `/lope:implement`, `/lope:audit`, `/lope:ask`, `/lope:review`, `/lope:vote`, `/lope:compare`, `/lope:pipe`, `/lope:team`, `/lope:flow`, `/lope:memory`, `/lope:deliberate`, `/lope:headroom`, `/lope:help` | ✅ | TOML files in `~/.gemini/commands/lope/` — note the colon, not hyphen |
| **OpenCode** | ✅ full Lope command set, including `/lope-flow`, `/lope-memory`, `/lope-deliberate`, `/lope-headroom` | ✅ | `~/.config/opencode/commands/*.md` (**plural**) with YAML frontmatter including `agent:` field |
| **Cursor** | ⚠️ unverified | ✅ | Skills written to `~/.cursor/agents/`. Test `/lope-*` after install; if they don't autocomplete, use natural language |
| **Mistral Vibe** | ❌ does **not** support user slash commands (confirmed by Vibe directly) | ✅ | Skills load as content at `~/.vibe/skills/`. Invoke via natural language |
| **Qwen Code** | ✅ via `~/.qwen/skills/` — same slash list as Claude | ✅ | Standard skill dir format |
| **pi (Traylinx)** | ✅ via shared `~/.agents/skills/` — same slash list | ✅ | Shared `@agents` skill tree; pi reads it at session start |
| **Codex** | ❌ does **not** register `/foo` from SKILL.md (confirmed by asking Codex directly) | ✅ | Skills load as **content**. Invoke via natural language: *"use lope to review auth.py across models"* and the Codex agent runs `lope <verb>` via bash |
| **GitHub Copilot CLI** | ❌ no user skill dir yet | ✅ | Invoke via natural language; agent runs `lope <verb>` via bash |

**Takeaway:** If your host is in the ❌ slash-commands column, lope still works perfectly. Users describe tasks in prose (*"plan the auth refactor"*, *"negotiate the Q4 campaign, needs to be right"*), the agent recognizes the shape and runs `lope negotiate "<goal>" --domain <engineering|business|research>` in a shell. Don't wait for an autocomplete that won't come.

---

## Step 1 — Clone or update lope to `~/.lope`

If this machine already has Lope, use the self-updater. For very old checkouts that do not have `lope update` yet, fall back to a plain fast-forward git pull once, then future updates use `lope update`.

```bash
if [ -d "$HOME/.lope/.git" ]; then
  if PYTHONPATH="$HOME/.lope" python3 -m lope update --help >/dev/null 2>&1; then
    PYTHONPATH="$HOME/.lope" python3 -m lope update --skip-install
  else
    git -C "$HOME/.lope" fetch --tags origin
    git -C "$HOME/.lope" pull --ff-only origin main
  fi
elif [ -e "$HOME/.lope" ]; then
  echo "$HOME/.lope exists but is not a Lope git checkout. Move it aside manually, then rerun install." >&2
  exit 1
else
  git clone https://github.com/traylinx/lope.git "$HOME/.lope"
fi
```

If `lope update` fails because the checkout has local tracked edits, stop and show the error. Do not overwrite user changes.

## Step 2 — Run the bundled installer

The fastest, tested path is the top-level `./install` bash script. It auto-detects which hosts exist on the machine and writes each one's files to the correct native path, with the correct format.

```bash
"$HOME/.lope/install"
```

The installer covers all 8 hosts (claude, codex, gemini, opencode, cursor, vibe, qwen, pi). It writes the standard locations for Claude, Codex, Gemini, OpenCode, and Cursor, and skips Vibe/Qwen/pi when their host homes are absent. Prints which slash commands should be available in which host.

### Alternative: per-host install (if the bundled installer fails)

Identify which CLI you're running inside and execute the matching block below. Skip the others.

#### Claude Code

```bash
mkdir -p "$HOME/.claude/skills"
for skill in lope lope-negotiate lope-execute lope-implement lope-audit lope-ask lope-review lope-vote lope-compare lope-pipe lope-team lope-flow lope-memory lope-deliberate lope-headroom lope-help using-lope; do
  target="$HOME/.claude/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
```

Available after restart: `/lope`, `/using-lope`, and the full `/lope-*` skill set (`negotiate`, `execute`, `implement`, `audit`, `ask`, `review`, `vote`, `compare`, `pipe`, `team`, `flow`, `memory`, `deliberate`, `headroom`, `help`).

#### Gemini CLI

```bash
mkdir -p "$HOME/.gemini/commands/lope"
for toml in negotiate.toml execute.toml implement.toml audit.toml ask.toml review.toml vote.toml compare.toml pipe.toml team.toml flow.toml memory.toml deliberate.toml headroom.toml help.toml; do
  target="$HOME/.gemini/commands/lope/$toml"
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$HOME/.lope/commands/lope/$toml" "$target"
done
```

Available after restart: `/lope:negotiate`, `/lope:execute`, `/lope:implement`, `/lope:audit`, `/lope:ask`, `/lope:review`, `/lope:vote`, `/lope:compare`, `/lope:pipe`, `/lope:team`, `/lope:flow`, `/lope:memory`, `/lope:deliberate`, `/lope:headroom`, `/lope:help`. Gemini uses **colon** not hyphen.

#### OpenCode

```bash
mkdir -p "$HOME/.config/opencode/commands"   # plural "commands"
for md in lope.md lope-negotiate.md lope-execute.md lope-implement.md lope-audit.md lope-ask.md lope-review.md lope-vote.md lope-compare.md lope-pipe.md lope-team.md lope-flow.md lope-memory.md lope-deliberate.md lope-headroom.md lope-help.md using-lope.md; do
  target="$HOME/.config/opencode/commands/$md"
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$HOME/.lope/commands/opencode/$md" "$target"
done
```

Available after restart: the full OpenCode command set — `/lope`, `/using-lope`, and `/lope-*` for negotiate, execute, implement, audit, ask, review, vote, compare, pipe, team, flow, memory, deliberate, headroom, and help. The `.md` files in `commands/opencode/` already have the required YAML frontmatter (`name`, `description`, `agent: build`) — do not replace them with raw SKILL.md symlinks, OpenCode will reject them.

#### Codex — content install only, no slash commands

Codex does not surface user `SKILL.md` files as slash commands. Install them anyway so the Codex agent can read them as context and invoke lope via natural language:

```bash
mkdir -p "$HOME/.codex/skills"
for skill in lope lope-negotiate lope-execute lope-implement lope-audit lope-ask lope-review lope-vote lope-compare lope-pipe lope-team lope-flow lope-memory lope-deliberate lope-headroom lope-help using-lope; do
  target="$HOME/.codex/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  mkdir -p "$target"
  cp -R "$HOME/.lope/skills/$skill"/. "$target"/
done
```

Tell the user: *"Typing `/lope` in Codex does nothing — Codex doesn't support user slash commands. Instead, describe the task in prose (e.g. 'plan the auth refactor, needs to be right') and Codex will invoke `lope` via bash."*

#### Mistral Vibe — content install only, no slash commands

Same story as Codex. Skills install as context, not slash commands.

```bash
mkdir -p "$HOME/.vibe/skills"
for skill in lope lope-negotiate lope-execute lope-implement lope-audit lope-ask lope-review lope-vote lope-compare lope-pipe lope-team lope-flow lope-memory lope-deliberate lope-headroom lope-help using-lope; do
  target="$HOME/.vibe/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
```

#### Cursor — unverified

```bash
mkdir -p "$HOME/.cursor/agents"
for skill in lope lope-negotiate lope-execute lope-implement lope-audit lope-ask lope-review lope-vote lope-compare lope-pipe lope-team lope-flow lope-memory lope-deliberate lope-headroom lope-help using-lope; do
  src="$HOME/.lope/skills/$skill/SKILL.md"
  target="$HOME/.cursor/agents/$skill.md"
  [ -f "$src" ] || continue
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$src" "$target"
done
```

After Cursor restart, check if `/lope-*` autocompletes. If yes, great. If no, invoke lope via natural language — the skills still load as context.

#### Qwen Code

```bash
mkdir -p "$HOME/.qwen/skills"
for skill in lope lope-negotiate lope-execute lope-implement lope-audit lope-ask lope-review lope-vote lope-compare lope-pipe lope-team lope-flow lope-memory lope-deliberate lope-headroom lope-help using-lope; do
  target="$HOME/.qwen/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
```

Available after restart: `/lope`, `/using-lope`, and the full `/lope-*` skill set.

#### pi (Traylinx) — shared `@agents` skill tree

pi reads skills from the cross-CLI `~/.agents/skills/` directory (shared with any other `@agents` consumer).

```bash
mkdir -p "$HOME/.agents/skills"
for skill in lope lope-negotiate lope-execute lope-implement lope-audit lope-ask lope-review lope-vote lope-compare lope-pipe lope-team lope-flow lope-memory lope-deliberate lope-headroom lope-help using-lope; do
  target="$HOME/.agents/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
```

Available after pi restart: `/lope`, `/using-lope`, and the full `/lope-*` skill set. Note: the install also registers pi as a lope **validator** (via `pi -p "{prompt}"`), so pi can both invoke lope AND be invoked BY lope for cross-model review.

#### Any other AI CLI (generic)

You know where your own skills or commands live. The Lope skills are in `~/.lope/skills/`:

```
$HOME/.lope/skills/lope/SKILL.md
$HOME/.lope/skills/lope-negotiate/SKILL.md
$HOME/.lope/skills/lope-execute/SKILL.md
$HOME/.lope/skills/lope-implement/SKILL.md
$HOME/.lope/skills/lope-audit/SKILL.md
$HOME/.lope/skills/lope-ask/SKILL.md
$HOME/.lope/skills/lope-review/SKILL.md
$HOME/.lope/skills/lope-vote/SKILL.md
$HOME/.lope/skills/lope-compare/SKILL.md
$HOME/.lope/skills/lope-pipe/SKILL.md
$HOME/.lope/skills/lope-team/SKILL.md
$HOME/.lope/skills/lope-flow/SKILL.md
$HOME/.lope/skills/lope-memory/SKILL.md
$HOME/.lope/skills/lope-deliberate/SKILL.md
$HOME/.lope/skills/lope-headroom/SKILL.md
$HOME/.lope/skills/lope-help/SKILL.md
$HOME/.lope/skills/using-lope/SKILL.md
```

Symlink them into your host's native skill path, or copy if symlinks aren't supported, or translate the frontmatter to whatever format your host requires. If you genuinely can't figure out where, file an issue at https://github.com/traylinx/lope/issues with the host name.

---

## Step 3 — Verify the engine

```bash
PYTHONPATH="$HOME/.lope" python3 -m lope version
PYTHONPATH="$HOME/.lope" python3 -m lope status
```

`version` prints the banner. `status` lists detected AI CLIs. Either failing means the engine is not importable — report the error.

Future updates are one command:

```bash
PYTHONPATH="$HOME/.lope" python3 -m lope update
# or, after you add the alias/wrapper:
lope update
```

`lope update` auto-detects the install method. For the normal `~/.lope` git checkout it fetches tags, pulls the explicit tracked remote branch with `--ff-only`, and reruns `./install` so host skills stay current. `lope update --dry-run` previews the commands. `lope update --host <host>` still updates code first, then scopes the installer refresh to that host. If you only want an install-only refresh, run `~/.lope/install --host <host>` directly. `lope upgrade` is kept as a legacy alias. PyPI publishing is not live yet, so the git checkout path is the supported server install/update path.

## Step 4 — Suggest a shell alias

Show the user this line and tell them to add it to `~/.zshrc` or `~/.bashrc`:

```bash
alias lope='PYTHONPATH=~/.lope python3 -m lope'
```

**Do not edit the user's shell rc yourself without asking.**

## Step 5 — Tell the user to restart their CLI

Slash command discovery is cached at CLI **session start**. The commands you just installed will **not** appear in the current session. Tell the user explicitly:

> Lope is installed. **Quit this CLI completely and reopen it** — `/lope-*` commands load at startup. For Codex and Vibe there are no slash commands by design; describe your task in prose and the agent will run `lope` for you.

## Step 6 — Smoke test

After the user restarts, they can verify install worked by running either of these in any CLI:

1. **Slash command path** (Claude Code, Gemini, OpenCode, Cursor): type `/lope-help` and confirm the agent dumps the complete lope reference.
2. **Natural-language path** (Codex, Vibe, or any CLI): type *"show me the lope docs"* — the agent should run `lope docs` in bash and display the reference.

Either path should end with the user seeing the structured lope reference.

---

## What lope needs to actually work

Lope needs **at least two different AI CLIs installed on the machine** to form a real ensemble for `negotiate/execute/audit`. The single-shot verbs (`ask/review/vote/compare/pipe`) run with one validator but produce less useful output — the whole point is multi-model perspective. Auto-detects 15: Claude Code, OpenCode, Gemini CLI, Codex, Mistral Vibe, Aider, Ollama, Goose, Open Interpreter, llama.cpp, GitHub Copilot CLI, Amazon Q, pi (Traylinx), Qwen Code, and Agy. If the user has only one, tell them to install one more before running lope.

## Hard rules — do not break these

- **Do not invent flags.** Each verb has its own fixed flag surface; run `lope <verb> --help` or `lope docs` if unsure. Key flags: `negotiate` takes `--domain/--out/--max-rounds/--context`; `ask/review/vote/compare/pipe` all take `--json`; `vote` requires `--options`; `compare` accepts `--criteria`; `review` accepts `--focus`; `pipe` accepts `--require-all`.
- **Do not write a wrapper script around lope.** Lope is already a CLI.
- **Do not commit lope state to the user's project git repo** unless they explicitly ask.

## Uninstall

```bash
rm -rf "$HOME/.lope" \
       "$HOME/.claude/skills/lope" "$HOME/.claude/skills/lope-"* "$HOME/.claude/skills/using-lope" \
       "$HOME/.codex/skills/lope" "$HOME/.codex/skills/lope-"* "$HOME/.codex/skills/using-lope" \
       "$HOME/.vibe/skills/lope" "$HOME/.vibe/skills/lope-"* "$HOME/.vibe/skills/using-lope" \
       "$HOME/.qwen/skills/lope" "$HOME/.qwen/skills/lope-"* "$HOME/.qwen/skills/using-lope" \
       "$HOME/.agents/skills/lope" "$HOME/.agents/skills/lope-"* "$HOME/.agents/skills/using-lope" \
       "$HOME/.gemini/commands/lope" \
       "$HOME/.config/opencode/commands/lope"*.md "$HOME/.config/opencode/commands/using-lope.md" \
       "$HOME/.config/opencode/command/lope"*.md "$HOME/.config/opencode/command/using-lope.md" \
       "$HOME/.cursor/agents/lope"*.md "$HOME/.cursor/agents/using-lope.md"
```

## Troubleshooting

- **`/lope*` doesn't autocomplete** after install and restart in a ✅-slash-command host → check `ls ~/.claude/skills/ | grep lope` (or equivalent path). If the symlinks are missing, re-run `~/.lope/install`.
- **`/lope*` doesn't autocomplete in Codex/Vibe** → that's by design. Those hosts don't surface user slash commands. Use natural language instead.
- **`/lope*` doesn't autocomplete in OpenCode** → confirm the directory is `commands/` (plural) not `command/` (singular). v0.3.1 shipped with the wrong path; v0.3.2 cleans up the old dir automatically.
- **`lope status` shows 0 detected CLIs** → no AI CLIs on `$PATH`; install at least 2.
- **`lope negotiate` crashes** → capture the full traceback and open an issue. Do not patch lope source.

## You are done when

1. `lope version` prints the new version banner
2. `lope status` lists at least one detected CLI
3. You told the user explicitly to **restart their CLI** (for slash-command hosts) or to **invoke lope via natural language** (for content-only hosts)
4. The user has successfully seen the lope reference via either `/lope-help` or prose ("show me the lope docs")

Final message:

> Lope is installed. **Slash-command hosts** (Claude Code, Gemini CLI, OpenCode): quit and reopen your CLI, then try `/lope-help` for the full reference or `/lope-negotiate "your first goal"` to draft a sprint. **Content-only hosts** (Codex, Vibe): describe your task in prose — I'll invoke lope for you. Run `lope docs` in a terminal anytime for the complete reference.
