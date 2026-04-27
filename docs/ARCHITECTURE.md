# Lope architecture

Lope is a zero-dependency Python CLI that coordinates multiple AI CLIs as an ensemble. It has two execution shapes:

1. **Sprint mode** — `negotiate -> execute -> audit` for multi-phase work with validator-in-the-loop retries.
2. **Single-shot mode** — `ask`, `review`, `vote`, `compare`, `pipe`, and `team` for one-pass fan-out, decisions, and roster management.

## Command surface

- `lope/cli.py` owns argparse wiring and thin command handlers. It is intentionally kept backward-compatible; v0.7 should add new helpers instead of making this file the dumping ground.
- `lope/negotiator.py` drafts sprint documents and loops through validator review rounds.
- `lope/executor.py` runs sprint phases, handles two-stage validation, retries `NEEDS_FIX`, and produces execution reports.
- `lope/auditor.py` renders scorecards and writes Lope journal entries.
- `lope/ensemble.py` provides the parallel fan-out primitive and majority-vote synthesis.
- `lope/validators.py` contains built-in CLI adapters plus the `Validator` interface.
- `lope/generic_validators.py` supports user-defined subprocess and HTTP providers from config.
- `lope/makakoo_adapter.py` bridges registered Makakoo adapters into Lope without making public Lope depend on Makakoo.
- `lope/config.py` loads layered config from defaults, global config, project config, environment, and CLI flags.
- `lope/curl_parser.py` turns pasted curl examples into provider config.
- `lope/output.py` is the v0.7 seam for rendering structured output.
- `lope/redaction.py` is the v0.7 seam for secret scrubbing before memory, logs, and exports.
- `lope/findings.py` is the v0.7 data spine: `Finding` / `MergedFinding` / `ConsensusFinding`, parser, three-rule deduper, consensus scorer, deterministic markdown formatter.
- `lope/review.py` orchestrates `lope review --consensus`: prompt assembly, fan-out injection point, per-validator parser-exception isolation, multi-format renderer (text, json, markdown, markdown-pr, sarif).
- `lope/sarif.py` emits SARIF v2.1.0 with `lope.<category>.<severity>` rule ids, conservative severity → SARIF level mapping, and rich per-result properties.
- `lope/synthesis.py` runs the `--synth` executive summary against the primary with fail-soft semantics; anonymous mode rewrites validator names to `Response A/B/C` consistently across every prompt surface.
- `lope/memory.py` provides the SQLite-backed `LopeMemory` store + `FindingRecord` / `ReviewSessionRecord` + `LOPE_MEMORY=off` / `LOPE_MEMORY_DB` env switches; full redaction before any column is bound.
- `lope/makakoo_bridge.py` is the optional Makakoo OS bridge: `detect_makakoo`, `query_brain`, `write_brain_journal`, `write_auto_memory`, `build_context_block`. Pure detection, no import-time side effects.
- `lope/deliberation.py` owns the `lope deliberate` 7-stage council with 6 built-in templates (adr, prd, rfc, build-vs-buy, migration-plan, incident-review). Single label map at session start drives anonymization across every stage.
- `lope/divide.py` is the v0.7 chunker: directory walk + binary skip + line-anchored chunks + symlink containment vs. the original tree, unified-diff hunk parser, role lens catalog with aliases.
- `lope/exporters.py` ships markdown-pr / SARIF passthroughs to `lope/review.py` so callers have one consistent module entry point for export shapes.

## Data paths

- Global config: `~/.lope/config.json` unless `LOPE_HOME` points elsewhere.
- Project config: `./.lope/config.json` layered above global config.
- Journal: under Lope home; used by audit/history features.
- Installed skills/commands: host-native directories such as `~/.codex/skills`, `~/.claude/skills`, `~/.gemini/commands`, and the pi/shared agents skill tree.

## Public contracts

- Python package remains stdlib-only: `pyproject.toml` dependencies stay empty.
- Existing commands remain default-compatible. v0.7 structured intelligence is opt-in behind flags such as `--consensus`, `--synth`, `--remember`, and export formats.
- Validators must not raise from `validate()`. They return `ValidatorResult` with `INFRA_ERROR` for infrastructure failures.
- `generate()` is raw prompt execution used by single-shot fan-out and autonomous implementation. It may raise; fan-out captures per-validator errors.
- Sprint verdicts use `---VERDICT--- ... ---END---` with JSON preferred and YAML-ish fallback retained for compatibility.
- Public Lope does not require Makakoo. Makakoo integration activates through registered adapters or explicit bridge flags.

## v0.8 extraction candidates

`cli.py` is the current complexity hotspot. Avoid broad rewrites during v0.7. Good future extraction targets:

- Move `ask`, `vote`, `compare`, and `pipe` orchestration out of `cli.py` after consensus review lands.
- Move team management commands into a dedicated `lope/team.py` module.
- Unify human/JSON rendering through `lope/output.py` once structured output formats stabilize.
