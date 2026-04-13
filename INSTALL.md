# Install Lope — any AI CLI, one prompt

You (the AI agent reading this) are going to install lope for your user. **Do not improvise. Do not skip steps. Do not invent flags.**

## Context

Lope is a multi-CLI validator ensemble sprint runner. Any AI CLI implements. Any AI CLI validates. Majority vote decides. Three modes: `/lope-negotiate`, `/lope-execute`, `/lope-audit`. Source of truth: https://github.com/traylinx/lope (MIT, zero external Python deps, pure stdlib).

## Prerequisites

- `git` on `PATH`
- `python3` >= 3.9 on `PATH`
- `bash` 3.2+ (stock macOS works)

Verify with `git --version && python3 --version && bash --version | head -1`. If any are missing, stop and tell the user.

---

## Step 1 — Clone or update lope to `~/.lope`

```bash
if [ ! -d "$HOME/.lope" ]; then
  git clone --depth 1 https://github.com/traylinx/lope.git "$HOME/.lope"
else
  cd "$HOME/.lope" && git pull --ff-only origin main
fi
```

---

## Step 2 — Identify your host CLI and install

You are running inside one specific AI CLI. **Figure out which one** (you know your own name) and jump to the matching section below. Execute the shell commands in that section. Each section is self-contained — only follow one.

If you can't identify your host, fall back to **"Generic"** at the bottom.

### If you are Claude Code

Skills live at `~/.claude/skills/`. Each skill is a directory containing a `SKILL.md` with YAML frontmatter. Symlink the five lope skills in so updates propagate when `~/.lope` is pulled.

```bash
mkdir -p "$HOME/.claude/skills"
for skill in lope lope-negotiate lope-execute lope-audit using-lope; do
  target="$HOME/.claude/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
ls -la "$HOME/.claude/skills/" | grep lope
```

Slash commands available after restart: `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`.

### If you are Codex (OpenAI Codex CLI)

Skills live at `~/.codex/skills/`. Same format as Claude Code — directory per skill, each with a `SKILL.md`.

```bash
mkdir -p "$HOME/.codex/skills"
for skill in lope lope-negotiate lope-execute lope-audit using-lope; do
  target="$HOME/.codex/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
ls -la "$HOME/.codex/skills/" | grep lope
```

Slash commands available after restart: `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`.

### If you are Gemini CLI

Gemini uses **namespaced TOML command files** at `~/.gemini/commands/<namespace>/<command>.toml`. Lope ships three pre-written TOML files at `~/.lope/commands/lope/*.toml` — symlink them in.

```bash
mkdir -p "$HOME/.gemini/commands/lope"
for toml in negotiate.toml execute.toml audit.toml; do
  target="$HOME/.gemini/commands/lope/$toml"
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$HOME/.lope/commands/lope/$toml" "$target"
done
ls -la "$HOME/.gemini/commands/lope/"
```

Slash commands available after restart: `/lope:negotiate`, `/lope:execute`, `/lope:audit`. **Note: Gemini uses a colon, not a hyphen.** There is no plain `/lope` umbrella in Gemini because Gemini doesn't support bare-namespace commands.

### If you are OpenCode

OpenCode reads flat markdown files from `~/.config/opencode/command/` (singular "command"). Symlink one `.md` per skill pointing at each skill's `SKILL.md`.

```bash
mkdir -p "$HOME/.config/opencode/command"
for skill in lope lope-negotiate lope-execute lope-audit using-lope; do
  src="$HOME/.lope/skills/$skill/SKILL.md"
  target="$HOME/.config/opencode/command/$skill.md"
  [ -f "$src" ] || continue
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$src" "$target"
done
ls -la "$HOME/.config/opencode/command/" | grep lope
```

Slash commands available after restart: `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`.

### If you are Cursor

Cursor agents live at `~/.cursor/agents/` as flat markdown files.

```bash
mkdir -p "$HOME/.cursor/agents"
for skill in lope lope-negotiate lope-execute lope-audit using-lope; do
  src="$HOME/.lope/skills/$skill/SKILL.md"
  target="$HOME/.cursor/agents/$skill.md"
  [ -f "$src" ] || continue
  [ -e "$target" ] || [ -L "$target" ] && rm -f "$target"
  ln -s "$src" "$target"
done
ls -la "$HOME/.cursor/agents/" | grep lope
```

### If you are Mistral Vibe

Vibe skills live at `~/.vibe/skills/`. Standard `SKILL.md` directory format — same as Claude Code.

```bash
mkdir -p "$HOME/.vibe/skills"
for skill in lope lope-negotiate lope-execute lope-audit using-lope; do
  target="$HOME/.vibe/skills/$skill"
  [ -e "$target" ] || [ -L "$target" ] && rm -rf "$target"
  ln -s "$HOME/.lope/skills/$skill" "$target"
done
ls -la "$HOME/.vibe/skills/" | grep lope
```

Slash commands available after Vibe restart: `/lope`, `/lope-negotiate`, `/lope-execute`, `/lope-audit`.

### If you are GitHub Copilot CLI

Copilot does not yet have a user-installable skill directory. Run the generic fallback below **and** tell the user that lope is callable via `lope negotiate "..."` from a terminal session but won't surface as a Copilot slash command until Copilot ships skill support.

### If you are any other AI CLI (Generic)

You know where your own skills or commands live. The five lope skills are in `~/.lope/skills/`, each with a standard `SKILL.md` YAML-frontmatter file.

```
$HOME/.lope/skills/lope/SKILL.md
$HOME/.lope/skills/lope-negotiate/SKILL.md
$HOME/.lope/skills/lope-execute/SKILL.md
$HOME/.lope/skills/lope-audit/SKILL.md
$HOME/.lope/skills/using-lope/SKILL.md
```

Do one of these, in order of preference:

1. **Symlink** each skill directory into your host's native skill path (so `~/.lope` updates propagate automatically)
2. **Copy** each skill directory if your host can't follow symlinks
3. **Translate** each `SKILL.md` into your host's native command format (TOML, JSON, flat markdown, whatever) and write it where your host expects

If your host uses a plugin manifest (package.json, manifest.toml, etc.), point it at `~/.lope` and let the host load everything.

If you genuinely cannot figure out where to put skills for your host, stop and tell the user the host name so they can file an issue at https://github.com/traylinx/lope/issues.

---

## Step 3 — Verify the engine

```bash
PYTHONPATH="$HOME/.lope" python3 -m lope version
PYTHONPATH="$HOME/.lope" python3 -m lope status
```

`version` prints the banner. `status` lists detected AI CLIs and the current config. If either fails, stop and report the exact error.

## Step 4 — Suggest a shell alias

Show the user this line and tell them to add it to their shell rc (`~/.zshrc` or `~/.bashrc`):

```bash
alias lope='PYTHONPATH=~/.lope python3 -m lope'
```

**Do not edit the user's shell rc yourself without asking.**

## Step 5 — Tell the user to restart their CLI

Slash command discovery is cached at CLI **session start** in every host. The commands you just installed will **not appear in the current session**. Tell the user explicitly:

> Lope is installed. **Quit this CLI and reopen it** — the `/lope-*` slash commands load at startup. You'll see them after the restart.

## Step 6 — Smoke test

After the user restarts, suggest they try:

```bash
lope negotiate --domain engineering --max-rounds 1 "rename a constant in one file"
```

A tiny task that runs end-to-end through the validator pool in under two minutes.

---

## What lope needs to actually work

Lope needs **at least two different AI CLIs installed on the machine** to form a real ensemble. It auto-detects 12 hosts: Claude Code, OpenCode, Gemini CLI, Codex, Mistral Vibe, Aider, Ollama, Goose, Open Interpreter, llama.cpp, GitHub Copilot CLI, Amazon Q. If the user has only one, tell them to install one more before running `lope negotiate`.

## Hard rules — do not break these

- **Do not invent flags.** `lope negotiate` takes `--domain`, `--out`, `--max-rounds`, `--context`. That is the complete list. No `--host`, no `--title`, no `--validators`.
- **Do not write a wrapper script around lope.** Lope is already a CLI. If the user asks you to "use lope to do X", invoke `lope <mode> <args>` directly. Do not create `lope_runner.py`, `generate_with_lope.sh`, or any other scaffold.
- **Do not commit lope state to the user's project git repo** unless they explicitly ask.

## Uninstall

```bash
rm -rf "$HOME/.lope" \
       "$HOME/.claude/skills/lope" "$HOME/.claude/skills/lope-"* "$HOME/.claude/skills/using-lope" \
       "$HOME/.codex/skills/lope" "$HOME/.codex/skills/lope-"* "$HOME/.codex/skills/using-lope" \
       "$HOME/.vibe/skills/lope" "$HOME/.vibe/skills/lope-"* "$HOME/.vibe/skills/using-lope" \
       "$HOME/.gemini/commands/lope" \
       "$HOME/.config/opencode/command/lope"*.md "$HOME/.config/opencode/command/using-lope.md" \
       "$HOME/.cursor/agents/lope"*.md "$HOME/.cursor/agents/using-lope.md"
```

## Troubleshooting

- **`git clone` fails** → check the repo is public and the user has network access
- **`python3` version < 3.9** → upgrade Python; do not try to work around it
- **`lope status` shows 0 detected CLIs** → the user has no AI CLIs on `PATH`; install at least two
- **`/lope` doesn't autocomplete after install** → the user did not restart their CLI; tell them to quit and reopen
- **Slash command works but `lope negotiate` crashes** → capture the full traceback and report it, do NOT patch lope's source

## You are done when

1. `lope version` prints a version banner
2. `lope status` lists at least one detected CLI
3. You told the user explicitly to **restart their CLI** before `/lope-*` will appear

Final message to the user:

> Lope is installed. Quit and reopen your CLI, then try `/lope-negotiate "your first goal"` to draft a sprint. Run `/lope` for the umbrella that explains when to use each mode.
