---
name: lope-flow
description: "Run a declarative GRAPH workflow where AI agents negotiate autonomously — no human gates. A flow is a DOT graph: nodes are agent turns, ensemble reviews, shell verify-steps, or judge/routers; edges carry conditions and loops. Each node dispatches into lope's existing multi-CLI executors (any CLI implements, the ensemble votes). Bounded by per-node and graph-wide visit caps so an unsupervised run can never loop forever. Use when the user wants to shape HOW agents collaborate (fan-out proposers, consensus, fix-loops) as editable, reviewable, version-controlled process — not a fixed linear sprint."
---

# Lope Flow

`lope flow` runs a **declarative graph workflow**. Where `negotiate`/`execute`/`implement` are fixed linear pipelines, `flow` lets you *draw the topology* — fan out to N proposers, consolidate, implement, fan out reviewers, loop on failure — in a DOT file you edit and version-control. It dispatches every node into lope's existing executors, so there is no new agent runtime: any CLI implements, the ensemble votes.

This is the mode for **autonomous, no-human multi-agent runs** where you want control over the process shape and a guarantee the run is bounded.

## When to use it

- The user wants agents to **negotiate what to do autonomously** (propose → consolidate → implement → review → loop) without babysitting.
- The user wants to **reshape the collaboration** (more proposers, a consensus step, an extra fix-loop) by editing a file, not Python.
- The user wants a **reviewable, diffable** process artifact, or a picture of what the agents will do.

For a one-shot plan use `/lope-negotiate`; for a fixed linear sprint use `/lope-execute` or `/lope-implement`. Reach for `flow` when the *graph shape itself* matters.

## Node types (each maps to a lope primitive)

| `type=` | What runs | Outcome it routes on |
|---|---|---|
| `start` / `exit` | entry / terminal (`exit` may set `status="fail"`) | `started` / `exited` |
| `agent` | `Validator.generate()` — the single-writer implementer turn | `succeeded` / `failed` |
| `review` | `EnsemblePool.validate()` — the whole team votes (majority) | `succeeded` / `needs_fix` / `failed` |
| `judge` | a router: ensemble vote **or** `generate` + a structured `outcome:` block | a label you declare (`ok`, `replan`, …) |
| `script` | `gates.run_gate()` — a deterministic shell test/lint step | `succeeded` / `failed` |
| `gate` | optional human approval pause (omit it for autonomous runs) | `succeeded` / `failed` |

Edges carry `condition="outcome=succeeded"` and `loop_restart="true"` for back-edges. A node's class routes through a `cli_stylesheet` (`.frontier { primary: claude; }`) — lope's take on fabro's model stylesheet, routing class → which CLI plays the role.

## Bounded autonomy (the safety guarantee)

Every node has `max_visits` (default 3); the graph has `max_node_visits` (default `max(50, 8*nodes)`). Both are enforced *before* a node runs, so any loop is finite — a non-converging run terminates with a clean escalation, never an infinite loop or unbounded cost. This is what makes "walk away and let it run" safe.

## Steps

1. **Start from a template** (don't hand-write DOT the first time):
   ```bash
   lope flow init consensus        # writes .lope/flow/consensus.dot
   # templates: consensus | judge-loop | review-gate
   ```
2. **Validate** it is runnable and bounded (catches dangling edges, dead ends, unbounded loops):
   ```bash
   lope flow validate .lope/flow/consensus.dot
   ```
3. **Render** it to see the graph (needs system graphviz; degrades gracefully):
   ```bash
   lope flow render .lope/flow/consensus.dot -o flow.svg
   lope flow render .lope/flow/consensus.dot -T dot     # normalized DOT to stdout
   ```
4. **Dry-run** to print the plan without invoking any model:
   ```bash
   lope flow run .lope/flow/consensus.dot --task "<goal>" --dry-run
   ```
5. **Run it** — autonomous, bounded, scored, journaled:
   ```bash
   lope flow run .lope/flow/consensus.dot --task "Add a /health endpoint with a test"
   ```

`--out <dir>` writes `trace.jsonl` + `report.md`; `--max-node-visits N` overrides the runaway cap; `--no-journal` skips the Brain entry; pool flags (`--validators`, `--primary`, `--timeout`, `--parallel`/`--sequential`) work as in the other modes.

## The consensus template (what autonomous looks like)

`Start → CheckDoD (judge) → 3 proposers in parallel → Consolidate (join) → Implement → Review (ensemble) → pass:Exit / fail:Postmortem → replan loops back to Implement`. No human anywhere. Branch decisions are made by the ensemble vote and judge nodes; the visit caps bound every loop.

## Authoring notes

- A judge's single decision selects **all** out-edges whose `condition` matches its outcome — that is how one decision fans out to N nodes.
- Mark a fan-in node `join="true"` so it waits for all its (non-loop) predecessors.
- A `script` node points at an inline `cmd="..."` or a named gate in `.lope/rules.json` (`gate="tests"`).
- `prompt="@file.md"` loads the prompt from disk.

## Do not

- **Do not add a human `gate` node to an "autonomous" flow** — it will block waiting for stdin. Autonomous templates have none.
- **Do not remove the visit caps** to "let it finish" — that is exactly the runaway you are bounding. Raise `max_visits` deliberately instead.
- **Do not wrap `lope flow` in a script.** It is the harness. Edit the `.dot`, run the CLI.
