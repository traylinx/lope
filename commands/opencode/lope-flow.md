---
name: lope-flow
description: Run a declarative DOT graph workflow where AI agents negotiate autonomously — fan-out proposers, ensemble review, judge-routers, fix-loops. No human gates required; bounded by per-node and graph-wide visit caps. For shaping HOW agents collaborate as editable, version-controlled process.
agent: build
---

# Lope flow

Run a workflow defined as a **Graphviz DOT graph**. Nodes are agent turns, ensemble reviews, shell verify-steps, or judge/routers; edges carry conditions and loops. Each node dispatches into lope's existing multi-CLI executors — any CLI implements, the ensemble votes. Autonomous (human gates optional) and bounded (a non-converging loop halts with an escalation, never an infinite loop).

## What to do

1. **Start from a template** — don't hand-write DOT the first time:

```bash
lope flow init consensus        # writes .lope/flow/consensus.dot
# templates: consensus | judge-loop | review-gate  (lope flow list)
```

2. **Validate** it is runnable and bounded:

```bash
lope flow validate .lope/flow/consensus.dot
```

3. **Render** it to see the graph (needs system graphviz; degrades gracefully):

```bash
lope flow render .lope/flow/consensus.dot -o flow.svg
```

4. **Run it** — autonomous, scored, journaled:

```bash
lope flow run .lope/flow/consensus.dot --task "<the user's goal>"
```

Optional flags: `--dry-run` (print the plan, run nothing), `--out DIR` (write trace.jsonl + report.md), `--max-node-visits N` (override the runaway cap), `--no-journal`, plus the pool flags `--validators`, `--primary`, `--timeout`, `--parallel`/`--sequential`.

## Node types

`agent` (generate) · `review` (ensemble majority vote) · `judge` (router: ensemble vote or generate + an `outcome:` block) · `script` (a shell gate from `.lope/rules.json` or inline `cmd=`) · plus `start`/`exit` and an optional human `gate`. A `cli_stylesheet` routes a node's class → which CLI plays the role.

## When to use

Use `/lope-flow` when the user wants agents to **negotiate autonomously as a graph** (propose → consolidate → implement → review → loop), to **reshape how they collaborate**, or to **see** what the agents will do. For a one-shot plan use `/lope-negotiate`; for a fixed linear sprint use `/lope-execute` or `/lope-implement`.

## Do not

- Do not add a human `gate` node to an autonomous flow — it blocks on stdin.
- Do not remove the visit caps to "let it finish" — raise `max_visits` deliberately instead.
- Do not wrap `lope flow` in a script. Edit the `.dot`, run the CLI.
