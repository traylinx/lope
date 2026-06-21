# Security Policy

## Supported versions

Security fixes are released against the latest GitHub release and the
`main` branch git checkout. PyPI publishing is not live yet, so upgrade the
standard `~/.lope` checkout with `lope update` to receive fixes.

| Version  | Supported |
| -------- | --------- |
| latest   | ✅        |
| < latest | ❌        |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's
[private vulnerability reporting](https://github.com/traylinx/lope/security/advisories/new)
("Report a vulnerability" under the repository's **Security** tab). If you
cannot use that, open a minimal public issue asking a maintainer to contact
you — **without** disclosing the details.

Please include:

- A description of the issue and its impact.
- Steps to reproduce (a minimal proof of concept if possible).
- Affected version(s) and platform.

We aim to acknowledge a report within **5 business days** and to share a
remediation plan or fix timeline after triage. This is a community project
maintained on a best-effort basis; there is no bug-bounty program.

## Scope & trust model

lope orchestrates other AI CLIs and can execute **project-defined commands**
(quality gates declared in a repository's `.lope/rules.json`) plus AI-CLI
subprocesses. Treat lope like any tool that runs code from a repository:

- **Only run lope in repositories you trust.** Gate commands and CLI
  invocations run with your user privileges.
- lope redacts credentials from captured output and refuses to persist a
  literal API key pasted via `--from-curl` (it swaps it for an env-var
  reference). Report any path where a real secret leaks into logs,
  artifacts, or persisted config.

Reports that materially tighten the trust boundary — command execution
without confirmation, secret leakage, path traversal in artifact writes,
unpinned remote-code execution in the installer — are especially welcome.
