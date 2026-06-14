You are Harvey, working in the Lope source repo, not HoCa.

Goal: implement and release Lope patch `v0.10.4` for negotiate timeout diagnostics.

Repo path:
`/Users/sebastian/Projects/lope`

Sprint doc:
`/Users/sebastian/Projects/lope/development/sprints/014-negotiate-timeout-diagnostics/SPRINT.md`

Context:
HoCa Sprint 011 exposed a Lope operator-experience bug. `pi` and `opencode` were not broken. `lope negotiate` was run with a too-low timeout and a large inlined context. Lope did not clearly explain:
- `--context-file` is read and inlined into the model prompt, not attached as a file.
- The generated drafter prompt size was large.
- The CLI timeout override replaced the safer global timeout.
- Lope invokes raw `pi`, not the user's zsh alias.
- Lope invokes `opencode run --pure --model myprovider/ail-compound --format json` unless overridden.

Evidence from HoCa investigation:
- Lope global config timeout: `900s`.
- Failed runs used around `120s` timeout.
- Full HoCa Sprint 011 generated drafter prompt: ~13KB, 357 lines.
- Compact `LOPE_BRIEF.md` generated drafter prompt: ~4KB, 79 lines.
- Compact brief + `--timeout 300` completed without infra timeout. Validators returned real `NEEDS_FIX`, proving providers were available.

Task:
1. Read `SPRINT.md` fully.
2. Create feature branch, e.g. `feature/v0.10.4-negotiate-timeout-diagnostics`.
3. Implement all sprint phases:
   - negotiate context preflight with prompt/context bytes and lines
   - warning for large prompt + low timeout
   - explicit note that context files are inlined
   - better timeout diagnostics for drafter fallback failures
   - pi/opencode invocation hints without leaking prompt content or secrets
   - docs and skill updates for `LOPE_BRIEF.md` workflow
   - version bump `0.10.3` -> `0.10.4`
4. Add/adjust tests.
5. Run tests:
   - `pytest -q`
   - `PYTHONPATH=/Users/sebastian/Projects/lope python3 -m lope ask "Say OK only" --validators pi,opencode --timeout 60`
   - a compact `lope negotiate` smoke that reaches validator feedback or PASS, not infra timeout
6. Commit.
7. Push branch to `origin`.
8. If tests pass, tag/release `v0.10.4` according to existing repo release conventions.
9. Update local installed Lope at `/Users/sebastian/.lope` from the source release, but do not destroy local config, memory db, journal, or provider config.
10. Report exact commit, branch, tag/release status, tests, and local install status.

Hard rules:
- Do not touch `/Users/sebastian/Projects/ai-impact/hoca`.
- Do not print secrets, prompt bodies, tokens, or credentials in diagnostics.
- Keep changes small and Unix-style.
- If release automation is unclear, inspect existing `CHANGELOG.md`, tags, scripts, and workflows before deciding.
- If a test failure is unrelated, document exact failing test and why, but do not pretend release is clean.

Expected final output:
- Branch
- Commit hash
- Tag/release version
- Tests run and results
- Whether `/Users/sebastian/.lope` was updated
- Any unresolved blockers
