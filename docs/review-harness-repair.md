# Review harness repair

Runner behavior and bounded validation evidence. See the linked release record
for historical qualification; no universal production-readiness claim.

## Observable request admission

Every request evaluates latency/clamp advice independently of output mode.
Machine JSON retains the advisory strings in `plan.requests[].budget_advice`;
rejected requests use `request_plan.budget_advice`. Stderr also carries them.
Advice is not a hard rejection, and stricter-wins timeout precedence is unchanged.

Use the canonical `$LOPE_HOME/latency.json` history. Creating a new `LOPE_HOME`
for each run creates a cold ledger; isolated runs need an explicit history plan.
Choose the per-call and whole-run ceilings together. `--respect-provider-timeout`
does not waive the shared whole-run deadline or authorize unlimited retries.
Raw adapter calls do not provide the full CLI preflight/latency framework.

## Failure diagnostics

Custom subprocess timeout, empty-output and nonzero-exit errors retain bounded
stdout/stderr tails. Redaction runs before clipping and covers credential-shaped
values, URL credentials, private keys and recognized reasoning blocks. It cannot
prove arbitrary output free of PII. Do not publish raw transcripts or enable body
capture merely to debug a run. A partial tail is not a completed review verdict.

## Local completion

The direct parent and its isolated process group are distinct lifecycle states.
After parent exit, the supervisor cleans only the exact owned group and drains
already-emitted output within a bounded interval. Output limits still fail closed.
Unclosed pipes or failed local cleanup produce a completion error, not success or
an unexplained wait for the model deadline. An escaped/unowned group is not blindly
signalled. Local cleanup does not prove cancellation of a remote/shared backend.

## Source/install parity

The installed copy has a later Claude safe-mode hotfix and modified skill files.
Repair only the owning module files; merge the shared runner exception branch
without replacing that Claude hotfix. Preserve the original installed cwd patch
and unrelated dirty files. Do not use a broad reset, reinstall, push or release as
a shortcut to synchronization.

## Verification

`tests/test_harness_repair.py` exercises both output modes, cwd, bounded/redacted
diagnostics, owned and unrelated groups, inherited/redirected pipes, escaped-pipe
failure and output limits. The full local suite passes 892 tests. Native file-read
probes are separate from these keyless tests; native review failures need actual
tool/output evidence, not a larger prompt or repeated reviewer cycling.


## Review permissions and launch profiles (0.17.0, 22 October 2026)

A native Agy failure reported that a command tool needed approval unavailable in
headless mode. Earlier Lope labelled it `parse_error` because stdout was empty.
The adapter now recognizes this specific stderr signature before truncation and
before accepting preparatory stdout. It emits terminal `tool_permission_denied`;
no retry, permission grant or automatic implementation-writer failover follows.
Quoted review text is not treated as a runner denial. Authentication failures and
unexplained empty output also retain separate runtime outcome categories.

`lope review` asks for critique of supplied content only; missing evidence must be
reported rather than fetched. This is a prompt instruction, not a security boundary.
An optional custom subprocess profile can reduce unrelated startup context:

```json
{
  "name": "agy",
  "type": "subprocess",
  "command": ["agy", "-p", "{prompt}"],
  "review_command": ["agy", "--disable-slash-commands", "-p", "{prompt}"],
  "review_workdir": "isolated",
  "review_stdin": false
}
```

Verify flags against the installed CLI before selecting a profile. This Agy profile
was checked locally; it is not automatically injected for every Agy version. Agy's
advertised interface has no `--no-tools` switch. Its init event still advertises
tools and request-review permissions even when a text-only probe succeeds.

Profiles apply only to invocation context mode `review`, including structured and
chunked review. Other modes and direct legacy adapter calls keep the original
command/caller directory. `review_workdir` accepts `caller` (default) or `isolated`;
the latter creates and cleans a temporary cwd. It does not isolate HOME, remove
credentials, disable global instructions, block filesystem/network access or stop
a CLI's tools. Use explicit absolute/PATH executables and absolute file arguments
where needed; relative executable paths are rejected for isolated mode. A custom
review executable's availability is reported before launch. Unsupported profiles
are not repaired with guessed flags or permission-bypass switches.

Preflight metadata appears at `plan.requests[].execution_profiles` and warnings
on stderr. It contains no argv, prompt body or credentials. The CLI still owns tool
permissions; a scope error fails closed with the known permission name. The original
native diagnostic does not reveal the requested command; Lope does not invent it.
Consensus JSON now includes actual usage, retry events and circuit state just like
raw review. A failed review remains a failed vote and may make quorum inconclusive.

The existing installed Claude safe-mode/no-session-persistence helper was promoted
unchanged into source. It reduces plugin/MCP startup issues but preserves built-in
tools for implementation; it is not a no-tools or permission-bypass setting.

See [historical verification and release gates](releases/0.17.0-local-validation.md).
The prior 892-test count above is historical, not a statement about this candidate.

### Follow-up audit hardening (22 October)

A full-suite stress run exposed a provider process group left alive. Source review
found two process-inspection paths that could exceed the parent's cleanup grace:
a two-second census before the first signal, and two-second identity probes before
the supervisor event loop. Signals now precede the census; identity/census probes
are bounded to 0.3s, with a no-process-group fast path. The exact stalled call in
the original failure was not traced, so neither path is claimed as its sole cause.
The confirmed orphaned test group was identified by exact fixture path and stopped.

Unknown census results now fail closed. `cleanup_failed` must not pass a
`startswith("clean")` check. `processes_targeted` records a leader lower bound,
not an exact descendant count. These are local POSIX/macOS tests, not a Windows
process-tree guarantee. A 0.5s test startup deadline was independently too short
under load; that fixture now allows 2s startup while keeping its descendant-death
assertion. This changes no production model timeout.

Permission signatures in stdout or quoted diagnostics are not trusted. Native
stderr is checked before clipping on both completion and exception paths. Explicit
review profiles must carry the prompt via stdin or `{prompt}`; isolated profiles
also set the child's `LOPE_WORKDIR` to their temporary cwd. Directory-cleanup errors
warn rather than replace the provider result or its original exception.

Native Agy follow-up additionally identified cumulative cleanup waits and redundant
post-success process-table probes. TERM, KILL and group inspection now share one
0.7s allowance; cancellation drain is bounded to 0.1s within the parent's existing
1s reserve. Empty/unknown census results are not re-probed. These are bounded
application deadlines, not hard real-time operating-system scheduling guarantees.
Pre-integration validation: 941 tests passed on each of Python 3.9 and Python
3.12 on macOS. Subsequent integration found a separate forced-stop startup race.

### Integration hardening

The owner can force-stop a supervisor while registry I/O prevents it from reaching
the cancellation loop. The provider runs in a separate session, so stopping only
the supervisor could leave that provider alive. The POSIX supervisor now handles
TERM by signalling its owned provider group immediately, including a signal that
arrives during spawn. The handler performs no registry I/O, process census or waits.
A real-process regression blocks startup registry I/O and verifies that both the
provider and its TERM-ignoring descendant terminate.

Subprocess cleanup evidence is propagated to invocation telemetry. A forced stop
without a result remains `unknown`; timeout classification cannot overwrite it
with `clean`. Each attempt starts without inherited cleanup evidence. Independent
registry closeout still checks process identities before declaring a run clean.
