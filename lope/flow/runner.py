"""FlowRunner — walk a flow graph, dispatching each node into lope primitives.

The runner adds NO new agent/validator/CLI code. It routes over:
  agent  → Validator.generate()            (validators.py)
  review → EnsemblePool.validate()         (ensemble.py)
  judge  → ensemble vote OR generate+parse (ensemble.py / models.parse_verdict_block)
  script → gates.run_gate()                (gates.py)

It walks breadth-first in "waves": all currently-ready nodes run concurrently
(fan-out), then their routing builds the next wave. Joins (fan-in) gate a node
until all its non-loop barrier sources complete. Loops are just back-edges,
bounded entirely by per-node `max_visits` and the graph-wide `max_node_visits`
so an unsupervised run can never loop forever or run unbounded cost.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .model import (
    Blackboard,
    FlowContext,
    FlowGraph,
    FlowNode,
    NodeKind,
    NodeResult,
    edge_matches,
    outcome_instructions,
    parse_outcome_block,
    verdict_to_outcome,
)
from .report import FlowReport
from .stylesheet import parse_stylesheet, split_names

DEFAULT_TIMEOUT_SECONDS = 480
DEFAULT_MAX_WORKERS = 5

# Appended to review / judge-ensemble prompts so validators emit the canonical
# ---VERDICT---...---END--- block that every lope validator parser reads
# (validators.parse_opencode_verdict / parse_verdict_block). This MUST match the
# block emitted by executor.py / negotiator.py — a custom format here is silently
# unparseable and surfaces as "no ---VERDICT--- block found" → INFRA_ERROR.
VERDICT_INSTRUCTIONS = (
    "\n\nReply with a verdict block in EXACTLY this format. The literal "
    "---VERDICT--- and ---END--- delimiter lines are REQUIRED:\n"
    "---VERDICT---\n"
    "status: PASS | NEEDS_FIX | FAIL\n"
    "confidence: 0.0-1.0\n"
    "rationale: 1-3 sentences, terse; cite file:line where relevant\n"
    "required_fixes:\n"
    "  - fix 1   (include this section only when NEEDS_FIX or FAIL)\n"
    "---END---"
)


# ─── Node handlers (uniform signature: (node, ctx) -> NodeResult) ──


def _handle_start(node: FlowNode, ctx: FlowContext) -> NodeResult:
    return NodeResult(node.id, "started", label="start")


def _handle_exit(node: FlowNode, ctx: FlowContext) -> NodeResult:
    return NodeResult(node.id, "exited", label="exit")


def _resolve_timeout(node: FlowNode, ctx: FlowContext) -> int:
    return node.timeout or int(getattr(ctx.cfg, "timeout", DEFAULT_TIMEOUT_SECONDS))


def _handle_agent(node: FlowNode, ctx: FlowContext) -> NodeResult:
    style = ctx.resolve_style(node)
    validator = ctx.validator_by_name(style.get("primary")) or ctx.pool.primary_validator()
    prompt = ctx.blackboard.render(node.prompt)
    if not prompt.strip():
        return NodeResult(node.id, "failed", error="agent node has an empty prompt")
    timeout = _resolve_timeout(node, ctx)
    try:
        out = validator.generate(prompt, timeout=timeout)
    except NotImplementedError as exc:
        return NodeResult(
            node.id, "failed", label=validator.name,
            error=str(exc)[:300], detail="primary cannot draft (.generate unsupported)",
        )
    except Exception as exc:  # subprocess died / timeout — value, never raise
        return NodeResult(
            node.id, "failed", label=validator.name,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    out = (out or "").strip()
    return NodeResult(
        node.id, "succeeded" if out else "failed",
        label=validator.name, detail=out[:1500], raw=out,
    )


def _build_ensemble(node: FlowNode, ctx: FlowContext):
    from ..ensemble import EnsemblePool

    style = ctx.resolve_style(node)
    validators = list(ctx.pool.validators())
    names = split_names(style.get("validators"))
    if names:
        subset = [v for v in validators if v.name in names]
        if subset:
            validators = subset
    primary = style.get("primary") or getattr(ctx.cfg, "primary", None)
    if primary and not any(v.name == primary for v in validators):
        primary = None
    parallel = bool(getattr(ctx.cfg, "parallel", True))
    return EnsemblePool(validators, primary=primary, parallel=parallel)


def _handle_review(node: FlowNode, ctx: FlowContext) -> NodeResult:
    ensemble = _build_ensemble(node, ctx)
    user_prompt = ctx.blackboard.render(node.prompt) or (
        f"Review the current implementation for the goal: {ctx.blackboard.get('goal')}. "
        "Check correctness and completeness against the plan."
    )
    timeout = _resolve_timeout(node, ctx)
    result = ensemble.validate(user_prompt + VERDICT_INSTRUCTIONS, timeout=timeout)
    verdict = result.verdict
    return NodeResult(
        node.id, verdict_to_outcome(verdict.status), label="ensemble",
        detail=(verdict.rationale or "")[:500], verdict=verdict,
        raw=result.raw_response or verdict.rationale or "",
        error=result.error or "",
    )


def _parse_outcome_map(raw: Optional[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if ":" in pair:
            key, _, value = pair.partition(":")
            if key.strip():
                out[key.strip().lower()] = value.strip()
    return out


def _handle_judge(node: FlowNode, ctx: FlowContext) -> NodeResult:
    mode = (node.attr("mode") or "ensemble").strip().lower()
    outcome_map = _parse_outcome_map(node.attr("outcome_map"))
    prompt = ctx.blackboard.render(node.prompt)
    timeout = _resolve_timeout(node, ctx)

    if mode == "generate":
        allowed = split_names(node.attr("outcomes")) or ["succeeded", "failed"]
        validator = ctx.validator_by_name(
            ctx.resolve_style(node).get("primary")
        ) or ctx.pool.primary_validator()
        try:
            out = validator.generate(prompt + outcome_instructions(allowed), timeout=timeout)
        except Exception as exc:
            return NodeResult(node.id, "infra_error", label=validator.name,
                              error=f"{type(exc).__name__}: {exc}"[:300])
        token, note = parse_outcome_block(out, allowed)
        return NodeResult(
            node.id, token, label=validator.name, detail=note, raw=(out or "")[:1500],
            error="" if token != "infra_error" else "judge produced no recognized outcome",
        )

    # ensemble mode (default): the team votes; map the verdict to a route.
    ensemble = _build_ensemble(node, ctx)
    review_prompt = (prompt or "Assess the work so far.") + VERDICT_INSTRUCTIONS
    result = ensemble.validate(review_prompt, timeout=timeout)
    base = verdict_to_outcome(result.verdict.status)
    mapped = outcome_map.get(base, base)
    return NodeResult(
        node.id, mapped, label="ensemble", detail=(result.verdict.rationale or "")[:500],
        verdict=result.verdict, raw=result.raw_response or "", error=result.error or "",
    )


def _handle_script(node: FlowNode, ctx: FlowContext) -> NodeResult:
    from ..gates import GateConfigError, GateSpec, load_gate_specs, run_gate

    timeout = _resolve_timeout(node, ctx)
    cmd = node.attr("cmd")
    gate_name = node.attr("gate")
    try:
        if cmd:
            spec = GateSpec(
                name=node.id, cmd=cmd, type=(node.attr("type") or "exit"),
                timeout=timeout, path=node.attr("path"), regex=node.attr("regex"),
            )
        elif gate_name:
            specs, _ = load_gate_specs(node.attr("gate_config"), cwd=Path(ctx.cwd))
            match = [s for s in specs if s.name == gate_name]
            if not match:
                return NodeResult(node.id, "infra_error",
                                  error=f"gate {gate_name!r} not found in .lope/rules.json")
            spec = match[0]
        else:
            return NodeResult(node.id, "infra_error", error="script node needs cmd= or gate=")
        res = run_gate(spec, Path(ctx.cwd), default_timeout=timeout)
    except GateConfigError as exc:
        return NodeResult(node.id, "infra_error", error=str(exc))
    detail = (res.stdout_tail or res.error or "")[:500]
    return NodeResult(
        node.id, "succeeded" if res.ok else "failed", label="gate",
        detail=detail, raw=res.stdout_tail or "",
    )


def _handle_gate(node: FlowNode, ctx: FlowContext) -> NodeResult:
    """Optional human approval pause. Omitted in autonomous flows."""
    prompt = ctx.blackboard.render(node.prompt) or "Approve to continue?"
    ctx.print_fn(f"\n[human gate] {prompt}")
    try:
        answer = input("approve? [y/N] ").strip().lower()
    except EOFError:
        return NodeResult(node.id, "failed", detail="no interactive stdin for human gate")
    return NodeResult(
        node.id, "succeeded" if answer in ("y", "yes", "approve") else "failed",
        label="human",
    )


_HANDLERS = {
    NodeKind.START: _handle_start,
    NodeKind.EXIT: _handle_exit,
    NodeKind.AGENT: _handle_agent,
    NodeKind.REVIEW: _handle_review,
    NodeKind.JUDGE: _handle_judge,
    NodeKind.SCRIPT: _handle_script,
    NodeKind.GATE: _handle_gate,
}

_SUCCESS_OUTCOMES = {"succeeded", "passed", "started", "exited", "done", "ok"}
_FAIL_OUTCOMES = {"failed", "infra_error"}


# ─── The runner ──────────────────────────────────────────────────


class FlowRunner:
    def __init__(
        self,
        graph: FlowGraph,
        pool,
        cfg,
        cwd: Optional[str] = None,
        max_node_visits: Optional[int] = None,
        print_fn=print,
        stylesheet=None,
    ):
        self.graph = graph
        self.pool = pool
        self.cfg = cfg
        self.cwd = cwd or os.getcwd()
        self.print_fn = print_fn
        self._max_node_visits = max_node_visits or graph.max_node_visits
        if stylesheet is None:
            stylesheet = parse_stylesheet(graph.stylesheet_text)
        self.stylesheet = stylesheet
        env_workers = os.environ.get("LOPE_FLOW_WORKERS")
        self._max_workers = int(env_workers) if env_workers else DEFAULT_MAX_WORKERS

    def run(self, task: str = "") -> FlowReport:
        from .validate import validate_graph

        validate_graph(self.graph)  # raises FlowConfigError on structural problems

        goal = task or self.graph.graph_attrs.get("goal", "")
        bb = Blackboard({"task": task, "goal": goal})
        if "$" not in goal:
            bb.set("goal", goal)
        ctx = FlowContext(bb, self.pool, self.cfg, self.cwd, self.stylesheet, self.print_fn)
        report = FlowReport(graph_name=self.graph.name)

        start = self.graph.start_node()
        ready: List[FlowNode] = [start]
        visit_counts: Dict[str, int] = defaultdict(int)
        pending_joins: Dict[str, set] = {}
        global_visits = 0
        terminated = False
        t0 = time.perf_counter()

        while ready and not terminated:
            wave = self._dedupe(ready)
            ready = []
            runnable: List[FlowNode] = []
            for node in wave:
                global_visits += 1
                if global_visits > self._max_node_visits:
                    report.escalation = self._escalation(
                        node, len(report.node_results),
                        f"global max_node_visits ({self._max_node_visits}) exceeded",
                    )
                    report.ok = False
                    terminated = True
                    break
                visit_counts[node.id] += 1
                if visit_counts[node.id] > node.max_visits:
                    report.escalation = self._escalation(
                        node, len(report.node_results),
                        f"node {node.id!r} exceeded max_visits={node.max_visits}",
                    )
                    report.ok = False
                    terminated = True
                    break
                runnable.append(node)
            if terminated:
                break

            next_ready: List[FlowNode] = []
            for node, result in self._run_wave(runnable, ctx):
                report.add(result)
                bb.put_result(result)
                self._print_step(node, result)

                if node.kind == NodeKind.EXIT:
                    report.ok = node.status != "fail"
                    terminated = True
                    break

                if result.outcome == "infra_error":
                    if node.retry_target:
                        next_ready.append(self.graph.node(node.retry_target))
                        continue
                    report.escalation = self._escalation(
                        node, len(report.node_results),
                        f"infra_error: {result.error or result.detail}",
                        result.verdict,
                    )
                    report.ok = False
                    terminated = True
                    break

                chosen = [
                    e for e in self.graph.edges_from(node.id) if edge_matches(e, result)
                ]
                if not chosen:
                    report.escalation = self._escalation(
                        node, len(report.node_results),
                        f"dead-end at {node.id!r} (outcome={result.outcome!r}, "
                        "no matching out-edge)",
                        result.verdict,
                    )
                    report.ok = False
                    terminated = True
                    break

                for edge in chosen:
                    self._enqueue(edge, node, next_ready, pending_joins)

            ready = next_ready

        if not terminated and not ready and report.ok and report.escalation is None:
            report.ok = False
            report.escalation = self._escalation(
                start, len(report.node_results),
                "frontier drained without reaching an exit node",
            )

        report.total_duration_seconds = time.perf_counter() - t0
        report.blackboard_snapshot = bb.snapshot()
        return report

    # ── internals ──

    def _enqueue(self, edge, source_node, next_ready, pending_joins) -> None:
        target = self.graph.node(edge.target)
        if edge.loop_restart:
            pending_joins.pop(target.id, None)  # re-arm any barrier on the next lap
            next_ready.append(target)
            return
        if self.graph.is_join(target.id):
            if target.id not in pending_joins:
                pending_joins[target.id] = set(self.graph.barrier_sources(target.id))
            pending_joins[target.id].discard(source_node.id)
            if not pending_joins[target.id]:
                pending_joins.pop(target.id)
                next_ready.append(target)
            return
        next_ready.append(target)

    def _run_wave(
        self, nodes: List[FlowNode], ctx: FlowContext
    ) -> List[Tuple[FlowNode, NodeResult]]:
        if not nodes:
            return []
        if len(nodes) == 1:
            return [(nodes[0], self._run_node_guarded(nodes[0], ctx))]
        results: List[Tuple[FlowNode, NodeResult]] = []
        with ThreadPoolExecutor(max_workers=min(len(nodes), self._max_workers)) as ex:
            futs = {ex.submit(self._run_node_guarded, n, ctx): n for n in nodes}
            for fut in as_completed(futs):
                node = futs[fut]
                try:
                    results.append((node, fut.result()))
                except Exception as exc:  # pragma: no cover - defensive
                    results.append((node, NodeResult(
                        node.id, "infra_error", error=f"{type(exc).__name__}: {exc}")))
        order = {n.id: i for i, n in enumerate(nodes)}
        results.sort(key=lambda nr: order.get(nr[0].id, 0))
        return results

    def _run_node_guarded(self, node: FlowNode, ctx: FlowContext) -> NodeResult:
        handler = _HANDLERS.get(node.kind)
        if handler is None:
            return NodeResult(node.id, "infra_error", error=f"no handler for {node.kind}")
        attempts = node.retry + 1
        last: Optional[NodeResult] = None
        t0 = time.perf_counter()
        for _ in range(attempts):
            try:
                last = handler(node, ctx)
            except Exception as exc:  # handlers shouldn't raise; treat as infra
                last = NodeResult(node.id, "infra_error",
                                  error=f"{type(exc).__name__}: {exc}"[:300])
            if last.outcome != "infra_error":
                break
        if last is None:  # pragma: no cover - defensive
            last = NodeResult(node.id, "infra_error", error="handler produced no result")
        last.duration_seconds = time.perf_counter() - t0
        return last

    def _escalation(self, node, index, reason, verdict=None):
        from ..models import EscalationRequired

        return EscalationRequired(
            phase_index=index, phase_name=node.id, reason=reason, last_verdict=verdict
        )

    def _print_step(self, node: FlowNode, result: NodeResult) -> None:
        if result.outcome in _SUCCESS_OUTCOMES:
            mark = "✓"
        elif result.outcome in _FAIL_OUTCOMES:
            mark = "✗"
        else:
            mark = "•"
        line = f">>> {node.id} [{node.kind.value}] → {result.outcome} {mark}"
        if result.error:
            line += f"  ({result.error[:80]})"
        self.print_fn(line)

    @staticmethod
    def _dedupe(nodes: List[FlowNode]) -> List[FlowNode]:
        seen = set()
        out = []
        for n in nodes:
            if n.id not in seen:
                seen.add(n.id)
                out.append(n)
        return out
