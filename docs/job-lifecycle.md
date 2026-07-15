# Lope job lifecycle and safe cleanup

Lope records every run and provider call under its owned run root. A lease contains only sanitized ownership metadata: run/call IDs, PID and process-start fingerprint, process-group identity, deadline, state, and owned temporary paths. Prompts, responses, credentials, and full command arguments are never persisted.

## Inspect

```bash
lope jobs list
lope jobs list --json
lope status --json
```

The listing distinguishes active, abandoned, unresponsive, ownership-unverified, and cleanup-failed jobs and reports age, deadline, process count, and best-effort CPU/RSS. A stale lockfile is not evidence of a live job.

## Reap

Always preview first:

```bash
lope jobs reap --dry-run
lope jobs reap
```

Reaping requires a positive run/call identity plus matching PID and process-start fingerprint. PID reuse, ambiguous ownership, live heartbeats, and unrelated provider processes are refused. Cleanup stays inside Lope-owned run roots and records a terminal result.

For an explicitly abandoned run, use `lope jobs kill <run-id>`. Never use `pkill -f`, `killall`, command-name matching, or broad process-tree scripts. Those can terminate unrelated user sessions.

## Runtime controls

`--timeout` limits one external call. `--run-timeout` is the monotonic whole-command deadline and covers retries, fallbacks, gates, and synthesis. `--request-policy auto` chooses direct, bounded chunking, or rejection from a byte/token profile. Use `--max-calls` and `--max-chunks` to tighten admission limits.

Timeouts preserve completed validator results and return typed reasons. Consensus with no substantive quorum is `inconclusive` and exits non-zero; it is not a clean review.
