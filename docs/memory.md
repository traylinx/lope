# Lope memory — persistent finding store

`lope review --remember` upserts every consensus finding into a local SQLite database so future sessions can answer "is this a recurring issue?", "which files keep getting flagged?", and "show me everything Lope has stored about `auth.py`."

The store is **local**, **stdlib-only**, and **opt-in**. Without `--remember` (or the `lope memory` verb) Lope never reads or writes the file.

---

## Configuration

| Env var | Effect | Default |
|---|---|---|
| `LOPE_MEMORY=off\|no\|0\|false\|disabled` | Hard kill switch. `--remember` becomes a visible no-op; `lope memory <subcmd>` exits 1 with a one-line note. | unset (memory enabled) |
| `LOPE_MEMORY_DB=/path/to/memory.db` | Relocate the SQLite file without changing `LOPE_HOME`. | `~/.lope/memory.db` |

Detection runs lazily on first call. `import lope.memory` has zero side effects — no schema initialization until something calls `LopeMemory(...)`.

---

## Schema

Three tables:

```
findings(id, hash, message, file, line, end_line, severity, category,
         confidence, consensus_score, consensus_level, agreement_count,
         total_validators, detected_by_json, evidence_json,
         first_seen_at, last_seen_at, seen_count)

review_sessions(id, task, focus, target_path, validators_json,
                raw_count, merged_count, created_at, duration_ms)

session_findings(session_id, finding_hash)
```

Five indexes cover the four query paths used by the CLI: hash lookup, file lookup, score-sorted retrieval, last-seen window, and the junction.

### Upsert semantics

Each `--remember` call upserts findings by `hash` (a 16-char SHA-256 of the canonical `(file, line, normalized_message)`):

- **Preserved on re-detection:** `first_seen_at`.
- **Bumped on re-detection:** `seen_count += 1`, `last_seen_at = now`.
- **Strengthened only:** `confidence` and `consensus_score` take per-call MAX; a finding never gets weaker over time.
- **Promoted forward only:** `consensus_level` walks `confirmed > likely > needs-verification > unverified > disputed` and never demotes.
- **Unioned by validator:** `detected_by` is a deduped union (every detector that ever flagged the issue stays). `evidence` is keyed by validator name and merged on collision — the most recent evidence snippet wins per validator. Old validators that don't appear in the new run are preserved.

The "promoted forward only" rule is why `disputed` sits at the bottom of the rank: a contradiction never demotes a previously confirmed finding, but neither does it inherit promotion strength on its own.

---

## CLI

```bash
lope memory stats                        # totals + recurring + last session
lope memory search "rate limit"          # LIKE search on message
lope memory search "csrf" --min-score 0.5
lope memory file auth.py                 # everything stored for one file
lope memory hotspots --days 30           # files with most findings recently
lope memory forget --hash <hash>         # remove one finding
lope memory forget --file legacy/x.py    # nuke every finding for a file
```

`stats`, `search`, `file`, and `hotspots` accept `--json` for machine-readable output. `forget` does not — it always prints a one-line ack on stdout and requires either `--hash` or `--file` (running it with no selector is a usage error, not a wipe).

```bash
lope review src/ --divide files --consensus --remember     # populate the store
lope memory hotspots                                       # see what keeps breaking
lope memory file src/auth/middleware.py                    # drill into one file
```

---

## Redaction

Every text path passes through `lope.redaction.redact_text` **before** the row is bound. That covers:

- `findings.message`, `evidence` keys + values, `detected_by` names.
- `review_sessions.task`, `focus`, `target_path`, `validators_json`.
- `findings.file` and `findings.category` (in case a path contains a token).

Redaction is idempotent (Phase 5 fixed a defensive double-redaction concern surfaced by `lope review --validators pi,kimi`). Bearer tokens, sk-* keys, GitHub PATs, and PEM blocks are all neutralized to `Bearer <redacted>` / `sk-<redacted>` / etc. before they reach disk.

---

## Recurring-issue UX

When `--remember` is paired with `--consensus`, the CLI emits a memory footer summarizing what was stored:

```
Memory: stored 4 finding(s) (1 recurring) → /Users/sebastian/.lope/memory.db
```

In JSON output, the same fact is structured:

```json
"memory": {
  "session_id": 42,
  "stored_count": 4,
  "recurring_count": 1,
  "recurring_hashes": ["abc123ef456789ab"],
  "db_path": "/Users/sebastian/.lope/memory.db"
}
```

When `--brain-log` is also set, the journal bullet quotes the lead recurring hash so the Makakoo Brain has a one-click pointer back to the durable record.

---

## What memory does NOT do

- **No daemon.** The store is a SQLite file; nothing runs in the background.
- **No external vector DB.** Search is `LIKE %query%` plus simple sort. Makakoo Brain can provide retrieval when present (`--brain-context`); public Lope memory stays SQLite/LIKE for v0.7.
- **No automatic write.** `--remember` is the only path that populates the store.
- **No multi-machine sync.** The DB file is portable across machines (it's stdlib SQLite), but Lope itself does not push or pull.

For implementation, see `lope/memory.py` and `tests/test_memory.py`.
