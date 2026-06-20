# Maintainers

This is the public, authoritative list of people who can merge to `lope`. [`CONTRIBUTING.md`](../CONTRIBUTING.md) describes the governance model; this file names the humans and the escalation path behind it.

## Current maintainers

| Name                    | GitHub        | Areas                                                                 |
| ----------------------- | ------------- | --------------------------------------------------------------------- |
| Sebastian Schkudlara    | [@rschumann](https://github.com/rschumann) | Everything. Core, CLI adapters, validation engine, release path. BDFL tiebreaker for v0.x. |

A single maintainer is a fact of a young project, not a goal. The table grows the moment a contributor has earned it, and [`.github/CODEOWNERS`](../.github/CODEOWNERS) is structured so adding handles is a one-line change.

## What a maintainer does

- **Merges.** No one merges their own non-trivial PR without a second maintainer's approval once there is a second maintainer. Until then, Sebastian self-merges but every change still lands through a PR with a green CI checkmark.
- **Owns CI green.** A red `main` is a maintainer's problem to fix or revert within the day.
- **Triages security reports.** See [`SECURITY.md`](../SECURITY.md).
- **Cuts releases.** Tags are pushed only by a maintainer.

## How decisions get made

Decisions live in public — issues, PRs, and the GitHub Discussions board.

## Becoming a maintainer

There is no application form. The path is mechanical and earned:
1. Land non-trivial PRs.
2. Review other people's PRs.
3. An existing maintainer nominates you.

## Stepping down

Maintainers who go quiet get moved to an Emeritus section.

## Security

Never report a vulnerability here or in a public issue. Follow [`SECURITY.md`](../SECURITY.md).
