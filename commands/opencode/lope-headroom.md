---
name: lope-headroom
description: Configure, install, verify, or troubleshoot Headroom MCP compression for Lope and Lope-installed agent hosts.
agent: build
---

# Lope Headroom

Use this when the user asks to install, verify, configure, or debug Headroom with Lope.

Read the source skill first:

```bash
cat ~/.lope/skills/lope-headroom/SKILL.md
```

If `~/.lope` is not the active checkout, use the repo-local file:

```bash
cat skills/lope-headroom/SKILL.md
```

Follow that skill's decision tree. Do not invent Headroom flags. For ordinary Lope updates, use `lope update`; Headroom is optional and stays opt-in unless the user asks for it.
