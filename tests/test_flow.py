"""Tests for `lope flow` — the declarative graph workflow runner.

Covers the DOT parser, routing/outcome contract, stylesheet cascade, static
validation, and the FlowRunner over a stub validator pool (linear, fan-out,
fan-in, and — critically — that a non-converging loop HALTS with an escalation
instead of running forever).
"""

import pytest

from lope.flow import (
    FlowConfigError,
    FlowRunner,
    flow_report_to_execution_report,
    get_template,
    load_flow_graph,
    parse_dot,
    template_names,
    validate_graph,
)
from lope.flow.dot import FlowSyntaxError
from lope.flow.model import (
    Blackboard,
    FlowEdge,
    FlowNode,
    NodeKind,
    NodeResult,
    edge_matches,
    parse_outcome_block,
    verdict_to_outcome,
)
from lope.flow.stylesheet import Stylesheet
from lope.models import PhaseVerdict, ValidatorResult, VerdictStatus
from lope.validators import ValidatorPool

QUIET = lambda *a, **k: None  # noqa: E731
_OK_GEN = "Here is my output.\n---OUTCOME---\noutcome: ok\nnote: clear enough\n---END---"


class _Cfg:
    def __init__(self, parallel=False, timeout=5, primary="a"):
        self.parallel = parallel
        self.timeout = timeout
        self.primary = primary


class _FakeV:
    """A minimal Validator: votes a fixed status, drafts a fixed string."""

    def __init__(self, name, vs="PASS", gen=_OK_GEN):
        self._n, self._vs, self._gen = name, vs, gen

    @property
    def name(self):
        return self._n

    def available(self):
        return True

    def validate(self, prompt, timeout=480):
        return ValidatorResult(
            self._n,
            PhaseVerdict(status=VerdictStatus(self._vs), confidence=0.9,
                         rationale="fake", validator_name=self._n),
        )

    def generate(self, prompt, timeout=480):
        return self._gen


def _pool(vs="PASS", gen=_OK_GEN):
    return ValidatorPool([_FakeV("a", vs, gen), _FakeV("b", vs, gen)], primary="a")


# ─── DOT parser ──────────────────────────────────────────────────


def test_parse_minimal():
    g = parse_dot('digraph t { A [type="start"] B [type="exit"] A -> B }')
    assert g.name == "t"
    assert set(g.nodes) == {"A", "B"}
    assert len(g.edges) == 1 and g.edges[0].source == "A" and g.edges[0].target == "B"


def test_parse_edge_chain():
    g = parse_dot('digraph t { A[type="start"] B[type="agent",prompt="x"] C[type="exit"] A->B->C }')
    assert {(e.source, e.target) for e in g.edges} == {("A", "B"), ("B", "C")}


def test_parse_condition_and_loop():
    g = parse_dot(
        'digraph t { A[type="judge",prompt="p"] B[type="exit"] '
        'A->B [condition="outcome=succeeded"] '
        'A->A [loop_restart="true"] }'
    )
    to_b = [e for e in g.edges if e.target == "B"][0]
    assert to_b.condition == "outcome=succeeded"
    loop = [e for e in g.edges if e.target == "A"][0]
    assert loop.loop_restart is True


def test_parse_multiline_prompt():
    src = 'digraph t {\n A [type="agent", prompt="line1\nline2"]\n B[type="exit"]\n A->B\n}'
    g = parse_dot(src)
    assert "line1\nline2" in g.nodes["A"].prompt


def test_parse_when_alias_for_condition():
    g = parse_dot('digraph t { A[type="judge",prompt="p"] B[type="exit"] A->B [when="outcome=ok"] }')
    assert g.edges[0].condition == "outcome=ok"


def test_parse_syntax_error_has_line():
    with pytest.raises(FlowSyntaxError) as ei:
        parse_dot('digraph t {\n A [type="start"\n')  # unterminated bracket
    assert "line" in str(ei.value).lower()


def test_unknown_type_rejected():
    with pytest.raises(FlowConfigError):
        parse_dot('digraph t { A [type="wizard"] }')


def test_shape_implies_kind():
    g = parse_dot('digraph t { A [shape="Mdiamond"] B[shape="Msquare"] A->B }')
    assert g.nodes["A"].kind == NodeKind.START
    assert g.nodes["B"].kind == NodeKind.EXIT


def test_comments_ignored():
    g = parse_dot('digraph t {\n // a comment\n A[type="start"] # trailing\n B[type="exit"] A->B\n}')
    assert set(g.nodes) == {"A", "B"}


# ─── Routing / outcome contract ──────────────────────────────────


def test_edge_matches():
    e = FlowEdge("a", "b", condition="outcome=succeeded")
    assert edge_matches(e, NodeResult("a", "succeeded"))
    assert not edge_matches(e, NodeResult("a", "failed"))
    assert edge_matches(FlowEdge("a", "b"), NodeResult("a", "anything"))  # unconditional
    ne = FlowEdge("a", "b", condition="outcome!=failed")
    assert edge_matches(ne, NodeResult("a", "succeeded"))
    assert not edge_matches(ne, NodeResult("a", "failed"))


def test_verdict_to_outcome():
    assert verdict_to_outcome(VerdictStatus.PASS) == "succeeded"
    assert verdict_to_outcome(VerdictStatus.NEEDS_FIX) == "needs_fix"
    assert verdict_to_outcome(VerdictStatus.FAIL) == "failed"
    assert verdict_to_outcome(VerdictStatus.INFRA_ERROR) == "infra_error"


def test_parse_outcome_block():
    text = "reasoning...\n---OUTCOME---\noutcome: replan\nnote: try again\n---END---"
    tok, note = parse_outcome_block(text, ["replan", "escalate"])
    assert tok == "replan" and "try again" in note
    tok2, _ = parse_outcome_block("no block here", ["a", "b"])
    assert tok2 == "infra_error"
    # token outside the allowed set is rejected
    tok3, _ = parse_outcome_block("outcome: bogus", ["a", "b"])
    assert tok3 == "infra_error"


def test_blackboard_render():
    bb = Blackboard({"task": "build X"})
    bb.set("n1.out", "result")
    assert bb.render("Goal: $task") == "Goal: build X"
    assert bb.render("Use {n1.out}") == "Use result"
    assert bb.render("keep {unknown} intact") == "keep {unknown} intact"


# ─── Stylesheet cascade ──────────────────────────────────────────


def test_stylesheet_cascade():
    sheet = Stylesheet.parse(
        "* { primary: opencode; } .frontier { primary: claude; } #Big { primary: codex; }"
    )
    assert sheet.resolve(FlowNode("X", NodeKind.AGENT, {}))["primary"] == "opencode"
    assert sheet.resolve(FlowNode("Y", NodeKind.AGENT, {"class": "frontier"}))["primary"] == "claude"
    # id beats class
    assert sheet.resolve(FlowNode("Big", NodeKind.AGENT, {"class": "frontier"}))["primary"] == "codex"
    # inline node attr beats everything
    inline = FlowNode("Z", NodeKind.AGENT, {"class": "frontier", "primary": "aider"})
    assert sheet.resolve(inline)["primary"] == "aider"


# ─── Static validation ───────────────────────────────────────────


def test_validate_missing_start():
    g = parse_dot('digraph t { A[type="agent",prompt="p"] B[type="exit"] A->B }')
    with pytest.raises(FlowConfigError) as ei:
        validate_graph(g)
    assert "start" in str(ei.value)


def test_validate_dangling_edge():
    g = parse_dot('digraph t { A[type="start"] A -> Ghost }')
    with pytest.raises(FlowConfigError) as ei:
        validate_graph(g)
    assert "Ghost" in str(ei.value)


def test_validate_dead_end():
    g = parse_dot('digraph t { A[type="start"] B[type="agent",prompt="p"] C[type="exit"] A->B A->C }')
    # B has no outgoing edge and is not an exit
    with pytest.raises(FlowConfigError) as ei:
        validate_graph(g)
    assert "dead end" in str(ei.value).lower()


def test_validate_unbounded_loop():
    src = (
        'digraph t { S[type="start"] I[type="agent",prompt="p"] R[type="judge",prompt="p"] '
        'E[type="exit"] S->I I->R R->E [condition="outcome=succeeded"] '
        'R->I [condition="outcome=failed", loop_restart="true"] }'
    )
    g = parse_dot(src)  # no max_visits, no max_node_visits
    with pytest.raises(FlowConfigError) as ei:
        validate_graph(g)
    msg = str(ei.value).lower()
    assert "loop" in msg or "bound" in msg


def test_validate_bounded_loop_ok():
    src = (
        'digraph t { graph[max_node_visits="20"] S[type="start"] I[type="agent",prompt="p"] '
        'R[type="judge",prompt="p"] E[type="exit"] S->I I->R '
        'R->E [condition="outcome=succeeded"] R->I [condition="outcome=failed", loop_restart="true"] }'
    )
    g = parse_dot(src)
    assert validate_graph(g) == []


def test_all_templates_validate():
    for name in template_names():
        g = parse_dot(get_template(name))
        assert validate_graph(g) == []


# ─── Runner: linear / fan-out / fan-in / guards ──────────────────


def test_runner_linear_happy():
    g = parse_dot(get_template("judge-loop"))
    r = FlowRunner(g, _pool("PASS"), _Cfg(), cwd="/tmp", print_fn=QUIET).run(task="x")
    assert r.ok
    assert r.path[-1] == "Exit"


def test_runner_fanout_fanin():
    g = parse_dot(get_template("consensus"))
    r = FlowRunner(g, _pool("PASS"), _Cfg(), cwd="/tmp", print_fn=QUIET).run(task="x")
    # all three proposers ran (fan-out)
    assert {"ProposeA", "ProposeB", "ProposeC"}.issubset(set(r.path))
    # the join fired exactly once, after all three proposers (fan-in barrier)
    assert r.path.count("Consolidate") == 1
    ci = r.path.index("Consolidate")
    assert all(r.path.index(p) < ci for p in ("ProposeA", "ProposeB", "ProposeC"))
    assert r.ok and r.path[-1] == "Exit"


def test_runner_guard_halts_no_infinite_loop():
    """A review that always FAILs must terminate via a visit cap, not hang."""
    g = parse_dot(get_template("judge-loop"))
    r = FlowRunner(g, _pool("FAIL"), _Cfg(), cwd="/tmp", print_fn=QUIET).run(task="x")
    assert r.ok is False
    assert r.escalation is not None
    assert "max_visits" in str(r.escalation)
    assert len(r.node_results) < 30  # bounded — no runaway


def test_runner_global_cap():
    g = parse_dot(get_template("judge-loop"))
    r = FlowRunner(
        g, _pool("FAIL"), _Cfg(), cwd="/tmp", max_node_visits=3, print_fn=QUIET
    ).run(task="x")
    assert r.ok is False
    assert r.escalation is not None


def test_runner_dead_end_escalates():
    # judge emits an outcome with no matching out-edge -> escalation, not crash
    src = (
        'digraph t { graph[max_node_visits="10"] S[type="start"] '
        'J[type="judge",mode="generate",outcomes="left",prompt="p"] '
        'E[type="exit"] S->J J->E [condition="outcome=right"] }'
    )
    g = parse_dot(src)
    # generate returns the _OK_GEN block whose outcome 'ok' is not in {left}
    r = FlowRunner(g, _pool("PASS"), _Cfg(), cwd="/tmp", print_fn=QUIET).run(task="x")
    assert r.ok is False
    assert r.escalation is not None


def test_report_adapter_feeds_auditor():
    g = parse_dot(get_template("judge-loop"))
    r = FlowRunner(g, _pool("PASS"), _Cfg(), cwd="/tmp", print_fn=QUIET).run(task="x")
    er = flow_report_to_execution_report(r)
    from lope.auditor import Auditor
    from lope.models import ExecutionReport

    assert isinstance(er, ExecutionReport)
    assert er.ok and er.sprint_doc.phases
    # the Auditor consumes it without error
    assert "FLOW-judge_loop" in Auditor().scorecard(er)


def test_load_flow_graph_from_disk(tmp_path):
    p = tmp_path / "wf.dot"
    p.write_text(get_template("judge-loop"))
    g = load_flow_graph(str(p))
    assert g.name == "judge_loop"
    assert validate_graph(g) == []


def test_verdict_instructions_round_trip_through_parser():
    """Regression: flow's VERDICT_INSTRUCTIONS must elicit the canonical
    ---VERDICT---...---END--- block that lope's validator parsers read.

    A custom verdict format here is silently unparseable and surfaces on every
    review/judge-ensemble node as "no ---VERDICT--- block found" -> INFRA_ERROR,
    which then escalates the whole run. Caught by dogfooding PR #6 (codex + agy
    reviewed the flow code and every verdict came back unparseable).
    """
    from lope.flow.runner import VERDICT_INSTRUCTIONS
    from lope.validators import parse_opencode_verdict

    assert "---VERDICT---" in VERDICT_INSTRUCTIONS
    assert "---END---" in VERDICT_INSTRUCTIONS

    # A model reply that follows the instructions must parse to a real status,
    # not the INFRA_ERROR sentinel.
    reply = (
        "Sure, here is my assessment after reading the code.\n"
        "---VERDICT---\n"
        "status: NEEDS_FIX\n"
        "confidence: 0.8\n"
        "rationale: example rationale citing runner.py:1\n"
        "required_fixes:\n"
        "  - tighten the guard\n"
        "---END---\n"
    )
    verdict = parse_opencode_verdict(reply, validator_name="probe", fallback_duration=0.0)
    assert verdict.status.name == "NEEDS_FIX"


# ---------------------------------------------------------------------------
# Hardening fixes surfaced by the PR #6 dogfood review (codex + agy)
# ---------------------------------------------------------------------------


def test_retry_is_clamped_to_max_retries():
    """Finding 1: an uncapped `retry` lets one counted visit fan out into
    unbounded model calls. retry is clamped to MAX_RETRIES."""
    from lope.flow.model import MAX_RETRIES

    assert FlowNode(id="x", kind=NodeKind.AGENT, attrs={"retry": "100"}).retry == MAX_RETRIES
    assert FlowNode(id="y", kind=NodeKind.AGENT, attrs={"retry": "2"}).retry == 2
    assert FlowNode(id="z", kind=NodeKind.AGENT, attrs={"retry": "-5"}).retry == 0


def test_validate_warns_on_excessive_retry():
    from lope.flow.model import MAX_RETRIES

    dot = (
        'digraph g {\n'
        '  graph [max_node_visits="10"]\n'
        '  S [type="start"]\n'
        f'  A [type="agent", prompt="do it", retry="{MAX_RETRIES + 50}"]\n'
        '  E [type="exit"]\n'
        '  S -> A\n'
        '  A -> E [condition="outcome=succeeded"]\n'
        '}'
    )
    warnings = validate_graph(parse_dot(dot))
    assert any("retry" in w and "clamp" in w.lower() for w in warnings)


def _agent_graph(prompt: str) -> str:
    return (
        'digraph g {\n'
        '  graph [max_node_visits="5"]\n'
        '  S [type="start"]\n'
        f'  A [type="agent", prompt="{prompt}"]\n'
        '  E [type="exit"]\n'
        '  S -> A\n'
        '  A -> E [condition="outcome=succeeded"]\n'
        '}'
    )


def test_at_file_rejects_absolute_path(tmp_path):
    """Finding 4: a hostile graph must not exfiltrate arbitrary files."""
    p = tmp_path / "wf.dot"
    p.write_text(_agent_graph("@/etc/passwd"))
    with pytest.raises(FlowConfigError):
        load_flow_graph(str(p))


def test_at_file_rejects_parent_escape(tmp_path):
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    sub = tmp_path / "graphs"
    sub.mkdir()
    p = sub / "wf.dot"
    p.write_text(_agent_graph("@../secret.txt"))
    with pytest.raises(FlowConfigError):
        load_flow_graph(str(p))


def test_at_file_loads_relative_within_dir(tmp_path):
    (tmp_path / "p.md").write_text("REVIEW THIS CODE")
    p = tmp_path / "wf.dot"
    p.write_text(_agent_graph("@p.md"))
    g = load_flow_graph(str(p))
    assert g.nodes["A"].prompt == "REVIEW THIS CODE"


def test_report_md_and_trace_redact_secrets(tmp_path):
    """Finding 5: report.md is built from scorecard() which embeds node errors
    verbatim; secrets in a CLI's stderr must not land on disk."""
    from lope.flow import write_flow_run
    from lope.flow.report import FlowReport

    fr = FlowReport(graph_name="g")
    fr.add(NodeResult("N", "infra_error", label="agent",
                      error="leaked key sk-ABCD1234deadbeefXYZ in stderr"))
    fr.ok = False
    out = write_flow_run(fr, str(tmp_path / "run"))

    md = (out / "report.md").read_text(encoding="utf-8")
    trace = (out / "trace.jsonl").read_text(encoding="utf-8")
    assert "sk-ABCD1234deadbeefXYZ" not in md
    assert "sk-ABCD1234deadbeefXYZ" not in trace
    assert "sk-<redacted>" in md
