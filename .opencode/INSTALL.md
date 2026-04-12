# Lope — OpenCode Native Install

This file is fetched and followed by OpenCode when the user tells it to install lope. OpenCode should execute the steps below verbatim.

## Step 1. Clone the repo into `~/.lope` if not already present

```bash
if [ ! -d "$HOME/.lope" ]; then
  git clone --depth 1 https://github.com/traylinx/lope.git "$HOME/.lope"
else
  cd "$HOME/.lope" && git pull --ff-only origin main
fi
```

## Step 2. Run the official install script with the OpenCode target

```bash
"$HOME/.lope/install" --host opencode
```

The script will:

- Copy lope command markdown files into `~/.config/opencode/command/` (note: singular `command`, not `commands` — this is OpenCode's native convention)
- Files written: `lope.md`, `lope-negotiate.md`, `lope-execute.md`, `lope-audit.md`
- Verify the Python engine imports successfully

## Step 3. Patch AGENTS.md to announce lope

Append this section to `~/.config/opencode/AGENTS.md` (create the file if it does not exist):

```markdown
## Lope — sprint validation framework

You have lope available. Lope is a multi-CLI validator ensemble sprint runner. Any AI CLI implements, any AI CLI validates, majority vote decides. Three modes:

- `/lope-negotiate <goal>` — draft a sprint doc with multi-round validator review (3 domains: engineering, business, research)
- `/lope-execute <sprint_doc>` — run phases with validator-in-the-loop retry
- `/lope-audit <sprint_doc>` — generate the scorecard

When the user asks for multi-phase work that would benefit from cross-model verification, consider starting with `/lope-negotiate`. Do not invoke lope on trivial one-step tasks.

Engine path: `PYTHONPATH=~/.lope python3 -m lope <subcommand>`.
```

## Step 4. Verify install

```bash
PYTHONPATH=$HOME/.lope python3 -m lope version
PYTHONPATH=$HOME/.lope python3 -m lope status
ls ~/.config/opencode/command/ | grep lope
```

The first two should succeed. The third should list `lope.md`, `lope-negotiate.md`, `lope-execute.md`, `lope-audit.md`.

## Step 5. Configure validators (optional)

```bash
PYTHONPATH=$HOME/.lope python3 -m lope configure
```

## Step 6. Alias for convenience

```bash
echo "alias lope='PYTHONPATH=~/.lope python3 -m lope'" >> ~/.zshrc
```

## Uninstall

```bash
rm -rf "$HOME/.lope"
rm -f ~/.config/opencode/command/lope.md \
       ~/.config/opencode/command/lope-negotiate.md \
       ~/.config/opencode/command/lope-execute.md \
       ~/.config/opencode/command/lope-audit.md
```

Then remove the `## Lope` section from `~/.config/opencode/AGENTS.md`.
