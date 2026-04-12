# Lope — Conversation Samples

Eight end-to-end samples showing how to use lope by **talking** to your AI agent in natural language. No slash commands required. Each sample shows the user's prompt, what the agent should recognize, and the actual lope invocation it runs.

All samples assume lope is installed (paste-the-prompt install, see `INSTALL.md`) and at least two AI CLIs are on `PATH`.

---

## Sample 1 — Engineering sprint (JWT auth refactor)

**You say:**

> Let's plan the auth refactor. I want to move from session cookies to JWT with refresh token rotation. This needs to be right — don't break the existing sessions. Get gemini and opencode to both review the plan before I touch any code.

**Agent recognizes:**
- Multi-phase engineering work (scaffold → middleware → rotation → migration)
- "Don't break things" is a consequential signal
- "Get gemini and opencode to review" is an explicit ensemble request
- Domain: engineering

**Agent runs:**

```bash
lope negotiate "JWT auth refactor with refresh token rotation, preserving existing sessions during migration" \
    --domain engineering \
    --max-rounds 3 \
    --context "Moving from session cookies to JWT. Must not invalidate existing user sessions during rollout."
```

**What you see:**

```
Drafter: claude-code  ·  Reviewers: gemini-cli, opencode

Round 1  drafter proposes 4-phase sprint
Round 1  gemini-cli + opencode review... NEEDS_FIX (0.74)
         - Phase 3 doesn't address existing session migration
         - Missing rate limit on refresh endpoint
         - No test for token expiry edge case
Round 2  drafter revises with specific fixes
Round 2  gemini-cli + opencode review... PASS (0.91)

Saved: SPRINT-JWT-AUTH-REFRESH.md
Next:  /lope-execute SPRINT-JWT-AUTH-REFRESH.md
```

---

## Sample 2 — Business sprint (Q4 launch campaign)

**You say:**

> Negotiate a Q4 launch campaign for our new enterprise tier. I want 4-5 phases — positioning, content, outbound, and a post-launch retro. Keep it tight, no fluff.

**Agent recognizes:**
- Multi-phase business work (not code)
- Explicit phase count hint (4-5)
- "No fluff" is a quality signal
- Domain: business

**Agent runs:**

```bash
lope negotiate "Q4 launch campaign for enterprise tier — positioning, content, outbound, retro" \
    --domain business \
    --max-rounds 3
```

**What you see:**

- Drafter proposes a business-format sprint with phases like *Positioning Brief*, *Content Calendar*, *Outbound Sequence*, *Launch Week Ops*, *Post-launch Retro*
- Each phase has `**Goal:**`, `**Deliverables:**` (instead of "Artifacts"), `**Success Metrics:**` (instead of "Tests")
- Validators review from a marketing-ops angle, not a code angle
- Lint catches any `TBD` / `TODO` / placeholder text before validator rounds start

---

## Sample 3 — Research sprint (systematic review)

**You say:**

> Scope out a systematic review of post-training RL techniques for small LMs. I want to cover RLHF, DPO, GRPO, and PPO variants. The methodology needs to be rigorous.

**Agent recognizes:**
- Multi-phase research work
- "Rigorous methodology" is a structural signal
- Domain: research

**Agent runs:**

```bash
lope negotiate "Systematic review of post-training RL techniques for small LMs (RLHF, DPO, GRPO, PPO)" \
    --domain research \
    --max-rounds 3 \
    --context "Must follow PRISMA-adjacent methodology. Scope: small LMs (< 7B params). Include reproducibility assessment."
```

**What you see:**

- Sprint doc with phases like *Search Protocol*, *Screening Criteria*, *Data Extraction Schema*, *Quality Assessment*, *Synthesis*
- Per-phase `**Artifacts:**` and `**Validation Criteria:**`
- Research-domain validator prompts focus on reproducibility, selection bias, methodology rigor

---

## Sample 4 — Tiny-sprint smoke test (new machine)

**You say:**

> I just installed lope. Run a quick smoke test to confirm it works.

**Agent recognizes:**
- Smoke test request (not a real sprint)
- Use `--max-rounds 1` so it finishes fast
- Domain: engineering (default)

**Agent runs:**

```bash
lope negotiate --domain engineering --max-rounds 1 \
    "rename a constant in one file"
```

**What you see:**

- One drafter call, one validator round
- Validators almost certainly escalate (because the goal is tiny and there's nothing substantial to negotiate)
- **Graceful escalation is the success signal** — lope completed end-to-end without a Python traceback
- If you see a stack trace, lope is misconfigured. Report the exact error.

---

## Sample 5 — Skip negotiate, just execute an existing sprint doc

**You say:**

> I already have a sprint doc at `SPRINT-RATE-LIMIT.md`. Run it.

**Agent recognizes:**
- User has a plan, wants to execute
- Skip negotiate, go straight to execute

**Agent runs:**

```bash
lope execute SPRINT-RATE-LIMIT.md
```

**What you see:**

- Phase-by-phase flow
- Per-phase two-stage review: spec compliance first, then code quality
- NEEDS_FIX on stage 1 (spec) short-circuits the quality pass and retries with specific fixes
- PASS on stage 1 runs stage 2; both stages recorded in the final scorecard

---

## Sample 6 — Natural language for a single edit (should NOT invoke lope)

**You say:**

> Rename `MAX_RETRIES` to `MAX_RETRY_ATTEMPTS` in `src/config.py`.

**Agent recognizes:**
- Single-edit task
- One file, one find-and-replace
- `using-lope` skill's "When NOT to trigger" list: this is exactly a case to skip

**Agent does:**

- **Does not invoke lope.** Just edits the file directly.
- If you wanted lope here, you'd say "plan a rename" or "refactor the retry constants" — but for a one-line rename, lope is overkill and would waste your time.

This is important: `using-lope` is deliberately scoped so that trivial edits don't trigger a 90-second validator roundtrip. The skill's "When NOT to trigger" list is load-bearing.

---

## Sample 7 — Non-code sprint: GDPR compliance audit

**You say:**

> We need to plan a GDPR compliance audit for our data pipeline. Think through retention, subject access requests, deletion propagation, and the audit log. This is legal-sensitive work — I want more than one model to sign off.

**Agent recognizes:**
- Multi-phase compliance work
- "Legal-sensitive" + "more than one model" are strong ensemble signals
- Domain: business (compliance work lives here)

**Agent runs:**

```bash
lope negotiate "GDPR compliance audit for data pipeline — retention, SAR, deletion propagation, audit log" \
    --domain business \
    --max-rounds 4 \
    --context "Legal-sensitive. Require multi-validator consensus before any phase can be marked PASS."
```

**What you see:**

- Sprint doc with phases like *Retention Policy Review*, *SAR Flow Audit*, *Deletion Propagation Test*, *Audit Log Completeness*
- Validators asked to push back on legal ambiguity
- Evidence gate (v0.3+) refuses rubber-stamp PASS votes — validators must cite specific policy sections or data flow diagrams

---

## Sample 8 — "Can you use lope to draft 3 blog posts about itself?" (meta dogfood)

**You say:**

> Draft 3 blog posts about lope for the launch. Use lope itself to negotiate the campaign.

**Agent recognizes:**
- Meta-dogfood request
- Multi-phase business work (3 posts + positioning + scheduling)
- Domain: business

**Agent runs:**

```bash
lope negotiate "Draft 3 launch blog posts for lope — launch announcement, intelligent caveman deep dive, non-dev use cases" \
    --domain business \
    --max-rounds 3
```

**What you see:**

- Sprint doc with phases like *Launch Announcement Draft*, *Caveman Mode Deep Dive Draft*, *Non-Dev Use Cases Draft*, and a final *Review + Schedule* phase
- Validators review from an editorial angle — "does the lede bite? is the claim defensible?"
- Lint catches any marketing clichés that look like placeholder text

**Do NOT:** write a Python script that imports lope and loops over a list of post titles. Lope is already a CLI. You invoke it once per sprint and let the ensemble do the work. No wrapper scripts.

---

## Patterns

Across all 8 samples, a few patterns:

1. **Natural language first, slash command as fallback.** Your agent handles the mapping. You only type `/lope-negotiate` when you want precise control.
2. **Domain matters.** `engineering` (default), `business`, `research`. Same loop, different validator role + labels.
3. **Context is cheap.** Pass `--context` with constraints, stakeholders, or rollout concerns. Validators use it to push back harder.
4. **Single edits skip lope.** The `using-lope` skill's anti-trigger list is load-bearing. Trivial work should not get a sprint.
5. **Validators pushing back is the success signal.** Lope's whole job is catching what one model would miss. An ensemble that rubber-stamps everything defeats the point.
6. **Do not wrap lope.** The CLI is the harness. No Python scripts, no pipeline scaffolds, no "lope_runner.sh". Just `lope <mode> <args>`.

## What each slash command actually does

| Slash command | When | Skill doc |
|---|---|---|
| `/lope` | Umbrella — explains the three modes and when to pick each | [`skills/lope/SKILL.md`](../skills/lope/SKILL.md) |
| `/lope-negotiate` | Draft a sprint doc with multi-round validator review | [`skills/lope-negotiate/SKILL.md`](../skills/lope-negotiate/SKILL.md) |
| `/lope-execute` | Run phases with validator-in-the-loop retry (two-stage review in v0.3+) | [`skills/lope-execute/SKILL.md`](../skills/lope-execute/SKILL.md) |
| `/lope-audit` | Generate scorecard + append to journal | [`skills/lope-audit/SKILL.md`](../skills/lope-audit/SKILL.md) |
| (auto) `using-lope` | Meta-skill — recognizes natural language and invokes the right mode | [`skills/using-lope/SKILL.md`](../skills/using-lope/SKILL.md) |

## Domains

| Domain | Use for | Artifact label | Check label |
|---|---|---|---|
| `engineering` (default) | code, software, infra, devops | Files | Tests |
| `business` | marketing, finance, ops, consulting, management | Deliverables | Success Metrics |
| `research` | studies, systematic reviews, academic work | Artifacts | Validation Criteria |

## Escape hatches

| Env var | Effect |
|---|---|
| `LOPE_LINT=off` | Skip the no-placeholder lint on drafts |
| `LOPE_EVIDENCE_GATE=off` | Skip the verification-before-completion gate on PASS verdicts |
| `LOPE_SINGLE_STAGE=1` | Revert execute mode to legacy single-pass validator review (not two-stage) |
| `LOPE_HOOK=off` | Suppress the SessionStart briefing |
| `LOPE_CAVEMAN=off` | Disable token-compression caveman directive on validator prompts |
| `LOPE_LLM_URL` | Optional hosted LLM fallback when primary validator can't draft |

Use the escape hatches for debugging or when your validator pool has unusual constraints. The defaults are what you want 95% of the time.
