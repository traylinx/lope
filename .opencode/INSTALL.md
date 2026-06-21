# Lope — OpenCode Native Install

This file is fetched and followed by OpenCode when the user tells it to install Lope. Execute the steps below verbatim.

## Step 1. Clone or update `~/.lope`

```bash
if [ ! -d "$HOME/.lope/.git" ]; then
  if [ -e "$HOME/.lope" ]; then
    echo "$HOME/.lope exists but is not a Lope git checkout. Move it aside manually, then rerun install." >&2
    exit 1
  fi
  git clone https://github.com/traylinx/lope.git "$HOME/.lope"
else
  if PYTHONPATH="$HOME/.lope" python3 -m lope update --help >/dev/null 2>&1; then
    PYTHONPATH="$HOME/.lope" python3 -m lope update --skip-install
  else
    git -C "$HOME/.lope" fetch --tags origin
    git -C "$HOME/.lope" pull --ff-only origin main
  fi
fi
```

If `lope update` or `git pull --ff-only` reports tracked local edits, stop and show the error. Do not overwrite user changes.

## Step 2. Run the official installer for OpenCode

```bash
"$HOME/.lope/install" --host opencode
```

The script writes Lope command wrappers into OpenCode's native plural command directory:

```text
~/.config/opencode/commands/
```

Files written include:

- `lope.md`, `using-lope.md`
- `lope-negotiate.md`, `lope-execute.md`, `lope-implement.md`, `lope-audit.md`
- `lope-ask.md`, `lope-review.md`, `lope-vote.md`, `lope-compare.md`, `lope-pipe.md`, `lope-team.md`
- `lope-flow.md`, `lope-memory.md`, `lope-deliberate.md`, `lope-headroom.md`, `lope-help.md`

The old singular `~/.config/opencode/command/` path was wrong and is cleaned up by the installer.

## Step 3. Patch AGENTS.md to announce Lope

Append this section to `~/.config/opencode/AGENTS.md` (create the file if it does not exist):

```markdown
## Lope — multi-CLI validator ensemble

You have Lope available. Lope is a multi-CLI validator ensemble for AI work. Any AI CLI implements; any AI CLI validates. Use it when cross-model perspective would improve the result.

Core command surface:
- `/lope-negotiate`, `/lope-execute`, `/lope-implement`, `/lope-audit` — planned multi-phase work
- `/lope-ask`, `/lope-review`, `/lope-vote`, `/lope-compare`, `/lope-pipe` — single-shot multi-model checks
- `/lope-team` — add, remove, list, enable, disable, and test validators
- `/lope-flow` — autonomous DOT graph workflows
- `/lope-memory`, `/lope-deliberate` — persistent findings and council decisions
- `/lope-headroom`, `/lope-help` — optional Headroom setup and full Lope reference
- `lope gate`, `lope check` — objective evidence gates from `.lope/rules.json`
- `lope update` / `lope upgrade` — self-update the git checkout and refresh installed host commands

Engine path: `PYTHONPATH=~/.lope python3 -m lope <subcommand>`.
```

## Step 4. Verify install

```bash
PYTHONPATH=$HOME/.lope python3 -m lope version
PYTHONPATH=$HOME/.lope python3 -m lope status
PYTHONPATH=$HOME/.lope python3 -m lope update --dry-run --skip-install
ls ~/.config/opencode/commands/ | grep '^lope'
```

The first two should succeed. The final command should list the full `lope*.md` command set.

## Step 5. Configure validators (optional)

```bash
PYTHONPATH=$HOME/.lope python3 -m lope configure
```

## Step 6. Alias for convenience

```bash
echo "alias lope='PYTHONPATH=~/.lope python3 -m lope'" >> ~/.zshrc
```

## Future updates

```bash
lope update
lope update --dry-run
lope update --host opencode
```

`lope update --host opencode` updates code first, then refreshes only OpenCode commands. For install-only refresh without pulling code, run `~/.lope/install --host opencode`.

The supported server path today is the git checkout in `~/.lope`. PyPI publishing is not live until Trusted Publisher is configured.

## Uninstall

```bash
rm -rf "$HOME/.lope"
rm -f ~/.config/opencode/commands/lope*.md ~/.config/opencode/commands/using-lope.md
```

Then remove the `## Lope` section from `~/.config/opencode/AGENTS.md`.
