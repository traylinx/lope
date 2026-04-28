# Lope in CI — SARIF and PR comments

`lope review --consensus --format sarif` and `--format markdown-pr` are designed for engineering CI pipelines. SARIF goes to GitHub code-scanning (or any scanner aggregator that reads the SARIF v2.1.0 schema); the PR-comment renderer drops a compact merged-findings table into a pull-request thread.

This doc documents the integration shapes; the workflow files are illustrative rather than shipped under `.github/workflows/` so projects can adapt them to their own CI.

---

## SARIF upload to GitHub code-scanning

The most useful pipeline: review the diff, emit SARIF, upload to GitHub. Findings become inline annotations on the PR plus first-class issues in the repo's Security tab.

```yaml
name: lope-review
on:
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: write
  security-events: write

jobs:
  consensus-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Build the diff against the base branch
        run: git diff origin/${{ github.base_ref }}...HEAD > /tmp/pr.patch

      - name: Install Lope
        run: pip install lope-agent==0.7.0

      - name: Run consensus review
        run: |
          lope review /tmp/pr.patch \
            --divide hunks \
            --consensus \
            --format sarif \
            --validators claude,gemini,codex \
            > /tmp/lope-review.sarif

      - name: Upload SARIF
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: /tmp/lope-review.sarif
          category: lope-consensus
```

The SARIF document carries per-result properties (`consensus_score`, `agreement_ratio`, `detected_by`, `dissenting`, `consensus_level`) so downstream filters can dial the noise floor. For example, GitHub's code-scanning advanced filters can drop everything below `consensus_level == confirmed`.

---

## Compact PR comment

For human review on the PR thread itself, render `--format markdown-pr` and post it as a comment.

```yaml
- name: Render PR comment
  id: lope
  run: |
    lope review /tmp/pr.patch \
      --divide hunks \
      --consensus \
      --format markdown-pr \
      --validators claude,gemini,codex \
      > /tmp/comment.md

- name: Post comment
  uses: actions/github-script@v7
  with:
    script: |
      const fs = require('fs');
      const body = fs.readFileSync('/tmp/comment.md', 'utf8');
      await github.rest.issues.createComment({
        ...context.repo,
        issue_number: context.issue.number,
        body
      });
```

The PR-comment body is a markdown table sorted by severity → consensus_score → file/line. With `--include-raw` it appends collapsible `<details>` blocks per validator so reviewers can inspect the full per-model response without bloating the default view.

---

## Quality gate: fail the build on confirmed findings

If you want the review to act as a gate (not just an annotation), parse the JSON output and exit non-zero on confirmed findings.

```yaml
- name: Run consensus review (JSON)
  id: review
  run: |
    lope review /tmp/pr.patch \
      --divide hunks \
      --consensus \
      --format json \
      > /tmp/lope-review.json
    # Fail if any finding is confirmed at high severity or above.
    BLOCKING=$(jq '[.findings[]
      | select(.consensus_level == "confirmed")
      | select(.severity == "high" or .severity == "critical")] | length' \
      /tmp/lope-review.json)
    echo "Blocking findings: $BLOCKING"
    [ "$BLOCKING" = "0" ]
```

Pair `--min-consensus 0.6` with this to filter out low-agreement noise before the gate runs.

---

## Memory inside CI

`--remember` is safe to use in CI — but the SQLite store lives in `~/.lope/memory.db` by default, which is ephemeral on a fresh runner. Two viable shapes:

1. **Per-PR memory only** (default ephemeral store): the consensus output already carries the `Recurring: seen N times` note across the runner's local DB; the recurrence dimension is lost across runs. Useful when you only care about within-PR dedup.
2. **Persistent memory**: cache `~/.lope/memory.db` between runs. GitHub Actions has a `cache` action; point it at `~/.lope/memory.db` keyed on the default branch. Lope is stdlib SQLite, so the DB file is portable across runners.

```yaml
- name: Restore lope memory
  uses: actions/cache@v4
  with:
    path: ~/.lope/memory.db
    key: lope-memory-${{ github.ref_name }}-${{ github.run_id }}
    restore-keys: |
      lope-memory-${{ github.ref_name }}-
      lope-memory-main-

- name: Run review
  run: lope review /tmp/pr.patch --divide hunks --consensus --remember
```

Set `LOPE_MEMORY_DB=/tmp/lope-memory.db` to relocate the file if you'd rather keep it out of the runner's home directory.

---

## Hard rules

- **No new dependency.** `lope review` runs against `pip install lope-agent` — no extra packages required.
- **Never commit the SARIF.** Treat it as build output. The `upload-sarif` action ingests it directly into GitHub.
- **Validators in CI.** Use cloud-API validators (claude, gemini, codex) plus `--require-all` only when you're certain every validator will be available. The default behavior is fail-soft so a transient outage in one CLI does not blank the run.
- **Redaction.** Every text path through Lope passes through `lope.redaction.redact_text` before reaching disk or stdout. Bearer tokens, sk-* keys, GitHub PATs, and PEM blocks are scrubbed automatically.

For the broader command surface and flag glossary, see [reference.md](reference.md).


## Objective gates in CI

For pass/fail checks that should not require model calls, use `lope check`. The commands come from `./.lope/rules.json` and can wrap your existing test/lint/build/coverage scripts.

```json
{
  "gates": [
    {"name": "tests", "cmd": "python -m pytest tests -q", "type": "exit", "required": true},
    {"name": "coverage", "cmd": "python -m coverage json -o -", "type": "json_number", "path": "totals.percent_covered", "min_value": 80}
  ]
}
```

```yaml
- name: Run Lope objective gates
  run: lope check --json > /tmp/lope-gates.json

- name: Upload gate report
  uses: actions/upload-artifact@v4
  if: always()
  with:
    name: lope-gates
    path: /tmp/lope-gates.json
```

To compare against a saved baseline in a longer agentic workflow:

```bash
lope gate save --json > /tmp/gates-before.json
# run agent / lope execute / migration
lope gate check --json > /tmp/gates-after.json
```

Gate commands are project-authored shell commands. Treat `.lope/rules.json` like CI config: trusted repo input, deterministic commands, bounded timeouts.

