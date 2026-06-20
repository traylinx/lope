"""Bundled flow templates — `lope flow init <name>`.

Embedded as strings (not data files) so they ship in the wheel with zero
package-data plumbing. `lope flow init consensus` writes one to disk.
"""

from __future__ import annotations

from typing import Dict

CONSENSUS = r'''// consensus.dot — fully autonomous multi-agent consensus (no human gates).
// 3 agents propose in parallel -> consolidate -> implement -> ensemble review
// -> pass exits, fail loops back through a postmortem. Bounded by max_visits.
digraph consensus {
  graph [
    goal="$task",
    rankdir="LR",
    max_node_visits="80",
    cli_stylesheet="
      *          { primary: opencode; }
      .frontier  { primary: claude; }
    "
  ]

  Start       [ type="start", shape="Mdiamond" ]

  CheckDoD    [ type="judge", mode="generate", outcomes="ok,refine", max_visits="3",
                prompt="Goal: $task\n\nIs this goal specific enough to implement without guessing? Choose 'ok' if it is clear, or 'refine' if it needs a sharper definition of done." ]
  Refine      [ type="agent",
                prompt="The goal '$task' is underspecified. Write a concrete definition of done (acceptance criteria, files, tests) to .ai/dod.md." ]

  ProposeA    [ type="agent", class="frontier",
                prompt="Goal: $task\nRead .ai/dod.md if it exists. Propose an implementation approach, write it to .ai/proposal_a.md, and give a 2-line summary." ]
  ProposeB    [ type="agent",
                prompt="Goal: $task\nPropose a DIFFERENT implementation approach. Write it to .ai/proposal_b.md and summarize in 2 lines." ]
  ProposeC    [ type="agent",
                prompt="Goal: $task\nPropose a third, simpler approach. Write it to .ai/proposal_c.md and summarize in 2 lines." ]

  Consolidate [ type="agent", join="true",
                prompt="Read .ai/proposal_a.md, .ai/proposal_b.md, .ai/proposal_c.md. Merge them into ONE plan at .ai/plan.md, resolving conflicts and keeping the strongest ideas." ]

  Implement   [ type="agent", class="frontier", timeout="1200", max_visits="4",
                prompt="Implement the plan in .ai/plan.md for goal: $task. Write the code and tests directly with your own tools." ]

  Review      [ type="review",
                prompt="Goal: $task\nReview the current implementation against the plan in .ai/plan.md. Check correctness and completeness." ]

  Postmortem  [ type="judge", mode="generate", outcomes="replan,escalate", max_visits="3",
                prompt="The review did not pass for goal: $task. Briefly diagnose the gap, write notes to .ai/postmortem.md, then choose 'replan' to try again or 'escalate' to stop." ]

  Exit        [ type="exit", shape="Msquare" ]
  Fail        [ type="exit", shape="Msquare", status="fail" ]

  Start       -> CheckDoD
  CheckDoD    -> ProposeA [ condition="outcome=ok" ]
  CheckDoD    -> ProposeB [ condition="outcome=ok" ]
  CheckDoD    -> ProposeC [ condition="outcome=ok" ]
  CheckDoD    -> Refine   [ condition="outcome=refine" ]
  Refine      -> CheckDoD [ loop_restart="true" ]

  ProposeA    -> Consolidate
  ProposeB    -> Consolidate
  ProposeC    -> Consolidate
  Consolidate -> Implement
  Implement   -> Review
  Review      -> Exit       [ label="Pass",     condition="outcome=succeeded" ]
  Review      -> Postmortem [ label="Fix",      condition="outcome=needs_fix" ]
  Review      -> Postmortem [ label="Fail",     condition="outcome=failed" ]
  Postmortem  -> Implement  [ label="Replan",   condition="outcome=replan", loop_restart="true" ]
  Postmortem  -> Fail       [ label="Escalate", condition="outcome=escalate" ]
}
'''

JUDGE_LOOP = r'''// judge-loop.dot — the minimal autonomous loop: implement, ensemble-review,
// pass exits / fail loops back. The smallest useful flow.
digraph judge_loop {
  graph [
    goal="$task",
    rankdir="LR",
    max_node_visits="40",
    cli_stylesheet="* { primary: opencode; }"
  ]

  Start     [ type="start", shape="Mdiamond" ]
  Implement [ type="agent", timeout="1200", max_visits="4",
              prompt="Implement: $task. Write code and tests directly with your own tools." ]
  Review    [ type="review",
              prompt="Review the implementation for: $task. Is it correct and complete?" ]
  Exit      [ type="exit", shape="Msquare" ]

  Start     -> Implement
  Implement -> Review
  Review    -> Exit      [ label="Pass", condition="outcome=succeeded" ]
  Review    -> Implement [ label="Fix",  condition="outcome=needs_fix", loop_restart="true" ]
  Review    -> Implement [ label="Fail", condition="outcome=failed",    loop_restart="true" ]
}
'''

REVIEW_GATE = r'''// review-gate.dot — evidence before judgment: implement, run a deterministic
// script gate (tests/lint), THEN have the ensemble review. Replace the Tests
// cmd with your real command, or point it at a named gate in .lope/rules.json.
digraph review_gate {
  graph [
    goal="$task",
    rankdir="LR",
    max_node_visits="40",
    cli_stylesheet="* { primary: claude; }"
  ]

  Start     [ type="start",  shape="Mdiamond" ]
  Implement [ type="agent",  max_visits="4",
              prompt="Implement: $task. Write code and tests directly." ]
  Tests     [ type="script", shape="parallelogram",
              cmd="echo 'replace this with your test command, e.g. python -m pytest -q'" ]
  Review    [ type="review",
              prompt="Review the implementation for: $task, taking the test output into account." ]
  Exit      [ type="exit",   shape="Msquare" ]
  Fail      [ type="exit",   shape="Msquare", status="fail" ]

  Start     -> Implement
  Implement -> Tests
  Tests     -> Review    [ condition="outcome=succeeded" ]
  Tests     -> Implement [ condition="outcome=failed", loop_restart="true" ]
  Review    -> Exit      [ condition="outcome=succeeded" ]
  Review    -> Implement [ condition="outcome=needs_fix", loop_restart="true" ]
  Review    -> Fail      [ condition="outcome=failed" ]
}
'''

TEMPLATES: Dict[str, str] = {
    "consensus": CONSENSUS,
    "judge-loop": JUDGE_LOOP,
    "review-gate": REVIEW_GATE,
}


def template_names() -> list:
    return sorted(TEMPLATES)


def get_template(name: str) -> str:
    try:
        return TEMPLATES[name]
    except KeyError:
        raise KeyError(
            f"unknown template {name!r}; available: {', '.join(template_names())}"
        ) from None
