# Releasing Lope

This is the release playbook. Follow it in order, don't improvise, don't skip steps. If a step is unclear, stop and fix the doc before shipping.

## Versioning — SemVer with lope-specific rules

Lope follows [Semantic Versioning](https://semver.org/) with these concrete rules:

| Bump | When |
|---|---|
| **MAJOR** (`0.3.x → 1.0.0`, `1.x → 2.0.0`) | Breaking changes to `~/.lope/config.json` schema, validator adapter interface, sprint doc format, or slash command names. Anything that makes a user's existing setup stop working. |
| **MINOR** (`0.3.x → 0.4.0`) | New features: new validator adapter, new host support, new CLI subcommand, new domain, new env flag, new architectural move (evidence gate, two-stage review, etc.). Backwards-compatible. |
| **PATCH** (`0.3.0 → 0.3.1`) | Install fixes, docs corrections, typo fixes, bugfixes that don't add features, INSTALL.md updates, bash installer tweaks. Must not introduce new public behavior. |

Pre-1.0 (`0.x.y`) means the config schema and adapter interface can still change without a MAJOR bump. The MAJOR column above takes effect at `1.0.0`. Until then, breaking changes bump MINOR and get called out loudly in the CHANGELOG.

## Version string lives in 6 places

If you change the version, every one of these must move in lockstep or the install will lie to users about what they have:

1. `pyproject.toml` — `version = "X.Y.Z"`
2. `lope/__init__.py` — `__version__ = "X.Y.Z"`
3. `install` — `echo "Lope vX.Y.Z …"` banner
4. `.claude-plugin/plugin.json` — `"version": "X.Y.Z"`
5. `.cursor-plugin/plugin.json` — `"version": "X.Y.Z"`
6. `gemini-extension.json` — `"version": "X.Y.Z"`

Use `./scripts/bump-version.sh NEW_VERSION` — it seds all 6 files and runs a sync check. Never edit them by hand.

## Preparation is not publication

A local candidate, version bump, successful tests and installed-file sync do not
publish a release. Commit, integration/merge, push, tag publication and hosted
release actions require explicit approval. The `v*` tag workflow publishes to
PyPI/TestPyPI only when the corresponding repository variable is explicitly
`true`: `PYPI_PUBLISH_ENABLED` or `TESTPYPI_PUBLISH_ENABLED`. Leave both unset for
a GitHub-only release. First configure the matching trusted publisher; a tag is
a distribution action, not merely bookkeeping.

Inspect source and installed checkouts separately. Preserve unrelated dirty work;
do not use a reset, stash, broad reinstall or tag deletion as a synchronization
shortcut. If the source branch is behind main, prepare the integration plan and
obtain approval before changing branch ancestry. Never silently discard installed
adapter fixes or locally modified skills.

## Release checklist

Copy this into the PR description (or into the commit body for a direct-to-main release). Check each box in order.

### Pre-flight

- [ ] **Clean working tree.** `git status` shows no untracked lope files and no accidental stash.
- [ ] **On `main`.** `git rev-parse --abbrev-ref HEAD` prints `main`. Never tag a release from a feature branch.
- [ ] **Up to date.** `git pull --ff-only origin main` reports no changes pulled.
- [ ] **CHANGELOG draft.** Write the new section at the top of `CHANGELOG.md` under `# Changelog`. Format: `## X.Y.Z — <one-line tagline>` followed by bullet points of what changed, grouped by the reader's interest (user-facing features first, install/docs fixes second, internals last).
- [ ] **Nothing secret in the diff.** Run the full configured secret scan and review the staged patch. Credential-shaped fixtures, environment-variable names and local paths may match a grep; examine each match rather than treating grep alone as proof. Never publish operator config, private review transcripts or local runtime stores.

### Version bump

- [ ] **Pick the bump level.** MAJOR / MINOR / PATCH per the table above. Write the reason in one sentence before you touch files.
- [ ] **Bump all 6 locations.** `./scripts/bump-version.sh NEW_VERSION`
- [ ] **Verify sync.** `./scripts/check-version.sh` exits 0 and prints the new version.
- [ ] **Engine reports new version.** `PYTHONPATH=. python3 -m lope version` prints the new banner.

### Local smoke test

- [ ] **Installer qualification.** Inspect the installer and use an explicitly approved host/scope or isolated fixture. Do not overwrite modified local skills merely to tick this box. Record any unexecuted installer gate.
- [ ] **Status runs clean.** `PYTHONPATH=. python3 -m lope status` prints the validator table and config path.
- [ ] **Tiny negotiate works.** `PYTHONPATH=. python3 -m lope negotiate --domain engineering --max-rounds 1 "rename a constant in one file"` runs end-to-end without a Python traceback. Validators may escalate — that's fine, we only care that the engine itself works.
- [ ] **Tests pass.** Run the full `tests/` suite on supported CI interpreters (currently Python 3.9 and 3.12), lint, syntax, version sync and secret scan. No optional skip: the repository has a substantial regression suite.
- [ ] **Packages verified.** Build wheel/sdist without publishing; check version metadata, changed modules, required docs and absence of local credentials/runtime artifacts.
- [ ] **Native adapter smoke.** Exercise changed provider paths with an approved bounded fixture. A keyless test is not native-provider proof; a successful fixture is not a universal permission guarantee.

### Commit and tag

- [ ] **Stage only the release files.** `git add pyproject.toml lope/__init__.py install CHANGELOG.md .claude-plugin/plugin.json .cursor-plugin/plugin.json gemini-extension.json` plus any feature/fix files that belong to this release. Never `git add .`.
- [ ] **Commit with a release body.** Subject: `vX.Y.Z — <tagline>`. Body: the CHANGELOG section pasted verbatim (minus the `## X.Y.Z —` header). Use a HEREDOC, not inline `-m`.
- [ ] **Tag annotated, not lightweight.** `git tag -a vX.Y.Z -m "vX.Y.Z — <tagline>"`. Lightweight tags don't carry metadata and break GitHub's release UI.
- [ ] **Push main first; verify CI for that exact commit before publishing the tag.** Only after explicit publication approval and green CI, push `vX.Y.Z`. Confirm the requested destination: GitHub-only means both package-publishing repository variables are unset/false; package publication additionally requires the matching trusted publishers and opt-in variables. Do not infer readiness from an earlier branch/test run.

### Post-push verification

- [ ] **Tag is live on GitHub.** `git ls-remote --tags origin | grep vX.Y.Z` prints the tag.
- [ ] **Live `INSTALL.md` matches.** `curl -sS -o /tmp/live_install.md -w "HTTP %{http_code}\n" https://raw.githubusercontent.com/traylinx/lope/main/INSTALL.md` returns `HTTP 200` and the file content matches local `INSTALL.md`.
- [ ] **Live `CHANGELOG.md` matches.** Same curl pattern for `CHANGELOG.md`. Spot-check the new section is visible.
- [ ] **Paste-a-prompt install still works.** In a fresh Claude Code session (or any other host), paste:
  ```
  Read https://raw.githubusercontent.com/traylinx/lope/main/INSTALL.md and follow the instructions to install lope on this machine natively.
  ```
  Agent fetches, installs, reports back. For a PATCH release this is a 30-second smoke. For MINOR/MAJOR, run it in at least 2 different host CLIs.
- [ ] **Preservation-aware local sync.** Compare installed/source hashes and diffs. Back up the exact affected files, verify they have not changed since inspection, then synchronize only reviewed owning modules and version surfaces. Preserve local provider config, permissions, credentials, history and modified skills. Use a separate clean checkout for full installer/release verification. Report local-source parity separately from Git ancestry or publication.

### Announce (MINOR and MAJOR only, skip for PATCH unless the fix is user-visible)

- [ ] **Update the Harvey Brain journal** at `~/HARVEY/data/Brain/journals/YYYY_MM_DD.md` with a one-paragraph release note.
- [ ] **Update marketing drafts** at `~/HARVEY/marketing/lope/` if the release ships anything the campaign references (v0.3.0 features, version number in blog posts, etc.). Run the per-file grep: `grep -rn "v0\.[0-9]" ~/HARVEY/marketing/lope/` and fix every hit.
- [ ] **Draft the LinkedIn/Twitter announcement** in `~/HARVEY/marketing/lope/linkedin/` if the release is big enough to warrant one. Follow the existing numbering. Do not auto-send — Sebastian reviews and fires manually.
- [ ] **GitHub release** (optional): `gh release create vX.Y.Z --title "vX.Y.Z — <tagline>" --notes-file <(sed -n '/## X.Y.Z/,/## /p' CHANGELOG.md | sed '$d')` if you want GitHub's release UI populated.

### Rollback (if the release is broken)

- [ ] **Never force-push.** Cut a PATCH release instead (`./scripts/bump-version.sh X.Y.Z+1`) with a CHANGELOG line like `## 0.3.2 — revert the broken change in 0.3.1`. Fixes forward.
- [ ] **If the tag itself is poisoned** and needs removal: tell Sebastian first, delete with `git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`, then cut the corrected release. Never do this silently.

## Hard rules — do not break these

- **Never skip the 6-file sync check.** Drift between `install`'s banner and the Python engine is the exact bug that caused v0.3.1.
- **Never release without updating CHANGELOG.md.** A release without a CHANGELOG entry is worse than no release — users can't tell what changed.
- **Never tag a feature branch.** Tags must live on `main`.
- **Never commit with `git add .` or `git add -A`.** List the files explicitly.
- **Never force-push to `main` or to a tag.** If something is broken, fix forward with a new PATCH.

## Quick reference

```bash
# Example after explicit release approval and all checks above; not an automatic script:
./scripts/bump-version.sh 0.3.2                           # 1. bump
./scripts/check-version.sh                                # 2. verify sync
$EDITOR CHANGELOG.md                                      # 3. add release notes
PYTHONPATH=. python3 -m lope version                      # 4. smoke test
git add pyproject.toml lope/__init__.py install \
        CHANGELOG.md .claude-plugin/plugin.json \
        .cursor-plugin/plugin.json gemini-extension.json  # 5. stage
git commit                                                # 6. commit (HEREDOC body)
git tag -a v0.3.2 -m "v0.3.2 — <tagline>"                 # 7. tag
git push origin main                                     # 8a. push approved commit
# Verify green CI for this exact commit, then obtain/confirm publication approval.
git push origin v0.3.2                                    # 8b. publishes tag; package uploads are separately opt-in
```

That's it. Memorize nothing — read this file every single time.
