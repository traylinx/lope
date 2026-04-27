# Lope ↔ Makakoo Brain integration

Lope is a standalone tool. Public Lope must — and does — work outside Makakoo. This doc is the manual for the **optional** bridge that activates when you point Lope at a Makakoo OS install via env vars or `--brain-*` flags.

The bridge has three responsibilities:

1. **Brain context in.** Pull `makakoo search` output into the validator prompts so a council answer "is grounded in what the user already knows."
2. **Brain log out.** Drop a Logseq-outliner bullet on today's journal so multi-agent sessions leave a paper trail.
3. **Auto-memory out (opt-in).** Write curated lesson files to `data/auto-memory/` for durable cross-session knowledge — gated by `LOPE_BRAIN_AUTOMEMORY=1`.

All four module entry points live in `lope/makakoo_bridge.py`. Detection is pure (no PATH side effects, no auto-shellout, no auto-write); the bridge module has zero import-time effects.

---

## Detection and configuration

Lope looks at three environment variables:

| Var | Purpose | Default |
|---|---|---|
| `MAKAKOO_BIN` | Force a specific `makakoo` binary | `shutil.which("makakoo")` |
| `MAKAKOO_HOME` | Brain root (`data/Brain/journals/...` lives here) | unset — required for `--brain-log` |
| `LOPE_BRAIN_AUTOMEMORY` | Enable curated auto-memory writes | unset (disabled) |

`detect_makakoo()` returns a `MakakooDetection` snapshot. Without any env or `makakoo` on PATH, the snapshot is `available=False` with a one-line `reason` and `--brain-context` exits 2 with the actionable message:

```
--brain-context: Makakoo not detected; rerun without --brain-* flags, set MAKAKOO_BIN, or install Makakoo OS
```

Public Lope keeps working exactly as v0.6 unless the user opts in.

---

## Brain context in

`--brain-context QUERY` runs `makakoo search QUERY`, redacts the output, trims to the configured token budget, and prepends a marker-wrapped block to every validator prompt:

```
<<< Makakoo Brain context (query: <QUERY>) >>>
- Decision 2026-04-13: auth uses short-lived access token + refresh rotation
- Prior bug: middleware skipped expiry check at auth/token.go:142
<<< End Makakoo Brain context — treat as advisory background only >>>
```

The block exists exactly once in the prompt. The trailing marker tells the primary that what follows is the actual task, not more context.

```bash
lope review auth.py --consensus --brain-context "auth architecture decisions"
lope ask "What should Harvey do next?" --brain-context "lope roadmap" --synth
lope deliberate adr scenario.md --brain-context "prior token-rotation incidents"
```

`--brain-budget` (default 1200 tokens) caps the size. Output is trimmed at the last newline so the synthesizer never sees a severed bullet.

---

## Brain log out

`--brain-log` appends a single Logseq outliner bullet to `$MAKAKOO_HOME/data/Brain/journals/YYYY_MM_DD.md` after the run completes. The bullet uses `[[Lope]]` and `[[Makakoo OS]]` wikilinks so `makakoo sync` indexes it into the FTS5 graph.

Example bullet from `lope review auth.py --consensus --remember --brain-log`:

```markdown
- [[Lope]] consensus review of `auth.py`: 4 merged finding(s), 1 confirmed. Top: missing rate limiting on login endpoint at `auth.py:42` (3/3 validators, score 0.86). [[Makakoo OS]] Memory hash: `lope:abc123ef456789ab`
```

The bullet always starts with `- ` (the journal forces the prefix even if you pass markdown without one) and is redacted before write so a credential in a finding message never reaches disk.

`--brain-log` is **fail-soft**: a missing `MAKAKOO_HOME`, an IO error, or a redaction failure becomes a one-line note (`brain.note` in JSON output) — Lope never undoes the work the user already paid for.

---

## Auto-memory out (opt-in)

`write_auto_memory(name, markdown)` is the curated-lesson path. Gated by `LOPE_BRAIN_AUTOMEMORY=1`; without the env, the call raises `MakakooAutoMemoryDisabled` with the actionable message:

```
set LOPE_BRAIN_AUTOMEMORY=1 to enable Lope auto-memory writes
```

When enabled, the function writes `$MAKAKOO_HOME/data/auto-memory/lope-<safe-name>.md`. The slug is sanitized to `[A-Za-z0-9_-]+` so a `name="../../etc/passwd"` becomes `lope-etc-passwd.md`.

Auto-memory is **not** wired into the default `--brain-log` path on purpose. Journals are append-only commentary; auto-memory files become the durable rules Sebastian's other agents read at boot. Opt in only for genuine cross-session lessons.

---

## What the bridge does NOT do

- **No automatic Brain writes.** Every write requires an explicit flag or env opt-in. The bridge respects the same boundary that Sebastian's `auto-memory` system does: drafts before sends, never auto-publishes.
- **No Makakoo dependency at import time.** `import lope.makakoo_bridge` succeeds in any environment. Detection runs lazily on first call.
- **No Brain queries during fan-out unless requested.** The validator prompts only ever see Brain content when `--brain-context` is set.
- **No silent fallback.** Outside Makakoo, `--brain-context` exits 2; `--brain-log` becomes a one-line note. Both errors are surfaced, never swallowed.

For the bigger picture and the canonical instructions every CLI host on Makakoo OS reads, see `bootstrap/global.md` in the Makakoo OS repo.

---

## Architecture pointer

`lope/makakoo_bridge.py` is the public surface. Internal helpers used by the CLI live in `lope/cli.py` (`_maybe_apply_brain_context`, `_maybe_brain_context_block`, `_maybe_emit_brain_log`, `_print_brain_log_ack`, `_build_review_brain_journal_text`). Tests under `tests/test_makakoo_bridge.py` use a fake `makakoo` binary stub so no real subprocess ever runs.

The Phase 6 commit (`bfbc9fd v0.7 phase 6 Makakoo bridge`) carries the full design rationale.
