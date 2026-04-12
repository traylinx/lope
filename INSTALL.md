# Install Lope — any AI CLI, one prompt

You (the AI agent reading this) are going to install lope for your user. Follow the steps below **in order**. Do not improvise. Do not skip the verification step.

## Context

Lope is a multi-CLI validator ensemble sprint runner. Any AI CLI implements. Any AI CLI validates. Majority vote decides. Three modes: `/lope-negotiate`, `/lope-execute`, `/lope-audit`. Works on Claude Code, Codex, Gemini CLI, OpenCode, Cursor, GitHub Copilot CLI, and any other AI CLI that surfaces slash commands.

Source of truth: https://github.com/traylinx/lope (MIT license, zero external Python deps, pure stdlib).

## Prerequisites

- `git` on `PATH`
- `python3` >= 3.9 on `PATH`
- `bash` 3.2+ (stock macOS works)

Verify with:

```bash
git --version && python3 --version && bash --version | head -1
```

If any of those are missing, stop and tell the user to install them before continuing. Do not try to work around missing prerequisites.

## Step 1 — Clone or update lope to `~/.lope`

```bash
if [ ! -d "$HOME/.lope" ]; then
  git clone --depth 1 https://github.com/traylinx/lope.git "$HOME/.lope"
else
  cd "$HOME/.lope" && git pull --ff-only origin main
fi
```

## Step 2 — Run the installer

```bash
"$HOME/.lope/install"
```

This script auto-detects which AI CLI hosts are present on the machine (Claude Code, Codex, Gemini CLI, OpenCode, Cursor) and writes lope's skills and commands into each host's **native** command directory using the format that host expects:

| Host | Path | Format |
|---|---|---|
| Claude Code | `~/.claude/skills/lope*/` | skill dirs with SKILL.md |
| Codex | `~/.codex/skills/lope*/` | skill dirs with SKILL.md |
| Gemini CLI | `~/.gemini/commands/lope/*.toml` | TOML commands |
| OpenCode | `~/.config/opencode/command/lope*.md` | flat markdown |
| Cursor | `~/.cursor/agents/lope*.md` | flat markdown |

Hosts that are not installed on the machine are skipped silently.

## Step 3 — Verify the engine

```bash
PYTHONPATH="$HOME/.lope" python3 -m lope version
PYTHONPATH="$HOME/.lope" python3 -m lope status
```

The first command should print the lope banner with a version number. The second should list detected AI CLIs on the machine and the current config. If either fails, stop and report the exact error to the user.

## Step 4 — Suggest a shell alias

Tell the user to add this to their shell rc (`~/.zshrc` or `~/.bashrc`) so they can type `lope` instead of the full `PYTHONPATH` incantation:

```bash
alias lope='PYTHONPATH=~/.lope python3 -m lope'
```

**Do not add this to the user's shell rc yourself without asking.** Show them the line and let them add it.

## Step 5 — Announce the slash commands

Tell the user that lope is now installed and these slash commands are available in their CLI session (they may need to restart the CLI for discovery to pick up the new skills):

- `/lope` — umbrella explaining the three modes and when to use each
- `/lope-negotiate <goal>` — draft a sprint doc with multi-round validator review
- `/lope-execute <sprint_doc>` — run phases with validator-in-the-loop retry
- `/lope-audit <sprint_doc>` — generate the scorecard

On Gemini CLI the syntax is `/lope:negotiate`, `/lope:execute`, `/lope:audit` (Gemini uses namespace-scoped commands).

## Step 6 — Test with a tiny sprint

Suggest the user try:

```bash
lope negotiate --domain engineering --max-rounds 1 "rename a constant in one file"
```

This is deliberately a tiny task — it will run end-to-end through the validator pool in under two minutes. If it completes cleanly (even if validators escalate), lope is installed and working. If it crashes with a Python traceback, report the exact error.

## What lope needs to actually work

Lope needs **at least two different AI CLIs installed on the machine** to form a real ensemble (otherwise there's nothing for the majority-vote logic to vote on). It auto-detects: Claude Code, OpenCode, Gemini CLI, Codex, Mistral Vibe, Aider, Ollama, Goose, Open Interpreter, llama.cpp, GitHub Copilot CLI, Amazon Q. If the user has only one, tell them to install one more — whichever they like — before running `lope negotiate`.

Users can also add custom HTTP providers by editing `~/.lope/config.json` (see README for the schema). No Python required.

## Uninstall

```bash
rm -rf "$HOME/.lope" \
       "$HOME/.claude/skills/lope" "$HOME/.claude/skills/lope-"* "$HOME/.claude/skills/using-lope" \
       "$HOME/.codex/skills/lope" "$HOME/.codex/skills/lope-"* "$HOME/.codex/skills/using-lope" \
       "$HOME/.gemini/commands/lope" \
       "$HOME/.config/opencode/command/lope"*.md "$HOME/.config/opencode/command/using-lope.md" \
       "$HOME/.cursor/agents/lope"*.md "$HOME/.cursor/agents/using-lope.md"
```

That's a full clean removal of every file the installer wrote.

## If something goes wrong

- **`git clone` fails with 404 or auth error** → the repo may still be private; tell the user to check with the maintainer
- **`python3` import fails** → the user's Python is not 3.9+; upgrade Python
- **`lope status` shows 0 detected CLIs** → the user has no AI CLIs on `PATH`; tell them to install at least two of the 12 supported ones
- **`lope negotiate` crashes with a traceback** → capture the full traceback and report it; do NOT try to patch lope's source as the "fix"
- **Install script reports a host as "skipped — not detected"** → that host isn't on the machine; this is fine and expected

Do not invent flags. Do not run `lope negotiate --host X` or `--title X` — those flags do not exist. Always run `lope <subcommand> --help` to see the real flag list for any subcommand.

**Do not write a wrapper script around lope.** Lope is already a CLI. If the user asks you to "use lope to do X", you invoke `lope <mode> <args>` in a shell — you do NOT create a `lope_runner.py` or a `generate_with_lope.sh` or any other scaffolding. The entire point of the multi-CLI ensemble is that lope is the harness. One command, no wrapper.

## You are done when

All three of these are true:

1. `lope version` prints a version banner
2. `lope status` lists at least one detected CLI and a config file
3. The slash commands (`/lope`, `/lope-negotiate`, etc.) show up in the user's CLI session after a restart

Report to the user:

> Lope is installed. Try `/lope-negotiate "your first goal"` to draft a sprint. Run `/lope` to see the umbrella skill that explains when to use each mode.
