# Install Lope — any AI CLI, one prompt

You (the AI agent reading this) are going to install lope for your user. **Do not improvise. Do not skip steps. Do not invent flags.**

## Context

Lope is a multi-CLI validator ensemble sprint runner. Any AI CLI implements. Any AI CLI validates. Majority vote decides. Modes: `/lope-negotiate`, `/lope-execute`, `/lope-audit`, `/lope-help` (reference dump), `/lope` (umbrella). Source: https://github.com/traylinx/lope (MIT, zero external Python deps, pure stdlib).

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
| **Claude Code** | ✅ full — `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`, `/lope-help`, `/using-lope` | ✅ | Scans `~/.claude/skills/` — symlinks just work |
| **Gemini CLI** | ✅ namespaced — `/lope:negotiate`, `/lope:execute`, `/lope:audit`, `/lope:help` | ✅ | TOML files in `~/.gemini/commands/lope/` — note the colon, not hyphen |
| **OpenCode** | ✅ `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`, `/lope-help`, `/using-lope` | ✅ | `~/.config/opencode/commands/*.md` (**plural**) with YAML frontmatter including `agent:` field |
| **Codex** | ❌ does **not** register `/foo` from SKILL.md (confirmed by asking Codex directly) | ✅ | Skills load as **content**. Invoke via natural language: *"use lope to negotiate the auth refactor"* and the Codex agent runs `lope <mode>` via bash |
| **Mistral Vibe** | ❌ does **not** support user slash commands (confirmed by Vibe directly) | ✅ | Skills load as content at `~/.vibe/skills/`. Invoke via natural language |
| **Cursor** | ⚠️ unverified | ✅ | Skills written to `~/.cursor/agents/`. Test `/lope-*` after install; if they don't autocomplete, use natural language |
| **GitHub Copilot CLI** | ❌ no user skill dir yet | ✅ | Invoke via natural language; agent runs `lope <mode>` via bash |

**Takeaway:** If your host is in the ❌ slash-commands column, lope still works perfectly. Users describe tasks in prose (*"plan the auth refactor"*, *"negotiate the Q4 campaign, needs to be right"*), the agent recognizes the shape and runs `lope negotiate "<goal>" --domain <engineering|business|research>` in a shell. Don't wait for an autocomplete that won't come.

---

## Step 1 — Clone or update lope to `~/.lope`

```bash
if [ ! -d "$HOME/.lope" ]; then
  git clone --depth 1 https://github.com/traylinx/lope.git "$HOME/.lope"
else
  cd "$HOME/.lope" && git pull --ff-only origin main
fi
```

## Step 2 — Run the bundled installer

The fastest, tested path is the top-level `./install` bash script. It auto-detects which hosts exist on the machine and writes each one's files to the correct native path, with the correct format.

```bash
"$HOME/.lope/install"
```

The installer covers all 6 hosts. Skips any host that isn't present. Prints which slash commands should be available in which host.

### Alternative: per-host install (if the bundled installer fails)

Identify which CLI you're running inside and execute the matching block below. Skip the others.

#### Claude Code

```bash
mkdir -p "$HOME/.claude/skills"
for skill in lope lope-negotiate lope-execute lope-audit lope-help using-lope; do
  target="$HOME/.claude/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
```

Available after restart: `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`, `/lope-help`, `/using-lope`.

#### Gemini CLI

```bash
mkdir -p "$HOME/.gemini/commands/lope"
for toml in negotiate.toml execute.toml audit.toml help.toml; do
  target="$HOME/.gemini/commands/lope/$toml"
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$HOME/.lope/commands/lope/$toml" "$target"
done
```

Available after restart: `/lope:negotiate`, `/lope:execute`, `/lope:audit`, `/lope:help`. Gemini uses **colon** not hyphen.

#### OpenCode

```bash
mkdir -p "$HOME/.config/opencode/commands"   # plural "commands"
for md in lope.md lope-negotiate.md lope-execute.md lope-audit.md lope-help.md using-lope.md; do
  target="$HOME/.config/opencode/commands/$md"
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$HOME/.lope/commands/opencode/$md" "$target"
done
```

Available after restart: `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`, `/lope-help`, `/using-lope`. The `.md` files in `commands/opencode/` already have the required YAML frontmatter (`name`, `description`, `agent: build`) — do not replace them with raw SKILL.md symlinks, OpenCode will reject them.

#### Codex — content install only, no slash commands

Codex does not surface user `SKILL.md` files as slash commands. Install them anyway so the Codex agent can read them as context and invoke lope via natural language:

```bash
mkdir -p "$HOME/.codex/skills"
for skill in lope lope-negotiate lope-execute lope-audit lope-help using-lope; do
  target="$HOME/.codex/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
```

Tell the user: *"Typing `/lope` in Codex does nothing — Codex doesn't support user slash commands. Instead, describe the task in prose (e.g. 'plan the auth refactor, needs to be right') and Codex will invoke `lope` via bash."*

#### Mistral Vibe — content install only, no slash commands

Same story as Codex. Skills install as context, not slash commands.

```bash
mkdir -p "$HOME/.vibe/skills"
for skill in lope lope-negotiate lope-execute lope-audit lope-help using-lope; do
  target="$HOME/.vibe/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
```

#### Cursor — unverified

```bash
mkdir -p "$HOME/.cursor/agents"
for skill in lope lope-negotiate lope-execute lope-audit lope-help using-lope; do
  src="$HOME/.lope/skills/$skill/SKILL.md"
  target="$HOME/.cursor/agents/$skill.md"
  [ -f "$src" ] || continue
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$src" "$target"
done
```

After Cursor restart, check if `/lope-*` autocompletes. If yes, great. If no, invoke lope via natural language — the skills still load as context.

#### Any other AI CLI (generic)

You know where your own skills or commands live. Five (or six) lope skills are in `~/.lope/skills/`:

```
$HOME/.lope/skills/lope/SKILL.md
$HOME/.lope/skills/lope-negotiate/SKILL.md
$HOME/.lope/skills/lope-execute/SKILL.md
$HOME/.lope/skills/lope-audit/SKILL.md
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

Lope needs **at least two different AI CLIs installed on the machine** to form a real ensemble. Auto-detects 12: Claude Code, OpenCode, Gemini CLI, Codex, Mistral Vibe, Aider, Ollama, Goose, Open Interpreter, llama.cpp, GitHub Copilot CLI, Amazon Q. If the user has only one, tell them to install one more before running `lope negotiate`.

## Hard rules — do not break these

- **Do not invent flags.** `lope negotiate` takes `--domain`, `--out`, `--max-rounds`, `--context`. That is the complete list. Run `lope <mode> --help` or `lope docs` if unsure.
- **Do not write a wrapper script around lope.** Lope is already a CLI.
- **Do not commit lope state to the user's project git repo** unless they explicitly ask.

## Uninstall

```bash
rm -rf "$HOME/.lope" \
       "$HOME/.claude/skills/lope" "$HOME/.claude/skills/lope-"* "$HOME/.claude/skills/using-lope" \
       "$HOME/.codex/skills/lope" "$HOME/.codex/skills/lope-"* "$HOME/.codex/skills/using-lope" \
       "$HOME/.vibe/skills/lope" "$HOME/.vibe/skills/lope-"* "$HOME/.vibe/skills/using-lope" \
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
