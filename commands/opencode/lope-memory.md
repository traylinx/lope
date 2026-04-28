---
name: lope-memory
description: Query the persistent Lope finding store (SQLite, populated by `lope review --remember`). Five subcommands — stats, search, file, hotspots, forget. Local, opt-in, redacted at write time. `LOPE_MEMORY=off` disables.
agent: build
---

# Lope memory

Query the local SQLite finding store at `~/.lope/memory.db` (override via `LOPE_MEMORY_DB`). Memory is **opt-in** — the store stays empty until the user runs `lope review <target> --consensus --remember`.

## What to do

```bash
lope memory stats                              # totals + recurring + last session
lope memory search "rate limit"                # LIKE on message
lope memory file path/to/file.py               # all findings for one file
lope memory hotspots                           # last 30 days, top 10
lope memory forget --hash <16-char-hash>       # remove one finding
lope memory forget --file legacy/old.py        # nuke every finding for one file
```

Optional flags: `--json`, `--limit N`, `--min-score 0.5` (search), `--days N` (hotspots).

## When to run memory

- "Is this a recurring issue?" → `lope memory search "<keywords from current finding>"`
- "What does Lope remember about <file>?" → `lope memory file <path>`
- "Which files keep getting flagged?" → `lope memory hotspots --days 30`
- "How many findings has Lope stored?" → `lope memory stats`
- "Forget the finding for <file/hash>" → `lope memory forget --file <path>` or `--hash <hash>`

## Do not

- Do not run `lope memory forget` without `--hash` or `--file` — it errors instead of wiping.
- Do not assume the store is populated. If `stats` shows `total_findings: 0`, suggest `lope review <target> --consensus --remember`.
- Do not panic if `LOPE_MEMORY=off` blocks every subcommand — the user has explicitly disabled the store; `unset LOPE_MEMORY` to re-enable.
- Run `lope docs` for the full reference.
