# SPRINT-LOPE-RELIABILITY-FIXES

**Date:** 2026-05-04  
**Repo:** `/Users/sebastian/Projects/lope`  
**Current branch at creation:** `main`  
**Current HEAD at creation:** `151a820`  
**Priority:** Critical  
**Owner:** next Lope repair context  
**Do not use Lope to execute this sprint until Phase 1 passes.** Lope is the broken system under repair.

---

## Why this sprint exists

Lope is valuable, but currently too fragile. On 2026-05-04 during Tytus OS product-direction work, `lope ask --validators kimi,opencode --timeout 120` produced a valid `kimi` answer while `opencode` timed out. Investigation found multiple reliability bugs, not just a slow model.

The goal of this sprint is simple: make Lope boringly reliable. If a validator hangs, Lope must kill it cleanly, report the truth, and keep the team usable. If `lope team list` says a validator is active, `lope team test <name>` must work or fail with an accurate diagnostic.

---

## Confirmed bugs

### BUG 1: OpenCode validator times out on non-trivial prompts

**Observed command path:**

```bash
PYTHONPATH=/Users/sebastian/.lope python3 -m lope ask "...product direction prompt..." --validators kimi,opencode --timeout 120 --synth
```

**Observed result:**

- `kimi` responded.
- `opencode` timed out after 120 seconds.

**Direct reproduction:**

```bash
cd /Users/sebastian/Projects/makakoo/api/ProjectWannolot/services/tytus-os
PYTHONPATH=/Users/sebastian/.lope python3 - <<'PY'
from lope.validators import OpencodeValidator
import time, textwrap
prompt = textwrap.dedent('''
We are deciding product direction for Tytus OS. Current app store exposes Memo, Sheet, Studio, Code Editor, Markdown Preview, Text Editor. These are mediocre clone apps. Users already have better native tools. Propose one AI-native productivity app that unifies notes, docs, markdown, code snippets, tables, and agent collaboration without overengineering. Return concise product direction, architecture, first milestone, what to delete/hide, and risks.
''').strip()
v = OpencodeValidator()
start = time.time()
try:
    out = v.generate(prompt, timeout=120)
    print('OK', round(time.time() - start, 2), len(out))
except Exception as e:
    print('ERROR', round(time.time() - start, 2), repr(e))
PY
```

**Actual:**

```text
ERROR 120.01 RuntimeError('opencode run timed out after 120s')
```

**Root code:** `lope/validators.py:252-279`

```python
subprocess.run(
    [self._binary, "run", "--format", "json"],
    input=prompt,
    capture_output=True,
    text=True,
    timeout=timeout,
    cwd=self._workdir,
)
```

**Diagnosis:**

Bare `opencode run --format json` is not a lean validator call. It loads OpenCode's full agent context, tool registry, skills, MCP permissions, and then may inspect files or invoke tools before answering. Even `Say OK only.` took 15.17 seconds and used about 50k input tokens. For product prompts, it can burn the whole 120s budget before producing final text.

---

### BUG 2: Timeout leaves orphan OpenCode child processes

**Observed after repeated timeouts:**

```bash
ps -axo pid,ppid,etime,stat,command | rg 'opencode( |.*\.opencode) run .*--format json'
```

Found 11 stale `.opencode run --format json` child processes, several multiple days old, parented to PID 1.

**Root cause:**

Python `subprocess.run(... timeout=...)` kills/waits for the direct child process it launched, but OpenCode spawns a nested `.opencode` child. Lope does not launch validators in their own process group and does not kill descendants on timeout.

**Required behavior:**

On timeout, Lope must kill the whole validator process tree or process group. No orphan validator processes. Ever.

---

### BUG 3: `lope team test opencode` falsely says OpenCode is not on the team

**Reproduction:**

```bash
PYTHONPATH=/Users/sebastian/.lope python3 -m lope team list
PYTHONPATH=/Users/sebastian/.lope python3 -m lope team test opencode "Say OK only." --timeout 30
```

**Observed:**

```text
ERROR: 'opencode' is not on the team.
       Active: codex, pi, qwen, opencode, kimi.
       Run `lope team list` to see the full roster.
```

Contradiction: active list includes `opencode`, test says not on team.

**Root code:**

- `lope/cli.py:3027-3034`
- `lope/ensemble.py:43-56`

`_team_test()` does:

```python
pool = build_validator_pool(cfg)
validator = next(
    (v for v in getattr(pool, "validators", []) if getattr(v, "name", None) == name),
    None,
)
```

But `EnsemblePool` stores validators privately as `self._validators`, with only `names()`, `primary_validator()`, and `reviewers()` public. It has no `.validators` attribute. So `team test` sees an empty list and lies.

---

### BUG 4: Team health is not enforceable before sprint execution

Lope currently lets a broken validator stay in the active team, then fails mid-work. It needs a preflight health check that answers:

- Is binary present?
- Can it answer a 1-line prompt within timeout?
- Does it produce parseable text?
- Does timeout cleanup leave zero child processes?
- Is the configured team internally consistent?

---

## Sprint goal

Make Lope validator orchestration resilient enough that Sebastian can trust it during Tytus OS and future work.

Definition of done:

1. `lope team test opencode "Say OK only." --timeout 30` works or reports a true actionable error.
2. OpenCode timeout leaves no orphan `opencode run --format json` or `.opencode run --format json` processes.
3. OpenCode validator can be configured in a lean mode for Lope use.
4. Team health/preflight command catches broken validators before `ask`, `review`, `execute`, or `negotiate` starts.
5. Regression tests cover the bugs above.
6. Full test suite passes.

---

## Non-goals

- Do not redesign Lope.
- Do not replace OpenCode.
- Do not remove validator diversity.
- Do not make Lope depend on one model/provider.
- Do not solve every CLI's quirks in one sprint. Fix OpenCode and generic subprocess timeout hygiene first.

---

## Phase 1: Process timeout hygiene

### Files likely touched

- `lope/validators.py`
- `lope/generic_validators.py`
- new helper module if useful, e.g. `lope/processes.py`
- tests under `tests/`

### Work

Implement a shared subprocess runner for validators.

Requirements:

- Runs child in a new process group/session.
- On timeout, kills the whole process group/tree.
- Captures stdout/stderr safely.
- Preserves existing error messages where possible.
- Works on macOS/Linux.
- Has a Windows fallback or explicit tested behavior if Windows support is not yet implemented.

Suggested API:

```python
run_validator_subprocess(
    command: list[str],
    input_text: str,
    timeout: int,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]
```

### Tests

Add tests with a helper script that spawns a child/grandchild sleeping process, then times out. Assert no descendant remains after timeout.

Acceptance commands:

```bash
cd /Users/sebastian/Projects/lope
PYTHONPATH=/Users/sebastian/.lope python3 -m pytest tests/test_validator_processes.py -q
PYTHONPATH=/Users/sebastian/.lope python3 -m pytest tests/test_team.py tests/test_new_verbs.py -q
```

---

## Phase 2: Fix `lope team test` for built-in validators

### Files likely touched

- `lope/cli.py`
- `lope/ensemble.py` if adding a public accessor
- `tests/test_team.py`

### Work

Fix `_team_test()` so it can locate validators inside both `ValidatorPool` and `EnsemblePool`.

Options:

1. Add a public `validators()` or `iter_validators()` method to both pool types.
2. Add a helper in `cli.py` that checks `_validators` as a fallback.

Preferred: public accessor. Less hacky.

### Tests

Add regression test:

- Config validators: `['codex', 'pi', 'qwen', 'opencode', 'kimi']`
- `build_validator_pool(cfg)` returns `EnsemblePool`
- `_team_test` can find `opencode`
- Error message never says “not on the team” if the name is present in `cfg.validators`

Acceptance:

```bash
cd /Users/sebastian/Projects/lope
PYTHONPATH=/Users/sebastian/.lope python3 -m pytest tests/test_team.py -q
```

Manual smoke:

```bash
PYTHONPATH=/Users/sebastian/.lope python3 -m lope team test opencode "Say OK only." --timeout 30
```

---

## Phase 3: Lean OpenCode validator mode

### Files likely touched

- `lope/validators.py`
- `lope/config.py` if adding config fields
- `docs/` or `README.md`
- tests

### Work

Make OpenCode validator configurable so Lope can avoid heavy default behavior.

Current command:

```bash
opencode run --format json
```

Problems:

- loads full skills/tools/MCP
- may invoke tools before answering
- huge token preamble
- slow and unstable for simple validator duties

Candidate command shape:

```bash
opencode run --pure --format json --agent <validator-agent> --model <provider/model>
```

Need verify exact OpenCode config support before implementation. If custom agent config is too much for this sprint, at minimum:

- allow env override: `LOPE_OPENCODE_ARGS="--pure --model deepseek/deepseek-v4-flash"`
- allow config provider override by replacing hardcoded built-in with a custom provider command
- document recommended config

Hard requirement: do not silently change Sebastian's global OpenCode setup. Lope should control its own validator invocation.

### Tests

- Unit test command assembly.
- Unit test JSON event extraction still works.
- Integration smoke can be opt-in behind env flag because it calls real OpenCode.

Manual smoke:

```bash
cd /Users/sebastian/Projects/makakoo/api/ProjectWannolot/services/tytus-os
PYTHONPATH=/Users/sebastian/.lope python3 -m lope team test opencode "Say OK only." --timeout 45
```

Expected: returns `OK` or a short text answer inside timeout, no tool spelunking.

---

## Phase 4: Team health/preflight

### Files likely touched

- `lope/cli.py`
- `lope/config.py`
- `tests/test_team.py`
- docs

### Work

Add or strengthen a health command:

```bash
lope team health --timeout 30
```

If existing command naming conflicts, use:

```bash
lope team test --all --timeout 30
```

Required output per validator:

```text
opencode  FAIL  timeout after 30s, killed 2 processes, no orphan children
kimi      PASS  2.4s, 128 chars
pi        PASS  5.1s, 200 chars
qwen      FAIL  binary not found
```

Required exit behavior:

- exit 0 if all active validators pass
- exit non-zero if any active validator fails
- print actionable fix suggestions

Preflight integration:

- `lope ask`, `lope review`, `lope negotiate`, `lope execute` should have optional `--preflight` or config default.
- Do not make every command slow by default unless config says so.
- For `execute`, preflight should be strongly recommended or default-on.

---

## Phase 5: Regression gates and docs

### Required test commands

```bash
cd /Users/sebastian/Projects/lope
PYTHONPATH=/Users/sebastian/.lope python3 -m pytest -q
PYTHONPATH=/Users/sebastian/.lope python3 -m lope team list
PYTHONPATH=/Users/sebastian/.lope python3 -m lope team test opencode "Say OK only." --timeout 45
ps -axo pid,ppid,etime,stat,command | rg 'opencode( |.*\.opencode) run .*--format json' || true
```

The final `ps` command must show no stale Lope-launched OpenCode validator processes after timeout tests.

### Docs to update

- `README.md` or relevant Lope docs: validator reliability and health checks
- `CHANGELOG.md`: Lope reliability fixes
- Config docs: OpenCode lean mode / env vars / team health

---

## Suggested implementation order

1. Add shared safe subprocess runner and tests.
2. Wire it into `OpencodeValidator.generate()` and `.validate()`.
3. Wire it into generic subprocess providers.
4. Fix `team test` pool access.
5. Add lean OpenCode args/config.
6. Add team health/preflight.
7. Run full tests.
8. Manual OpenCode smoke.
9. Commit.

---

## Reviewer escalation

If blockers arise, query the Lope Team with technical focus:

- `kimi`: architecture, product-level tradeoff, minimal surface area
- `OpenCode`: implementation edge cases, CLI/process handling, test design

Because OpenCode validator is broken inside Lope, do **not** rely on Lope's OpenCode validator until Phase 1 and Phase 2 are fixed. If OpenCode input is needed before that, call OpenCode directly or use another independent path.

Suggested external question:

```text
We are repairing Lope validator orchestration. Bugs: subprocess timeout leaves orphan nested opencode children; team test cannot find built-in opencode because EnsemblePool stores _validators privately; opencode run --format json loads heavy tools and times out. Review this fix plan: process-group runner, public pool validator accessor, lean opencode args/config, team health preflight. What edge cases are missing?
```

---

## Fresh-context continuation prompt

Paste this into a new context window to execute the sprint:

```text
You are Harvey. Work in /Users/sebastian/Projects/lope.

Read this sprint first:
/Users/sebastian/Projects/lope/development/sprints/2026-05-04-lope-reliability-fixes/SPRINT.md

Do not use Lope to execute its own repair until Phase 1 passes. Implement the sprint in order:
1. safe validator subprocess runner that kills process groups/descendants on timeout
2. fix `lope team test opencode` for EnsemblePool
3. add lean OpenCode validator args/config
4. add team health/preflight
5. regression tests and docs

Known failing evidence:
- `OpencodeValidator.generate()` times out with `RuntimeError('opencode run timed out after 120s')`
- `lope team test opencode "Say OK only." --timeout 30` falsely says opencode is not on the team while `lope team list` shows it active
- stale `.opencode run --format json` orphan processes were found after timeouts

Run gates:
PYTHONPATH=/Users/sebastian/.lope python3 -m pytest -q
PYTHONPATH=/Users/sebastian/.lope python3 -m lope team test opencode "Say OK only." --timeout 45
ps -axo pid,ppid,etime,stat,command | rg 'opencode( |.*\.opencode) run .*--format json' || true

If blockers arise, query kimi and OpenCode for technical review, but do not rely on the broken Lope OpenCode validator path until fixed.
```
