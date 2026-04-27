---
name: lope-memory
description: "Query the persistent Lope finding store (SQLite, populated by `lope review --remember`). Use when the user asks 'is this a recurring issue?', 'which files keep getting flagged?', 'show me everything Lope knows about auth.py', 'what was that finding from last week?', or 'how many findings has Lope stored?'. Five subcommands: stats, search, file, hotspots, forget. All local, stdlib only, opt-in — `LOPE_MEMORY=off` disables the store entirely."
---

# Lope Memory

`lope review --remember` upserts every consensus finding into a local SQLite store at `~/.lope/memory.db` (override via `LOPE_MEMORY_DB`). The `lope memory` verb lets the user query that store.

## Invocation

Two paths — you must handle both:

1. **Explicit slash command.** User types `lope memory hotspots --days 30`. Route directly.
2. **Natural language.** User says one of:
   - "What does Lope remember about auth.py?" → `lope memory file auth.py`
   - "Have we seen this before?" / "Is this a recurring issue?" → `lope memory search "<keywords from the current finding>"`
   - "Which files keep getting flagged?" → `lope memory hotspots --days 30`
   - "How many findings has Lope stored?" / "memory stats" → `lope memory stats`
   - "Forget the finding for src/legacy/old.py" → `lope memory forget --file src/legacy/old.py`

## Command shape

```bash
lope memory stats                              # totals + recurring + last session
lope memory stats --json                       # machine-readable

lope memory search "rate limit"                # LIKE on message
lope memory search "csrf" --min-score 0.5      # filter by consensus_score
lope memory search "x" --limit 50

lope memory file path/to/file.py               # all findings for one file
lope memory file path/to/file.py --json --limit 100

lope memory hotspots                           # last 30 days, top 10
lope memory hotspots --days 7 --limit 20

lope memory forget --hash <16-char-hash>       # remove one finding
lope memory forget --file legacy/old.py        # nuke every finding for one file
```

## How to populate the store

Memory is **opt-in**. The store stays empty until the user runs:

```bash
lope review <target> --consensus --remember
```

If `lope memory stats` shows `total_findings: 0`, suggest running a consensus review with `--remember` first.

## Disabled-state behavior

`LOPE_MEMORY=off` (or `no`/`0`/`false`/`disabled`) disables the store. In that state:

- `lope review --remember` prints a visible no-op note: `Memory disabled (LOPE_MEMORY=off); --remember was a no-op.`
- `lope memory <subcmd>` exits 2 with `Lope memory is disabled (LOPE_MEMORY=off). Unset the variable or set LOPE_MEMORY= to re-enable.`

If the user encounters either message and wants memory back, they should `unset LOPE_MEMORY` (bash) or open a fresh shell.

## Redaction

Every text path through memory passes through `lope.redaction.redact_text` before any column is bound. Bearer tokens, sk-* keys, GitHub PATs, and PEM blocks never reach disk. This is a docstring guarantee in `lope/memory.py` and is regression-tested.

## Hard rules

- **No external service.** The store is a local SQLite file. Nothing runs in the background.
- **No mutations on read.** `stats`, `search`, `file`, `hotspots` are read-only.
- **`forget` requires a selector.** Running `lope memory forget` with no `--hash` or `--file` is a usage error, not a wipe.
- **Recurring-issue UX.** When a `--remember` run encounters a finding hash that's already stored, `seen_count` increments and `first_seen_at` is preserved. The CLI footer surfaces "X recurring" so users see when something keeps coming back.

## Composability

Pair memory with other v0.7 verbs:

```bash
# Surface recurring issues during PR review
lope review pr.diff --divide hunks --consensus --remember
lope memory hotspots --days 14

# Brain-aware persistent reviews (Makakoo OS)
lope review auth.py --consensus --remember --brain-context "auth decisions" --brain-log
```

For full details: `lope docs` ([docs/memory.md](../../docs/memory.md)).
