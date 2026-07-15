# SPRINT-015 — Deadline budgeting, request shaping, and orphan-safe job lifecycle

**Date:** 2026-07-15
**Repository:** `/Users/sebastian/Projects/lope`
**Baseline:** `main` at `ffad7d5e7b9d17303ecedbe8d6dd04ee26cf962c` (`v0.13.1`)
**Status:** READY FOR IMPLEMENTATION AFTER APPROVAL
**Planned release:** `v0.14.0` because this adds public runtime-budget and request-planning behavior
**Planning constraint:** This document is the only repository artifact created during the investigation. No production code was changed.

## Origin

Sebastian reported intermittent Lope timeouts and suspected that some requests are too large or take too long. He asked for a complete investigation of every execution path, improvement options such as smaller chunks, and an implementation-ready sprint without code changes. He then added a second hard requirement: old or broken jobs must not remain behind consuming computer resources.

## Executive verdict

The problem is real, but “increase the timeout” is the wrong fix. Current Lope has six interacting failure classes:

1. `--timeout` is a per-call limit, not a command-wide deadline. Multi-round modes can legally multiply one timeout into hours.
2. Several fan-out and subprocess paths cannot enforce a hard ceiling. An external adapter that ignores its timeout can hold the process indefinitely or leave descendants alive.
3. Request admission exists only as an advisory negotiate warning. Most modes accept unbounded prompts and responses, often transporting the full prompt through argv.
4. Existing manual chunking reduces individual prompt size but can explode total calls and wall time because chunks run one after another through the entire validator team.
5. Telemetry is too weak to distinguish queue delay, process startup, model latency, parsing failure, rate limiting, cancellation, and budget exhaustion.
6. Process cleanup is reactive and non-durable. Lope has no persistent run/call ownership registry, no parent-death supervisor, no safe stale-job reconciler, and no way to distinguish a Lope orphan from Sebastian's unrelated live CLI sessions.

The correct architecture is a **budget-aware request planner** backed by one cancellation-safe invocation layer and an **orphan-safe job supervisor**. Smaller chunks are part of the answer, but only when Lope also caps chunk count, forecasts calls, preserves context, bounds synthesis, stops scheduling work when the command budget is exhausted, and proves that every process it launched was either reaped or safely handed to reconciliation.

## Non-negotiable runtime invariants

1. `--timeout` remains the hard ceiling for one external call. It includes startup, primary request, compatibility fallback, response read, parsing, and cleanup.
2. New `--run-timeout` is an absolute monotonic deadline for the whole command. Every retry, fallback, synthesis, gate, and self-heal step consumes the same budget.
3. No call starts until it atomically reserves remaining wall time, one external-call credit, and its input/output byte allowance.
4. A timed-out subprocess leaves no live child or grandchild. This applies to every built-in validator, generic provider, Makakoo adapter, gate, and self-heal smoke path.
5. A prompt is profiled before launch: UTF-8 bytes, lines, conservative token estimate, transport, planned calls, and worst-case wall ceiling.
6. Large input routes to `direct`, `chunk`, or `reject` deterministically. It never silently creates an unbounded number of calls.
7. Validator stdout, stderr, HTTP bodies, and synthesis inputs have explicit byte ceilings. Truncation is visible and never reported as complete output.
8. Timeouts and partial failures preserve completed results. Budget exhaustion is a typed result, not a blank `INFRA_ERROR`.
9. Consensus-shaped commands require a substantive-result quorum. Tool intent, empty output, unparseable output, or zero usable contributors can never exit 0 as a clean review.
10. Existing `Validator.generate(prompt, timeout)` and `Validator.validate(prompt, timeout)` callers remain source-compatible through wrappers around a versioned internal invocation context.
11. Hard cancellation is guaranteed for Lope’s built-in and configured external adapters. Third-party in-process Python validators must adopt the new cooperative context or opt into a killable worker boundary; Lope must not promise that Python can safely kill an arbitrary stuck thread.
12. Stdlib-first remains mandatory. Do not replace Lope with an async framework, workflow engine, queue system, or tokenization dependency.
13. Every external call has a durable, sanitized ownership lease before provider launch. The lease identifies the Lope run, owner, supervisor, child process group, deadline, and owned temporary paths without storing prompts, response bodies, secrets, or full argv.
14. Parent death closes a control pipe watched by a separate supervisor. The supervisor terminates the provider group, force-kills after a bounded grace period, calls `wait()`/`waitpid()` until all direct children are reaped, and records the cleanup result.
15. Startup and explicit reaping act only on positively identified Lope-owned jobs. PID plus process-start fingerprint and run/call identity must match; a stale heartbeat alone is never permission to kill a live process. Broad process-name matching and `pkill -f` are forbidden.
16. Lope exposes active/stale job count, age, deadline, state, and best-effort CPU/RSS. Completed metadata and owned temporary/spool files have bounded retention; cleanup never follows an untrusted path outside the Lope-owned run root.

## Confirmed baseline

### Active configuration

`~/.lope/config.json` currently resolves to:

- Validators: `codex`, `claude`, `pi`
- Primary: `codex`
- Parallel ensemble: enabled
- Global per-call timeout: `900s`
- Pi provider cap: `360s`
- Pi transport: argv via `pi --no-tools --no-context-files --no-session -p {prompt}`

Installed runtime parity was checked: `/Users/sebastian/.lope/lope/` matches the source repo’s `lope/` package exactly, excluding caches.

### Real health baseline

`PYTHONPATH=. python3 -m lope team health --timeout 30 --json` passed all three validators:

| Validator | Result | Tiny-prompt latency | Output |
|---|---:|---:|---:|
| codex | PASS | 4.9s | 2 chars |
| claude | PASS | 13.1s | 2 chars |
| pi | PASS | 4.7s | 2 chars |

This rules out a generally dead team. Failures are workload- and orchestration-dependent.

### Historical evidence already in the repo

- Sprint 014 recorded a 13KB, 357-line negotiate prompt timing out at 120s while a compact 4KB, 79-line brief completed with 300s.
- The 2026-05-04 reliability sprint recorded OpenCode loading roughly 50K input tokens for a trivial run, timing out, and leaving descendants.
- The 2026-05-17 reliability sprint fixed provider timeouts that could override shorter CLI timeouts and added a deadline around `_fanout_generate`.
- Three recorded Flow runs took 116s, 141s, and 125s, but every per-node duration was journaled as `0s`.
- One Flow escalation became `all validators infra error:` with no actual reason.
- Lope finding memory currently contains zero findings matching `timeout`.
- No stale process attributable to the Lope run under investigation was present during the initial narrow check. A later machine-wide audit found two unowned validator-shaped orphans; R9 records why Lope cannot currently identify or safely reap them.

## Reproduced defects

All reproductions used local stubs or existing read-only commands. None changed production code.

### R1 — `EnsemblePool.validate` has no fan-out deadline

A fast validator and a stub validator that slept for five seconds were passed `timeout=0.2`. An outer 1.2-second harness had to kill the process. `EnsemblePool.validate` never returned because `as_completed()` at `lope/ensemble.py:110-133` has no deadline.

### R2 — `_fanout_generate` returns, but the Python process still cannot exit

A stub that ignored `timeout=0.2` caused `_fanout_generate` to return a timeout tuple after 0.46s. The child Python process was still alive after 1.5s because `ThreadPoolExecutor.shutdown(wait=False)` does not cancel a running worker and Python joins executor workers during interpreter shutdown. The current regression test proves early function return, not process termination.

### R3 — built-in validator timeout leaves a descendant alive

A fake Claude binary spawned `sleep 20`. `ClaudeCodeValidator.generate("prompt", timeout=1)` returned `claude timed out after 1s`, but the descendant remained alive and required explicit `SIGKILL`. Claude, Gemini, Codex, and Aider still use plain `subprocess.run` in `lope/validators.py`.

### R4 — Makakoo adapter exceeds its advertised call timeout

A fake Makakoo adapter slept two seconds and was called with `timeout=1`. It returned PASS after 2.29s because `lope/makakoo_adapter.py:93-106` gives the wrapper `timeout + 30` seconds.

### R5 — OpenCode export fallback receives a second timeout

A fake OpenCode run and export each slept 1.6s. `OpencodeValidator.generate("prompt", timeout=3)` succeeded after 3.62s. The initial run and `opencode export` at `lope/validators.py:458-480` use separate timeout allocations.

### R6 — HTTP timeout is an idle socket timeout, not a total deadline

A local HTTP server trickled response chunks every 0.2s. `GenericHttpValidator.validate("prompt", timeout=0.3)` returned after 1.03s. `urlopen(timeout=seconds)` plus unbounded `resp.read()` at `lope/generic_validators.py:318-325` does not enforce total wall time when bytes keep arriving.

### R7 — argv has a real hard limit

On this machine, `getconf ARG_MAX` is 1,048,576 bytes. Empirical single-argument execution succeeded through 1,041,993 bytes and failed with `E2BIG` at 1,041,994 bytes. Several built-ins and auto-discovered providers inject the entire prompt into argv. Long before the hard failure, the prompt is also visible in process listings.

### R8 — full sprint dogfood timed out and still exited successfully

Lope reviewed the 44KB first draft of this sprint with `codex`, `claude`, and `pi` at `--timeout 180`. Codex and Claude both timed out. Pi returned intent to run a shell tool but no actual review. Consensus output contained `fallback: true`, zero findings, and still exited 0. This confirms both size sensitivity and a fail-open result-validity bug.

A compact 3.5KB validation brief at the same timeout produced substantive Codex and Pi reviews while Claude still timed out. Lope nevertheless waited for the full slow tail. The useful reviewers independently confirmed these blockers:

- hard cancellation must be scoped to killable external calls or a versioned cooperative adapter boundary;
- a zero-usable-result consensus must be `inconclusive` and non-zero;
- wall time alone does not cap fast call/cost amplification;
- `--run-timeout 0` is too easy an escape hatch;
- HTTP total deadlines need a killable isolation boundary for DNS/blocking-read cases;
- the first release should defer resume/persistent adaptive machinery rather than ship every optimization at once.

### R9 — old validator-shaped jobs are consuming CPU with no ownership evidence

A read-only machine audit on 2026-07-15 found PIDs `70127` and `73436`: two `gemini` shell wrappers started on 2026-07-10, reparented to PID 1, each leading its own process group and still consuming roughly 1.9% CPU after five days. Their accumulated CPU times were approximately 106 and 105 minutes. Both retained open prompt/response/log descriptors under a deleted temporary Scoutica scan runtime; each response and provider log remained zero bytes. No zombie processes were present.

These jobs are clearly abandoned, but they cannot be proven to have been launched by Lope. They contain no run ID, call ID, owner fingerprint, manifest, or environment ownership marker. Killing by command text would risk terminating unrelated Gemini/Codex/Claude sessions. They were therefore not killed during this planning-only investigation. The evidence proves the lifecycle requirement: Lope must register ownership before launch and reap only positively identified Lope jobs.

`~/.lope/run.lock` existed as an empty zero-byte file with no open holder. That is normal `flock` behavior, not a stale job. The age or existence of the lockfile must never be used as a kill signal. No `~/.lope/runs/` registry currently exists.

## Static failure-path inventory

### Deadline and retry multiplication

| Path | Current behavior | Failure |
|---|---|---|
| `lope/cli.py:1746-1802` | Drafter tries primary, every other validator, then optional HTTP fallback | One failed drafting call can consume `900 + 900 + 360 = 2160s` before HTTP fallback |
| `lope/negotiator.py:191-224` | Initial draft plus two lint retries | Up to three full drafter chains per negotiation round |
| `lope/negotiator.py:226-277` | Up to three proposal/review rounds | No command budget or call forecast |
| `lope/executor.py:129-289` | Three attempts, implementation plus spec and quality review | Up to nine model-call waves per phase before gates |
| `lope/deliberation.py:664-764` | Every stage loops validators sequentially | Standard/deep protocol makes `4N + 1` sequential calls |
| `lope/flow/runner.py:290-446` | Visit count bounds handler executions | Ensemble nodes make N calls inside one counted handler; documented model-call bound is false |
| `lope/gates.py:210-277` | Gate specs run sequentially | Total is the sum of all gate timeouts; execute can run the suite four times per phase |
| `lope/healer.py:118-168` | Help 10s, reviewer 120s, smoke 60s | Fixed 190s side path ignores caller/run budget, then implementation retries |

### Cancellation and transport

| Path | Current behavior | Failure |
|---|---|---|
| `lope/processes.py:61-78` | Uses `preexec_fn=os.setsid` | `preexec_fn` is unsafe when invoked from Lope’s worker threads and can deadlock before `communicate()` starts its timeout |
| `lope/validators.py:995-1079` | Gemini uses plain `subprocess.run` | Direct child only; captured output unbounded |
| `lope/validators.py:1296-1377` | Claude uses plain `subprocess.run` | Direct child only; prompt in argv; output unbounded |
| `lope/validators.py:1407-1526` | Codex uses plain `subprocess.run` | Direct child only; prompt in argv; output unbounded |
| `lope/validators.py:1556-1645` | Aider uses plain `subprocess.run` | Direct child only despite supporting `--message-file` |
| `lope/makakoo_adapter.py:88-127` | Plain subprocess with `timeout + 30` | Ceiling violation and descendant risk |
| `lope/gates.py:215-277` | `shell=True` with plain timeout | Shell descendants can survive; output is collected in full before tailing |
| `lope/healer.py:188-247` | Help and smoke use plain subprocesses | Same descendant and output risks |
| `lope/makakoo_bridge.py:173-224` | Brain search uses plain subprocess | Context preflight has a separate untracked timeout path |

### Request and response size

| Path | Current behavior | Failure |
|---|---|---|
| `lope/cli.py:45-94` | Negotiate prints bytes/lines warning only | Advisory only; no token estimate, routing, or rejection |
| `lope/cli.py:2753-3386` | ask/review/vote/compare/pipe inline full input | No common admission check; stdin and file reads are unbounded |
| `lope/divide.py:35-43` | 16K-char file chunks and 256K per-file cap | No total chunk cap, no small-file packing, no overlap; a single long line can exceed 16K |
| `lope/divide.py:372-452` | One validator fan-out per diff hunk | A small diff with many hunks creates many calls; a single huge hunk is not split |
| `lope/synthesis.py:140-254` | Concatenates every successful raw response | Synthesis input can exceed the original request by N times |
| `lope/flow/model.py:367-385` | Blackboard stores full `result.raw` as `{node.out}` | Downstream Flow prompts can grow on every hop |
| `lope/deliberation.py:477-564` | Peer blocks concatenate all prior turns | Critique, revision, and synthesis prompts compound council output |
| `lope/generic_validators.py:320-323` | HTTP response uses unbounded `resp.read()` | Remote endpoint can exhaust memory or disk |
| `lope/processes.py:61-85` | stdout/stderr are unbounded pipes | Validator output can exhaust memory and block cleanup |

### Correctness and diagnostics discovered during timeout tracing

1. `EnsemblePool.synthesize()` chooses `ValidatorResult.error`; parse failures often keep the reason only in `verdict.rationale`, producing blank ensemble errors.
2. `Negotiator.refine()` replaces original evidence context with the previous proposal, and `_build_validator_prompt()` never includes the original context. Reviewers therefore cannot verify a draft against the evidence that produced it.
3. `GenericHttpValidator` implements `validate()` but not `generate()`, so HTTP teammates do not work consistently across ask, draft, deliberate, synthesis, and team-test paths.
4. `lope/curl_parser.py:403-410` and `lope/cli.py:3813-3819` add a literal JSON key named `{max_tokens}`. `_substitute_prompt()` replaces values, not keys, so this does not produce a provider `max_tokens` field. `DEFAULT_MAX_TOKENS` in `validators.py` is otherwise unused.
5. Learned self-heal adapters are persisted but never consumed by `build_validator_pool()`. A successful heal retries the same built-in adapter, and a new `SelfHealer` instance can repeat work in later phase attempts.
6. Role-based review at `lope/cli.py:2499-2510` ignores parallel configuration and calls validators sequentially.
7. `_fanout_generate()` always runs in parallel even when `--sequential` was requested.
8. Timeout defaults drift: layered config and validator defaults use 960s, while hydration, selector, ensemble, generic adapters, Flow, docs, and several fallback constructors use 480s. Gate timeout is also 480s but is a different concept and should remain separately named.

### Job ownership, stale-state, and resource cleanup

| Path | Current behavior | Failure |
|---|---|---|
| `lope/processes.py:61-121` | Cleans a process group only after `communicate()` raises timeout | Top-level `SIGKILL`, interpreter crash, dead worker thread, or host termination before the timeout handler can leave the provider group alive |
| `lope/processes.py:72-78` | Context-manager exit and a final `communicate()` are expected to reap the direct child | No independent supervisor watches parent death; no durable record exists if cleanup itself hangs or the process is killed between signal and wait |
| `lope/runlock.py:37-119` | Serializes four heavy modes through `flock`; lockfile content is only PID plus command | Lock ownership has no start fingerprint; the empty file persists normally; it is not a job registry and cannot identify child processes or resources |
| `lope/runlock.py:83-95` | Lock conflict advice tells the user to run `pkill -f 'python3 -m lope'` | Broad pattern kill can terminate unrelated healthy runs and provides no child-tree cleanup proof |
| Built-in/generic adapters | Do not inject a Lope run/call identity into provider environments | An orphan cannot later be distinguished from Sebastian's manually launched CLI session |
| Prompt files, HTTP workers, stdout/stderr spools | Lifetime is local to the call stack | Crash leftovers have no ownership manifest, safe path boundary, retention policy, or startup reconciliation |
| `lope status` | Shows validator/config availability | Does not show run owner, active/stale jobs, deadlines, CPU/RSS, cleanup failures, or retained artifacts |

## Request-size measurements

Conservative token estimates below use four characters per token only for comparison. Implementation must use a UTF-8-aware upper-bound estimate and label it as an estimate.

| Input | Generated/request size | Approximate tokens |
|---|---:|---:|
| Raw review of `lope/cli.py` | 158,544 bytes | 39.5K |
| Raw review of `lope/validators.py` | 64,325 bytes | 15.8K |
| Raw review of `README.md` | 45,396 bytes | 11.1K |
| Negotiate with prior reliability sprint as context | 15,106 bytes | 3.8K |
| Synthesis of three 100K-character answers | 301,809 bytes | 75.5K |

Current manual splitting creates the following plans:

| Target | Chunks | Validator calls with active team | Nominal timeout ceiling |
|---|---:|---:|---:|
| `lope/` package | 70 | 210 | 17.50h |
| `tests/` | 47 | 141 | 11.75h |
| Entire repository | 221 | 663 | 55.25h |
| Latest 32KB commit diff | 38 hunks | 114 | 9.50h |

These are worst-case ceilings rather than expected latencies, but they prove that naive “split everything smaller” is not sufficient. The latest diff is only 32KB, yet one-call-per-hunk creates 38 fan-outs.

## Current worst-case runtime matrix

For the active team:

- `P = max(call caps) = 900s` for a parallel fan-out
- `S = sum(call caps) = 2160s` for a sequential full-team pass
- `D = primary cap = 900s`
- `N = 3` validators

| Mode | Current maximum call shape | Active nominal wall ceiling |
|---|---|---:|
| ask/pipe/vote/compare/raw review | one parallel team pass | `P = 15m` |
| same with `--synth` | team pass plus primary synthesis | `P + D = 30m` |
| role review | sequential team pass | `S = 36m`, plus 15m synthesis |
| divided review with C chunks | C sequential team passes | `C × P`; `lope/` is 17h30m |
| negotiate, clean lint and no fallback, three rounds | three drafter calls plus three reviews | `3D + 3P = 1h30m` |
| negotiate, maximum lint retries and full drafter fallbacks | nine full fallback chains plus three reviews | `9S + 3P = 6h09m` |
| negotiate with sequential reviews | nine fallback chains plus three sequential reviews | `12S = 7h12m` |
| execute/implement, one phase, parallel reviews | three implementations plus six team reviews | `3 × (D + 2P) = 2h15m` |
| execute/implement, one phase, sequential reviews | three implementations plus six sequential reviews | `3 × (D + 2S) = 4h21m` |
| deliberate quick | positions, synthesis, rubric | `2S + D = 1h27m` |
| deliberate standard/deep | positions, critiques, revisions, synthesis, rubric | `4S + D = 2h39m` |
| Flow with 50 serial ensemble nodes | 150 model calls hidden inside 50 handler visits | 12h30m parallel; 30h sequential |
| gate/check with G gates | one sequential gate suite | sum of each gate’s configured timeout |
| execute with gates | baseline plus up to three gate suites | up to four times the gate-suite ceiling per phase |

No current mode propagates a remaining command budget into these calls.

## Target runtime policy

### Public timeout semantics

- `--timeout SECONDS`: per external model/provider call, unchanged for compatibility.
- `--run-timeout SECONDS`: positive whole-command deadline.
- `--allow-unbounded-run`: explicit unsafe escape hatch. It removes only the wall deadline; per-call timeout, maximum calls, chunks, and byte limits remain mandatory.
- `LOPE_RUN_TIMEOUT`: environment override below the CLI layer.
- Provider timeout remains a shorter cap only: `min(provider_timeout, call_timeout, remaining_run_budget)`.
- Gate timeouts remain separate from model defaults and are always clamped to remaining run budget.
- Default `max_external_calls` is 96, default total model-input allowance is 16MiB, and default total retained model-output allowance is 32MiB. Mode forecasts may choose a lower call ceiling but never a higher one without explicit override.

### Default run ceilings

| Command family | Default run timeout |
|---|---:|
| ask, review, vote, compare, pipe, team health | 1800s |
| negotiate, deliberate | 3600s |
| execute, implement, flow | 7200s |
| gate, check | 1800s |

These are safety ceilings, not targets, and intentionally do not fund every theoretical retry path. Users can lower them. Removing the wall ceiling requires the explicit unsafe flag.

### Request-policy defaults

- `auto` is the default: direct when all selected adapters can safely transport the prompt; otherwise chunk where the mode has a lossless strategy; otherwise reject before launch.
- `direct` forces one prompt but still obeys argv, provider, and hard-byte limits.
- `chunk` forces the mode’s hierarchical strategy.
- Default maximum chunks: 32. Raising it requires `--max-chunks` and prints the updated call forecast.
- Default chunk workers: 1 by design because every chunk already fans out across the full validator team. A user may raise bounded concurrency, but Lope must show the resulting provider concurrency before launch.
- Mandatory argv transport is capped at 128KiB by default even when the OS reports a larger `ARG_MAX`.
- Input limits are bytes, not Python characters. Token counts are conservative estimates only.
- The common token estimate is `ceil(UTF-8 bytes / 3)` and is labelled conservative. It is never used as the hard safety boundary.

### Default byte ceilings

| Surface | Default hard ceiling |
|---|---:|
| one subprocess/HTTP stdout body | 2MiB |
| one stderr body | 512KiB |
| one source included in synthesis | 256KiB |
| total synthesis prompt | 1MiB |
| one Flow blackboard inline value | 512KiB |
| total Flow blackboard inline data | 8MiB |
| total model input retained/accounted per run | 16MiB |
| total model output retained per run | 32MiB |

Crossing a ceiling returns a typed incomplete result. It never silently drops the remainder and reports success.

### Transport preference

1. stdin when the CLI supports a clean non-interactive stdin mode.
2. Secure temporary prompt file (`0600`, deleted after call) when the CLI supports file input, such as Aider `--message-file` or Pi `@file`.
3. argv only when required by the installed CLI version, with the explicit argv ceiling.
4. HTTP JSON body with total deadline and request/response byte caps.

Adapter capability must be declared and tested; do not guess from CLI name. OpenCode’s current version-specific positional prompt behavior remains supported.

### Job ownership and reaping policy

- Internal lifecycle state lives under `$LOPE_HOME/runs/`: `active/<run_id>.json`, `completed/`, and per-run private `work/<run_id>/`. Directories are mode `0700`; manifests and transient files are mode `0600`.
- A separate short-held registry lock protects atomic write/rename/reconcile operations. It is not the command-wide `run.lock`, and `lope jobs` must remain usable while another Lope run holds the command lock.
- The run manifest records schema version, run ID, mode, owner PID and process-start fingerprint, host boot fingerprint, start/heartbeat/deadline timestamps, state, call counters, and sanitized cleanup history.
- Each active call records call ID, validator, supervisor PID/start fingerprint, child PID/PGID/start fingerprint, a random non-secret ownership marker inherited through the provider environment, executable and redacted-command hashes, deadline, heartbeat, transport, and Lope-owned temp/spool paths. Prompt text, output bodies, credentials, full argv, and environment values are forbidden.
- The provider is launched in a process group separate from the supervisor. The supervisor owns bounded I/O and watches a parent control pipe. Normal completion, cancellation, parent pipe EOF, or deadline all converge on one idempotent `TERM → grace → KILL → wait/reap → cleanup → manifest` state machine.
- Liveness uses PID plus process-start fingerprint, never PID alone. Automatic reaping requires a dead/reused owner fingerprint and positive child ownership evidence. A live owner with an old heartbeat is reported as `unresponsive`; it is not automatically killed. Ambiguous identity is reported as `ownership_unverified` and left untouched.
- Reconciliation runs before external-work command admission and after normal completion. Signal handlers request the same cancellation path. The supervisor covers parent `SIGKILL`, where handlers and `atexit` cannot run. A later Lope invocation repairs any manifest left by simultaneous parent/supervisor death.
- `lope jobs list [--json]` shows run/call state, owner/supervisor/child liveness, age, deadline, and best-effort process count/CPU/RSS. `lope jobs reap [--dry-run]` reconciles confirmed abandoned jobs. `lope jobs kill <run-id>` cancels one positively identified Lope run. All three bypass the global command lock but serialize registry mutations.
- `lope status` adds a concise jobs summary and the exact safe `lope jobs list` or `lope jobs kill <run-id>` command when attention is required. Lock-conflict messages name the recorded run and use ownership-aware job commands; they never recommend `pkill`, `killall`, or command-name matching.
- Completed sanitized manifests retain seven days or 1,000 entries, whichever bound is reached first. Prompt files, HTTP-worker scratch, and I/O spools are removed immediately after confirmed cleanup. Crash cleanup deletes only canonical, non-symlink paths beneath the recorded Lope run work directory.
- A job is not “clean” until its provider group has no positively identified live member, every direct child has been waited, all owned transient paths are removed, and the final manifest is atomically moved out of `active/`. Cleanup failure remains visible and makes the command non-zero.

## Mode-specific shaping policy

| Mode | Oversize strategy |
|---|---|
| review | Pack small files and adjacent diff hunks into bounded semantic chunks; fan out per packed chunk; merge with existing deterministic finding dedupe; synthesize only capped merged findings |
| compare | Build aligned section summaries for A and B, then run the final A/B vote over bounded summaries; preserve section provenance |
| ask/pipe | Split on Markdown headings, blank-line blocks, or explicit record boundaries; map answers; run one bounded synthesis only when requested or required by chunking |
| negotiate | Preserve a bounded evidence brief through every drafter and reviewer round; never replace original context with only the prior proposal; reject context that cannot be represented without user-approved loss |
| execute/implement | Profile each phase prompt and review prompt; cap implementation summaries; stop retries when the remaining phase/run budget cannot cover another implementation plus required review |
| deliberate | Run independent calls within a stage with bounded fan-out; summarize/cap peer material before the next stage; do not treat timeout placeholders as council opinions |
| flow | Cap blackboard values, store large raw output as a file reference, count actual model calls rather than handler visits, and reject a graph whose forecast exceeds call/run limits unless explicitly overridden |
| synthesis | Prefer structured findings; cap each source and total input; fail soft with a visible `input_limit` reason instead of recursively synthesizing unbounded raw transcripts |

## Phases

### Phase 1: Add authoritative run budgets, job ownership, and structured call telemetry

**Goal:** Make every orchestrator share one monotonic command deadline, one durable ownership model, one call forecast, and one typed failure vocabulary without breaking the public validator API.

**Criteria:**
- Add a small stdlib-only runtime primitive with `started_at`, absolute monotonic deadline, remaining seconds, cleanup reserve, atomic external-call/input-byte/output-byte reservations, and planned/actual call records.
- Add a stdlib-only run registry with atomic sanitized manifests under `$LOPE_HOME/runs/`, short-held registry locking, explicit lifecycle states, schema versioning, and bounded completed-record retention.
- Generate run/call IDs before external launch. Record owner PID plus start/boot fingerprint and reserve a private run work directory before any prompt file, spool, HTTP worker, or provider process is created.
- Add shared `--run-timeout`, `--max-calls`, `--request-policy`, and `--max-chunks` flags only to commands that execute external work. The unsafe unbounded-wall mode has its own explicit flag.
- Preserve existing `--timeout` meaning as the per-call cap.
- Define one `DEFAULT_MODEL_CALL_TIMEOUT_SECONDS = 960`; keep separately named defaults for gates, health checks, and mode run ceilings.
- Add a versioned internal `InvocationContext` carrying deadline, cancellation, limits, and telemetry. Existing validator methods become compatibility wrappers; do not use mutable globals or thread-local budget state.
- `EnsemblePool.validate()` and `_fanout_generate()` enforce a fan-out deadline in both parallel and sequential modes and honor the selected parallel policy.
- Queued validators receive only the remaining budget after they actually start; a queue delay cannot silently grant a fresh full timeout.
- Built-in/configured external adapters cannot prevent CLI process exit after the run deadline. Legacy in-process adapters that do not accept cooperative cancellation are classified `legacy_non_cooperative` and must run behind a killable isolation boundary before joining parallel CLI fan-out.
- Every call record includes: run ID, call ID, mode, stage, validator, queue/start/end timestamps, prompt bytes/lines/estimated tokens, requested/effective timeout, transport, outcome class, output bytes, and cleanup result.
- Active call records additionally include sanitized supervisor/child identity, PGID, start fingerprints, heartbeat, deadline, ownership-marker hash, redacted command hash, and owned temporary paths. Raw prompts, responses, full argv, environment values, and credentials fail manifest-schema validation.
- Outcome classes include at least `ok`, `provider_timeout`, `run_budget_exhausted`, `rate_limited`, `launch_error`, `nonzero_exit`, `parse_error`, `input_limit`, `output_limit`, and `cancelled`.
- Ensemble errors preserve each validator’s actual error or verdict rationale; no blank `all validators infra error:` result.
- Consensus/ensemble operations require a default majority quorum of substantive results. Structured `CLEAN` is valid; empty/tool-only/unparseable output is not. Quorum failure is `inconclusive`, exits non-zero, and preserves partial responses.
- Machine output gains an additive top-level `schema_version` plus `plan`, `timing`, `limits`, `partial`, and typed `reason` fields.
- Flow and Auditor use measured node/stage wall time when synthesized verdict duration is missing.
- `lope status` reports active/abandoned/unresponsive/cleanup-failed job counts and best-effort CPU/RSS without treating an old or empty `run.lock` file as a live job.

**Files:**
- New `lope/runtime.py`
- New `lope/jobs.py`
- `lope/config.py`
- `lope/models.py`
- `lope/cli.py`
- `lope/ensemble.py`
- `lope/executor.py`
- `lope/negotiator.py`
- `lope/deliberation.py`
- `lope/flow/model.py`
- `lope/flow/runner.py`
- `lope/flow/report.py`
- `lope/gates.py`
- `lope/auditor.py`
- `lope/runlock.py`
- New `tests/test_runtime_budget.py`
- New `tests/test_job_registry.py`
- `tests/test_new_verbs.py`
- `tests/test_negotiator_timeout.py`
- `tests/test_flow.py`
- `tests/test_deliberation.py`

**Tests:**
- Killable external stub ignores a 0.2s timeout; ensemble returns by deadline and the child Python process exits within 1.0s.
- Parallel and sequential pools both stop scheduling when the run budget is exhausted.
- Provider cap, call cap, and remaining budget always resolve to the smallest positive value.
- Concurrent schedulers cannot oversubscribe the external-call or byte budget because reservations are atomic.
- Manifest writes survive process interruption as either the previous or next valid JSON document; no partial JSON is observable.
- PID-reuse simulation proves that a matching PID with a different start fingerprint is never classified as the recorded owner or child.
- Empty/stale-but-unlocked `run.lock` content is not classified as an active job; a genuinely held flock is reported separately.
- Manifest schema rejects raw prompt/output fields and cleanup paths outside the canonical Lope-owned run directory.
- Synthesis, fallback, retry, gate, and self-heal calls decrement the same run budget.
- Blank parse-error fields fall back to the verdict rationale in ensemble diagnostics.
- Two timeout errors plus one tool-only answer yield `inconclusive`, zero clean findings, and non-zero exit rather than `fallback=true` success.
- Flow’s 116–141s class of run records non-zero node durations in scorecard and journal output.
- CLI/config/env precedence tests cover positive `--run-timeout`, `--max-calls`, and the explicit unsafe-unbounded flag.

### Phase 2: Add parent-death supervision and unify subprocess, HTTP, fallback, and output-limit enforcement

**Goal:** Route every external execution path through a parent-death-aware supervisor with cancellation-safe, bounded I/O so a per-call timeout is a real ceiling and a crashed Lope parent cannot strand work.

**Criteria:**
- Replace `preexec_fn=os.setsid` with thread-safe process-group/session creation such as `start_new_session=True` on POSIX.
- Launch each external call through a small killable supervisor process that is outside the provider process group, owns the provider pipes, publishes identity to the run manifest, and watches a parent control pipe as well as the monotonic deadline.
- On parent control-pipe EOF, normal cancellation, output overflow, or deadline, run the same idempotent cleanup state machine. Send TERM to the positively identified provider group, wait a bounded grace period, send KILL if needed, then call `wait()`/`waitpid()` until the supervisor's direct children are reaped.
- Inject random `LOPE_RUN_ID`/`LOPE_CALL_ID` ownership markers into the child environment and record only their hashes. Reconciliation uses the marker plus PID/start/PGID evidence; command text alone is never ownership proof.
- Use the common process runner for OpenCode, Gemini, Claude, Codex, Aider, generic subprocess providers, Makakoo adapters, Brain search, self-heal help/smoke, and project gates.
- On timeout, terminate then force-kill the entire group within a bounded cleanup reserve; report how many processes were targeted when the platform can determine it.
- Define and test Windows behavior. Use a process-tree mechanism or return an explicit degraded-cleanup classification; never claim descendant cleanup when only the direct child was killed.
- On supported Windows versions, attach the provider tree to a kill-on-close Job Object through stdlib `ctypes`; otherwise refuse the hard-cleanup guarantee with an explicit `cleanup_degraded` result rather than silently leaking descendants.
- OpenCode `run` plus `export` share one call deadline. Export receives only remaining time and is skipped when none remains.
- Makakoo adapter wrapper timeout never exceeds the caller’s remaining deadline; remove the unbudgeted `+30` behavior.
- HTTP connections use a total monotonic deadline, bounded chunked reads, `Content-Length` preflight, and a hard response-byte limit. A trickle response cannot extend the call indefinitely. DNS/connect/read work runs behind a killable stdlib worker-process boundary so a blocking system resolver cannot defeat the deadline.
- Subprocess stdout/stderr use bounded streaming or secure spooling. Crossing the limit cancels the process and returns `output_limit` with preserved head/tail diagnostics.
- All prompt files, HTTP-worker scratch, and stdout/stderr spools are created beneath the private run work directory and registered before use. Cleanup rejects symlinks and paths that escape the canonical root.
- Prompt transport is capability-driven: stdin, secure file, then argv. No prompt body appears in process diagnostics.
- Before argv launch, calculate environment plus argv bytes against `SC_ARG_MAX` with safety reserve and enforce the lower 128KiB Lope policy cap.
- Implement `GenericHttpValidator.generate()` with the same request builder, response parser, deadline, and caps as `validate()`.
- Fix HTTP `max_tokens` generation: emit the provider field `max_tokens`, not the literal key `{max_tokens}`; replace the unused global 100K default with mode-appropriate output budgets.
- Add one output-validity normalizer. It recognizes adapter-native envelopes and classifies tool-intent-without-answer, empty, malformed, and substantive responses. Do not use broad regex stripping that can delete legitimate code review content.
- Consume valid, unexpired learned adapters in pool construction. Self-heal is one attempt per CLI per run and only starts when its complete bounded sequence fits in remaining budget.
- Maintenance-only subprocesses (`lope update`, installer, Graphviz render) remain out of the model-call layer but receive separately documented operational timeouts where a hang is possible.
- Replace the run-lock conflict suggestion to use broad `pkill -f` with ownership-aware `lope jobs list` and `lope jobs kill <run-id>` guidance.

**Files:**
- `lope/processes.py`
- New `lope/supervisor.py`
- `lope/jobs.py`
- New `lope/http_worker.py`
- `lope/validators.py`
- `lope/generic_validators.py`
- `lope/makakoo_adapter.py`
- `lope/makakoo_bridge.py`
- `lope/healer.py`
- `lope/gates.py`
- `lope/curl_parser.py`
- `lope/cli_discovery.py`
- `lope/cli.py`
- `lope/runlock.py`
- `tests/fixtures/spawn_tree.py`
- New `tests/fixtures/ignore_term_tree.py`
- `tests/test_validator_processes.py`
- New `tests/test_job_lifecycle.py`
- New `tests/test_invocation_limits.py`
- New `tests/test_http_deadline.py`
- `tests/test_opencode_extract.py`
- `tests/test_makakoo_adapter.py`
- `tests/test_makakoo_bridge.py`
- `tests/test_self_heal.py`
- `tests/test_curl_parser.py`
- `tests/test_new_verbs.py`

**Tests:**
- Descendant-cleanup regression runs through every built-in validator class and the generic/Makakoo/gate wrappers, not only the shared helper.
- A harness `SIGKILL`s the top-level Lope parent while a child and grandchild ignore TERM; the supervisor detects control-pipe EOF, escalates to KILL, reaps its direct child, and no marked process remains after five seconds.
- A second harness kills parent and supervisor together; the next startup reconciler reaps the positively identified provider group from its manifest without touching a concurrent unregistered CLI process with similar command text.
- Repeated timeout/cancel races produce no `Z` processes and exactly one terminal cleanup record.
- Fake OpenCode spends most of its deadline in `run`; export gets only the remainder and total wall stays within timeout plus cleanup tolerance.
- Fake Makakoo sleeps past its inner timeout; Lope returns within the outer call ceiling and leaves no child.
- Local HTTP server trickles bytes faster than the socket idle timeout; total call still ends at the monotonic deadline.
- Local DNS/connect worker blocks past deadline; parent kills the worker and returns `provider_timeout`.
- Local HTTP server declares or streams a response over the byte cap; Lope returns `output_limit` without loading it all into memory.
- A 128KiB-plus mandatory-argv prompt routes to chunk/reject before `execve`; an accepted stdin/file prompt never appears in `ps` output.
- `--max-tokens 256` produces a JSON `max_tokens: 256` value and is honored by both direct and curl-derived HTTP providers.
- Pi-style tool intent with no final answer is `invalid_output`, not a successful empty review.
- Persisted learned adapter changes the next constructed validator invocation and expires after the existing TTL.
- Stale prompt/spool files beneath a confirmed abandoned run are removed; a symlink and a forged outside-root path survive and produce `cleanup_path_rejected` diagnostics.

### Phase 3: Build admission control and hierarchical semantic chunking

**Goal:** Make large requests predictable and smaller without turning one request into an uncontrolled number of full-team calls.

**Criteria:**
- Add one request planner used by negotiate, ask, pipe, review, compare, deliberate, synthesis, execute/implement review prompts, and Flow node prompts.
- Planner emits `direct`, `chunk`, or `reject` plus reason, input profile, transport per validator, chunks, planned calls, maximum concurrency, and nominal wall ceiling.
- Token estimate uses UTF-8 bytes conservatively and is always labelled approximate. Hard enforcement uses bytes/provider metadata, not token guesswork.
- Chunker recognizes Markdown headings, fenced blocks, Python top-level declarations where `ast` parsing succeeds, diff file/hunk boundaries, and generic paragraph/line fallback.
- Pack adjacent small files and hunks into one bounded request while preserving source labels and line ranges.
- Split a single overlong line/hunk safely; no chunk may exceed its configured hard byte limit.
- Add bounded overlap only where needed for local context; deterministic dedupe removes overlap duplicates.
- Default maximum is 32 chunks. Exceeding it rejects before any validator launch and prints the exact override plus revised call ceiling.
- Review uses existing structured finding parsing and deterministic merge. It does not send all raw chunk responses into a final synthesis.
- Synthesis caps each source and total input. Structured findings win over transcripts; truncation records source and omitted bytes.
- Negotiate carries a bounded evidence brief through initial proposal, validator review, and every refinement. Original context is not silently discarded.
- Deliberation caps peer blocks and runs same-stage independent calls using configured bounded parallelism.
- Flow blackboard stores bounded inline detail and a reference for larger output. Interpolation cannot recursively inject unlimited prior output.
- Partial chunk results are written to the run output as evidence, but automatic cross-process resume is deferred. A later resume design must subtract consumed duration/calls/bytes from the original limits rather than persisting a monotonic timestamp.
- Exceeding chunk, call, or byte limits rejects before launch with required-size diagnostics. There is no implicit truncation consent.

**Files:**
- New `lope/request_plan.py`
- `lope/divide.py`
- `lope/review.py`
- `lope/synthesis.py`
- `lope/negotiator.py`
- `lope/deliberation.py`
- `lope/executor.py`
- `lope/implement.py`
- `lope/flow/model.py`
- `lope/flow/runner.py`
- `lope/cli.py`
- New `tests/test_request_plan.py`
- `tests/test_divide.py`
- `tests/test_review_consensus.py`
- `tests/test_synthesis.py`
- `tests/test_negotiate_reliability.py`
- `tests/test_deliberation.py`
- `tests/test_flow.py`

**Tests:**
- Current 32KB HEAD diff packs into a small bounded number of requests rather than 38 one-hunk fan-outs.
- `lope/` package planning is deterministic, reports exact call count, and refuses when it exceeds 32 chunks unless overridden.
- A single 200KB line never produces an over-limit chunk.
- Chunk overlap does not duplicate final consensus findings.
- Three 100K validator outputs cannot create a 300K synthesis prompt; the result reports truncation/structured reduction.
- Negotiate reviewer prompt contains the bounded original evidence brief in every round.
- Flow output expansion test chains multiple nodes and proves downstream prompt bytes stay under the configured ceiling.
- A 33-chunk plan with the default limit launches zero validators and reports the required chunk/call/byte override.

### Phase 4: Apply budget-aware retries, fallbacks, progress, and true call accounting to every mode

**Goal:** Remove multiplicative surprises and make each mode stop, retry, fall back, or degrade based on remaining budget and typed failure reason.

**Criteria:**
- Drafter uses primary plus at most one healthy fallback by default. Additional fallback breadth requires explicit policy and sufficient forecast budget.
- For this release, a healthy fallback means available, not quarantined by an earlier timeout/rate-limit/invalid-output result in the same run, and capable of the requested generate/validate transport. Do not add persistent health scoring yet.
- Lint retries, negotiation refinements, synthesis, HTTP fallback, and self-heal run only when remaining budget covers the next complete stage.
- Transient retry is limited to classified 429/503/connect-reset cases, honors `Retry-After`, uses bounded jitter, and never retries deterministic input, parse, auth, or output-limit failures.
- A per-run circuit breaker stops calling a validator after repeated timeout/rate-limit failures; completed answers remain in the result and the breaker reason is visible.
- Execute/implement prints a per-phase forecast and refuses to start another implementation attempt when it cannot fund implementation plus mandatory spec review.
- Optional quality/synthesis work is skipped with `budget_exhausted_optional` rather than consuming the last budget and hiding completed core results.
- Gate suites clamp each gate to remaining budget and stop scheduling further gates after command deadline; required skipped gates fail closed.
- Role review uses common fan-out and respects `--parallel`/`--sequential`.
- Deliberation parallelizes independent calls within each stage, applies a minimum-contributor rule, and excludes timeout/error placeholders from council evidence.
- Consensus review and execution validation apply the same substantive-result quorum; an explicit structured clean result counts, a tool-call preamble does not.
- Flow adds `max_model_calls` accounting. Agent/judge-generate costs one call; review/judge-ensemble costs the number actually scheduled; retries consume their true internal call count.
- Flow dry-run/validate reports model-call forecast, chunk forecast, run deadline, and any graph path whose theoretical ceiling exceeds configured budget.
- Single-shot commands stream progress as validators finish rather than staying silent until all work ends. Heavy modes emit a heartbeat at least every 15 seconds with stage, elapsed, remaining budget, completed/total calls, and active validator names.
- Final summary shows forecast versus actual calls and wall time, per-validator latency, cancellation reasons, prompt/output bytes, and partial-result location.
- `team health` distinguishes tiny-prompt health from workload admission. Its advice prioritizes request shaping and transport before blindly suggesting a larger timeout.
- Add `lope jobs list [--json]`, `lope jobs reap [--dry-run]`, and `lope jobs kill <run-id>`. Registry inspection/reaping bypasses the global run lock, uses a separate short-held registry lock, and returns typed per-job actions and refusals.
- Reconcile active manifests before every external-work command and after normal completion. Confirmed abandoned owned jobs are reaped; live-owner stale heartbeats and ambiguous identities are reported but never auto-killed.
- Extend `lope status` and lock-conflict output with safe job state, exact run IDs, deadlines, process count, best-effort CPU/RSS, and cleanup failures. Remove every broad `pkill`/`killall` recommendation from user surfaces.
- Heartbeats come from both owner and call supervisor so a blocked owner can be distinguished from a still-managed provider. Heartbeat writes are rate-limited and atomic; they do not extend any deadline.
- Completed run closeout prunes sanitized metadata to seven days/1,000 records and verifies no active manifest, live owned provider, or private work directory remains for the completed run.

**Files:**
- `lope/cli.py`
- `lope/ensemble.py`
- `lope/negotiator.py`
- `lope/executor.py`
- `lope/implement.py`
- `lope/deliberation.py`
- `lope/healer.py`
- `lope/gates.py`
- `lope/flow/model.py`
- `lope/flow/validate.py`
- `lope/flow/runner.py`
- `lope/flow/report.py`
- `lope/output.py`
- `lope/jobs.py`
- `lope/runlock.py`
- `lope/journal.py`
- `lope/auditor.py`
- New `tests/test_retry_policy.py`
- New `tests/test_progress_reporting.py`
- New `tests/test_jobs_cli.py`
- `tests/test_negotiate_reliability.py`
- `tests/test_execute_gates.py`
- `tests/test_implement.py`
- `tests/test_deliberation.py`
- `tests/test_flow.py`
- `tests/test_team.py`

**Tests:**
- Three failing drafters cannot consume three fresh 900s allocations after the 3600s negotiate deadline.
- Circuit breaker prevents the same timed-out validator from being retried in later lint/round/stage calls.
- One simulated 429 with short `Retry-After` retries once inside budget; long `Retry-After` is skipped immediately with a typed reason.
- Execute with insufficient remaining time does not start an implementation it cannot validate.
- Required gate skipped by deadline makes the run fail closed; optional synthesis skipped by deadline preserves the successful fan-out.
- Standard three-validator deliberation schedules four bounded parallel stages plus primary synthesis instead of 13 serial waits.
- Flow with `max_node_visits=50` and three-validator ensemble reports up to 150 model calls, not 50.
- Progress heartbeat appears during a slow stub run and machine JSON remains valid on stdout.
- Full-sprint dogfood shape (two timeouts plus one tool-only response) exits non-zero with preserved partial diagnostics.
- `jobs list --json` reports active, abandoned, unresponsive, ownership-unverified, and cleanup-failed fixtures with stable schema and resource fields.
- `jobs reap --dry-run` mutates nothing; real reap kills only a confirmed abandoned marked group. A live concurrent run and an unrelated same-name provider remain alive.
- `jobs kill <run-id>` refuses a missing/reused owner fingerprint, records the refusal, and never falls back to command-name matching.
- A held command lock does not block `jobs list/reap`; lock output points to the safe run ID workflow and contains no `pkill -f` instruction.

### Phase 5: Regression matrix, documentation, skills, compatibility rollout, and release

**Goal:** Prove the new ceilings under hostile conditions, document the semantics on every user surface, and ship a backwards-compatible `v0.14.0` with source/install parity.

**Criteria:**
- Add a deterministic hostile-adapter matrix: ignored timeout, descendant tree, parent `SIGKILL`, parent-plus-supervisor death, TERM-ignoring grandchild, PID reuse, stale heartbeat with live owner, stale unlocked lockfile, concurrent unrelated same-name CLI, zombie prevention, stale temp/spool cleanup, slow startup, oversized argv, output flood, HTTP trickle, 429 retry, malformed verdict, empty error, synthesis explosion, chunk explosion, and run-budget exhaustion.
- Tests assert wall-clock tolerance and process cleanup, not only returned error strings.
- Python 3.9 and 3.12 pass the same deadline tests with conservative timing tolerances.
- Existing config files load unchanged. New fields are optional; unknown provider entries remain preserved.
- Existing library calls remain valid. New deadline/telemetry arguments are keyword-only and additive.
- Additive JSON schema versioning and the new `inconclusive` exit contract are documented with migration examples.
- `--timeout` remains per-call. Documentation stops describing it as sufficient protection for a multi-round command.
- Consolidate default documentation: model call 960s; gates 480s; health 30s; mode run ceilings as specified above.
- Update reference docs, architecture, README troubleshooting, a new job-lifecycle/operator manual, deliberation/Flow docs, release notes, all affected `skills/*/SKILL.md`, Gemini command TOMLs, and OpenCode wrapper docs.
- Skill guidance says: inspect request plan, prefer compact evidence, use chunking only with a bounded call forecast, and lower/raise `--run-timeout` explicitly rather than blindly increasing `--timeout`.
- Job guidance says: use `lope jobs list`, inspect ownership state and run ID, dry-run reaping first when uncertain, and never use `pkill -f`, `killall`, or process-name matching to clean validators.
- `docs/RELEASING.md` no longer claims the public repo has no tests.
- Version moves from `0.13.1` to `0.14.0` in all six required surfaces.
- Source repo and `/Users/sebastian/.lope` are identical after release install.
- No code ships until all deterministic gates pass and at least one real-wire smoke proves tiny, medium, and automatically shaped large requests.

**Files:**
- `README.md`
- `CHANGELOG.md`
- `docs/ARCHITECTURE.md`
- `docs/reference.md`
- New `docs/job-lifecycle.md`
- `docs/deliberation.md`
- `docs/RELEASING.md`
- `skills/using-lope/SKILL.md`
- `skills/lope-negotiate/SKILL.md`
- `skills/lope-execute/SKILL.md`
- `skills/lope-implement/SKILL.md`
- `skills/lope-ask/SKILL.md`
- `skills/lope-review/SKILL.md`
- `skills/lope-vote/SKILL.md`
- `skills/lope-compare/SKILL.md`
- `skills/lope-pipe/SKILL.md`
- `skills/lope-deliberate/SKILL.md`
- `skills/lope-flow/SKILL.md`
- `skills/lope-team/SKILL.md`
- New `skills/lope-jobs/SKILL.md`
- Matching `commands/lope/*.toml`
- Matching `commands/opencode/*.md`
- `pyproject.toml`
- `lope/__init__.py`
- `install`
- `.claude-plugin/plugin.json`
- `.cursor-plugin/plugin.json`
- `gemini-extension.json`
- `scripts/check-version.sh`
- New/updated test files from Phases 1–4

**Tests:**
- `ruff check .`
- `python3 -m compileall -q lope tests`
- `python3 -m pytest -q`
- `mypy lope` remains advisory, with new errors documented or fixed.
- `./scripts/check-version.sh`
- `PYTHONPATH=. python3 -m lope version`
- `PYTHONPATH=. python3 -m lope team health --timeout 30 --json`
- Tiny smoke: `PYTHONPATH=. python3 -m lope ask --validators codex,claude,pi --timeout 60 --run-timeout 120 --json "Reply exactly: OK"`
- Medium smoke: consensus review of `README.md` with request-plan output and a 10-minute run ceiling.
- Large smoke: review `lope/cli.py` with `--request-policy auto`; confirm no argv overflow, bounded chunk count, partial progress, and successful merge inside the run ceiling.
- Inconclusive smoke: two timeout stubs plus one tool-only output; confirm non-zero exit, substantive quorum failure, and preserved per-validator reasons.
- Cancellation smoke: hostile local adapter ignores timeout and spawns descendants; command exits inside tolerance and `ps` finds no surviving tree.
- Parent-death smoke: kill the Lope owner during a live provider call; supervisor clears the marked process group, removes owned scratch, and leaves a completed cleanup record without waiting for another Lope command.
- Crash-recovery smoke: kill owner and supervisor together; the next `lope jobs reap` removes only the registered abandoned group and refuses an unrelated same-name provider plus a PID-reuse fixture.
- Resource smoke: `lope status`/`lope jobs list --json` report active process count and best-effort CPU/RSS, then show zero active owned processes after closeout.
- HTTP smoke: local trickle server and response flood both terminate with typed reasons.
- Release gates: bump all six version surfaces, commit surgically, push `main`, tag `v0.14.0`, create GitHub release, run `./install`, and verify `/Users/sebastian/.lope` parity.

## Compatibility and migration policy

1. Existing `~/.lope/config.json` version 1 remains valid. Do not require a migration command.
2. Existing provider `timeout` remains a shortening cap. It can never extend call or run deadline.
3. Existing `--divide files|hunks` remains accepted. Internally it routes through the new planner and packing logic; output labels and consensus schema remain stable.
4. Existing `LOPE_TIMEOUT` remains the model call default override. New `LOPE_RUN_TIMEOUT` is independent.
5. Existing validator subclasses remain callable with positional `(prompt, timeout)`. New adapters implement the versioned invocation context; legacy wrappers are visibly classified when they cannot guarantee cancellation.
6. Machine JSON keeps existing fields and adds `schema_version`, `plan`, `timing`, `limits`, `partial`, and typed `reason` fields without removing old keys.
7. A request newly rejected for unsafe argv or chunk explosion exits 2 with exact mitigation. It must not silently truncate input.
8. Run-timeout exhaustion exits 124 for commands whose core result is incomplete. Optional synthesis exhaustion preserves exit 0 when the core result completed.
9. Consensus quorum failure is a deliberate behavior correction: it becomes `inconclusive` and non-zero. Raw single-shot modes keep partial-result semantics, but zero substantive answers are always non-zero.
10. User-facing run artifacts live under the selected output directory or `lope-runs/`; no source file or global finding memory is modified implicitly. Sanitized internal ownership manifests and private transient work live under `$LOPE_HOME/runs/` and never contain prompt/output bodies or secrets.
11. The persistent zero-byte `run.lock` file remains valid. Its existence/mtime is not job liveness; only an acquired flock indicates command-lock ownership.
12. New `lope jobs` and additive `lope status` fields do not change existing validator/config output. Registry schema is versioned independently and corrupt/unknown manifests fail closed without signaling a process.

## Rollout gates

### Gate A — deterministic unit safety

- All hostile adapters terminate inside `configured timeout + cleanup tolerance`.
- Zero surviving descendants after every timeout test.
- No input/output cap test allocates the full hostile payload in memory.
- Parent `SIGKILL` leaves no marked provider process after the supervisor cleanup bound.
- Parent-plus-supervisor death is repaired on reconciliation with PID/start/ownership proof; a same-name unrelated process and PID-reuse fixture remain alive.
- No timeout/cancel/crash test leaves a zombie, active manifest, or owned transient path after successful closeout.

### Gate B — mode contract safety

- Every external-call mode emits a request plan and obeys the shared run deadline.
- Every retry/fallback/synthesis/gate path proves remaining-budget propagation.
- No blank infrastructure reason reaches CLI, JSON, Flow report, or journal.

### Gate C — request shaping quality

- Medium requests stay direct when safe.
- Oversized review/diff inputs produce bounded semantic chunks with provenance.
- Chunk packing reduces the current 38-hunk sample materially and never exceeds 32 default chunks.
- Hierarchical merge produces no duplicate overlap findings in fixtures.

### Gate D — real validator dogfood

- Tiny team health stays green near the current 4.7–13.1s baseline.
- Medium prompts complete without timeout regression.
- Large prompt path uses planned shaping and completes or stops at run deadline with usable partial results.
- One validator failure does not erase healthy validator output.

### Gate E — release integrity

- CI passes on Python 3.9 and 3.12.
- Version strings, docs, skills, command wrappers, source repo, and installed copy agree on `v0.14.0`.
- Changelog explicitly documents new timeout semantics and any behavior that can now reject unsafe work.
- README, reference, operator manual, skills, and wrappers document `lope jobs` and contain no broad process-name kill guidance.

## Measurable success targets

1. 100% of model/provider subprocess paths use the common cancellation-safe runner.
2. 100% of multi-call modes have an enforced command deadline and remaining-budget propagation.
3. No tested timeout leaves a descendant process alive after five seconds.
4. Fan-out with an ignored timeout in a built-in/configured external adapter returns and lets the Python process exit within two seconds of its deadline.
5. No model request exceeds the selected adapter’s transport or prompt-byte limit.
6. No response or synthesis input exceeds its configured hard byte ceiling.
7. A request requiring more than 32 chunks launches zero validators unless explicitly overridden.
8. Flow reports actual model calls and non-zero node durations; the documented bound matches runtime accounting.
9. Timeout errors identify validator, stage, effective timeout, elapsed time, prompt bytes, transport, remaining run budget, and cleanup result.
10. Tiny health latency stays within 25% of the pre-sprint median unless provider-side variance is documented.
11. No run exceeds its external-call, total-input-byte, or total-output-byte allowance, even under concurrent scheduling.
12. Consensus with fewer than a majority of substantive results is `inconclusive` and never a clean exit 0.
13. 100% of external provider launches create a durable call lease before `exec` and reach exactly one terminal lifecycle state.
14. Parent `SIGKILL` during every built-in/configured adapter leaves zero positively identified provider descendants within five seconds.
15. Startup reconciliation reaps 100% of confirmed abandoned fixtures and kills 0 live, PID-reused, ambiguous, or unrelated same-name processes.
16. Successful run closeout leaves zero active manifests and zero Lope-owned prompt/spool/HTTP-worker scratch paths.
17. No cleanup test leaves a zombie; every supervisor direct child is waited after normal exit, TERM, KILL, timeout, output overflow, and parent death.
18. `lope jobs list --json` and `lope status` expose active job count and best-effort process count/CPU/RSS; a five-day orphan fixture becomes diagnosable without inspecting raw process command lines.

## Explicit non-goals

- Do not increase all default timeouts as the primary fix.
- Do not add Celery, asyncio orchestration, Redis, a workflow engine, or a tokenizer dependency.
- Do not auto-discover or hardcode every model’s context window. Provider metadata and byte-safe defaults are enough.
- Do not run multiple implementation writers against one checkout.
- Do not persist raw prompts or unredacted validator outputs in telemetry.
- Do not create a general distributed scheduler.
- Do not create a resident daemon, launchd service, or OS-wide process cleaner. Per-call supervisors plus startup/explicit reconciliation are sufficient.
- Do not kill unregistered validator processes, even when they look abandoned. Lope owns and reaps only processes for which it has positive run/call identity evidence.
- Do not infer ownership from PID alone, command name, argv substring, cwd, lockfile age, or stale heartbeat.
- Do not redesign finding consensus, sprint markdown format, or Lope memory beyond the timing fields needed here.
- Do not add automatic cross-process resume, persistent latency learning, or a durable provider circuit-breaker in `v0.14.0`.
- Do not include `lope update`, installer, or Graphviz execution in model-call accounting; give those separate operational ceilings only.

## Deferred follow-up after v0.14.0

These ideas remain valid but are deliberately outside the first containment release:

- cross-process resume with consumed wall/call/byte accounting;
- persistent provider latency percentiles and adaptive concurrency;
- durable circuit-breaker state across runs;
- provider-specific tokenizer plugins or discovered context windows;
- worktree-backed parallel implementation writers;
- richer semantic parsers beyond the stdlib Markdown/Python/diff/fallback boundaries required here.

## Implementation order and stop conditions

1. Implement Phase 1 and the non-cooperative fan-out regression first. Stop if the CLI process still cannot exit at deadline.
2. Implement Phase 2 and parent-death/descendant/HTTP/output-limit tests. Stop if parent `SIGKILL` leaves a marked provider alive, any PID-reuse test can signal the wrong process, or any built-in path bypasses the common supervisor.
3. Implement Phase 3 planner and dry request profiles. Stop if the planner can schedule more calls than its reported forecast.
4. Wire Phase 4 mode behavior. Stop if any retry, fallback, gate, or synthesis receives a fresh timeout instead of remaining budget.
5. Run Phase 5 docs, real-wire dogfood, version bump, and release only after deterministic gates A–D pass.

## Final decision

Proceed with this sprint. The dominant improvement is not merely smaller prompts. It is the combination of:

- hard command budgets,
- cancellation-safe universal invocation,
- byte-aware admission,
- semantic packed chunks,
- bounded synthesis,
- circuit breaking and budget-aware fallback,
- true call/duration telemetry,
- durable job ownership and parent-death supervision,
- safe stale-job reconciliation with CPU/RSS visibility.

That combination addresses the observed timeouts without converting them into longer hangs, abandoned CPU consumers, or a larger bill.
