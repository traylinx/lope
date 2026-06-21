# Lope — Codex Native Install

This file is fetched and followed by Codex when the user tells it to install Lope. Execute the steps below verbatim. Do not improvise flags.

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

## Step 2. Run the official installer for Codex

```bash
"$HOME/.lope/install" --host codex
```

The script copies the full Lope skill set into `~/.codex/skills/` so Codex sees plain skill names, not plugin-namespaced symlinks:

- `lope`, `using-lope`
- `lope-negotiate`, `lope-execute`, `lope-implement`, `lope-audit`
- `lope-ask`, `lope-review`, `lope-vote`, `lope-compare`, `lope-pipe`, `lope-team`
- `lope-flow`, `lope-memory`, `lope-deliberate`, `lope-headroom`, `lope-help`

Codex is a content-skill host. It may not expose every Lope item as a slash command, but the installed skills let the agent map natural language to `lope <verb>`.

## Step 3. Patch AGENTS.md to announce Lope

Add a `## Lope` section to `~/.codex/AGENTS.md` (create the file if it does not exist):

```markdown
## Lope — multi-CLI validator ensemble

You have Lope available. Lope is a multi-CLI validator ensemble for AI work. Any AI CLI implements; any AI CLI validates. Use it when cross-model perspective would improve the result.

Core command surface:
- `lope negotiate`, `lope execute`, `lope implement`, `lope audit` — planned multi-phase work
- `lope ask`, `lope review`, `lope vote`, `lope compare`, `lope pipe` — single-shot multi-model checks
- `lope team` — add, remove, list, enable, disable, and test validators
- `lope flow` — autonomous DOT graph workflows
- `lope memory`, `lope deliberate` — persistent findings and council decisions
- `lope docs` and `lope-headroom` skill — full reference and optional Headroom setup
- `lope gate`, `lope check` — objective evidence gates from `.lope/rules.json`
- `lope update` / `lope upgrade` — self-update the git checkout and refresh installed host skills

Default engine path: `PYTHONPATH=~/.lope python3 -m lope <subcommand>`. If `lope` is on PATH, use it directly.
```

## Step 4. Verify install

```bash
PYTHONPATH=$HOME/.lope python3 -m lope version
PYTHONPATH=$HOME/.lope python3 -m lope status
PYTHONPATH=$HOME/.lope python3 -m lope update --dry-run --skip-install
```

`status` lists detected validators. Lope needs at least two different AI CLIs for a real ensemble.

## Step 5. Configure validators (optional, interactive)

```bash
PYTHONPATH=$HOME/.lope python3 -m lope configure
```

## Step 6. Alias for convenience

Suggest adding this to `~/.zshrc` or `~/.bashrc`:

```bash
alias lope='PYTHONPATH=~/.lope python3 -m lope'
```

## Future updates

```bash
lope update
lope update --dry-run
lope update --host codex
```

`lope update --host codex` updates code first, then refreshes only Codex skills. For install-only refresh without pulling code, run `~/.lope/install --host codex`.

The supported server path today is the git checkout in `~/.lope`. PyPI publishing is not live until Trusted Publisher is configured.

## Uninstall

```bash
rm -rf "$HOME/.lope"
rm -rf ~/.codex/skills/lope ~/.codex/skills/lope-* ~/.codex/skills/using-lope
```

Then remove the `## Lope` section from `~/.codex/AGENTS.md`.
