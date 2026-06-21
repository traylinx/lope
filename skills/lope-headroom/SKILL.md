---
name: lope-headroom
description: Use when configuring, installing, verifying, or troubleshooting optional Headroom MCP compression with Lope or Lope-installed agent hosts. Covers opt-in Headroom MCP registration, large validator/tool-output compression, Claude/Codex/Cursor MCP status checks, and the `headroom-ai[mcp]` dependency when the user asks for it.
---

# Lope + Headroom

Headroom is Lope's optional context-compression layer for bulky tool and MCP output. It is not installed by default.

## Install and verify

Lope's `./install` skips Headroom by default. Enable the best-effort setup only when the user asks for it:

```bash
./install --host claude --with-headroom
headroom mcp status
```

For ordinary Lope updates, use `lope update`; do not install Headroom unless the user explicitly asks for compression setup.

If `headroom` is missing, install manually with Python 3.10+:

```bash
pipx install --python python3.11 'headroom-ai[mcp]>=0.25.0'
headroom mcp install --agent claude
```

If Python install fails because native dependencies are unavailable, use Headroom's Docker-native wrapper:

```bash
curl -fsSL https://raw.githubusercontent.com/chopratejas/headroom/main/scripts/install.sh | bash
headroom mcp install --agent claude --proxy-url http://127.0.0.1:8787
```

## Use during Lope work

- Compress noisy logs, huge validator output, long JSON, and repeated grep/test output.
- Preserve exact commands, errors, paths, IDs, versions, hashes, and verdict blocks before compression.
- Retrieve exact originals with `headroom_retrieve` if a validator or implementation agent needs detail.
- Check savings with `headroom_stats` when context bloat causes slow or failed reviews.

## Do not compress

- Secrets, keys, tokens, or credentials.
- Destructive-action confirmations.
- Exact patches or code where whitespace/context matters.
- External writing that the user will publish or send.
- Short output where compression adds no value.

Headroom complements Lope's `lope/caveman.py`: caveman compresses prompts/instructions, Headroom compresses tool/MCP payloads and keeps retrieval handles.
