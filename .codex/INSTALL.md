# Lope — Codex Native Install

This file is fetched and followed by Codex when the user tells it to install lope. Codex should execute the steps below verbatim, no improvisation.

## Step 1. Clone the repo into `~/.lope` if not already present

```bash
if [ ! -d "$HOME/.lope" ]; then
  git clone --depth 1 https://github.com/traylinx/lope.git "$HOME/.lope"
else
  cd "$HOME/.lope" && git pull --ff-only origin main
fi
```

## Step 2. Run the official install script with the Codex target

```bash
"$HOME/.lope/install" --host codex
```

The script will:

- Symlink `skills/lope/`, `skills/lope-negotiate/`, `skills/lope-execute/`, and `skills/lope-audit/` into `~/.codex/skills/`
- Symlink the markdown command files into `~/.codex/commands/` so Codex can surface them as slash commands
- Verify the Python engine imports successfully

## Step 3. Patch AGENTS.md to announce lope

Add a `## Lope` section to the Codex-level `AGENTS.md` at `~/.codex/AGENTS.md` (create the file if it does not exist) with this content:

```markdown
## Lope — sprint validation framework

You have lope available. Lope is a multi-CLI validator ensemble sprint runner. Any AI CLI implements, any AI CLI validates, majority vote decides. Three modes:

- `/lope-negotiate <goal>` — draft a sprint doc with multi-round validator review (3 domains: engineering, business, research)
- `/lope-execute <sprint_doc>` — run phases with validator-in-the-loop retry
- `/lope-audit <sprint_doc>` — generate the scorecard

When the user asks for multi-phase work that would benefit from cross-model verification, consider starting with `/lope-negotiate`. Do not invoke lope on single-edit tasks or simple one-step operations.

Engine path: `PYTHONPATH=~/.lope python3 -m lope <subcommand>`. If `lope` is on PATH, you can use it directly.
```

## Step 4. Verify install

```bash
PYTHONPATH=$HOME/.lope python3 -m lope version
PYTHONPATH=$HOME/.lope python3 -m lope status
```

Both should succeed. The status command lists detected CLIs — lope needs at least two different AI CLIs installed to form a real ensemble.

## Step 5. Configure validators (optional, interactive)

```bash
PYTHONPATH=$HOME/.lope python3 -m lope configure
```

This launches an interactive picker that writes `~/.lope/config.json`.

## Step 6. Alias for convenience

Suggest the user add this to their `~/.zshrc` or `~/.bashrc`:

```bash
alias lope='PYTHONPATH=~/.lope python3 -m lope'
```

## Uninstall

```bash
rm -rf "$HOME/.lope"
rm -rf ~/.codex/skills/lope ~/.codex/skills/lope-* ~/.codex/commands/lope*
```

Then remove the `## Lope` section from `~/.codex/AGENTS.md`.
