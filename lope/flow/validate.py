"""Static validation for flow graphs — `lope flow validate`.

Checks the graph is runnable and bounded BEFORE any model is invoked:
exactly one start, a reachable exit, no dangling edges, no dead ends, and —
critically for autonomous runs — no unbounded loops. Returns warnings for
softer issues. Stdlib only.
"""

from __future__ import annotations

from typing import List, Set

from .model import FlowConfigError, FlowGraph, NodeKind


def validate_graph(graph: FlowGraph) -> List[str]:
    """Raise FlowConfigError on a structural error; return a list of warnings.

    Called by FlowRunner.run before execution and by `lope flow validate`.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 1. edges reference defined nodes
    for edge in graph.edges:
        if edge.source not in graph.nodes:
            errors.append(f"edge -> {edge.target!r} (line {edge.line}): source "
                          f"{edge.source!r} is not a defined node")
        if edge.target not in graph.nodes:
            errors.append(f"edge {edge.source!r} -> (line {edge.line}): target "
                          f"{edge.target!r} is not a defined node")
    if errors:
        raise FlowConfigError("invalid flow graph:\n  - " + "\n  - ".join(errors))

    # 2. exactly one start
    starts = [n for n in graph.nodes.values() if n.kind == NodeKind.START]
    if not starts:
        errors.append('no start node (add a node with type="start")')
    elif len(starts) > 1:
        errors.append(f"{len(starts)} start nodes ({', '.join(n.id for n in starts)}); "
                      "exactly one is required")

    # 3. at least one exit, reachable
    exits = [n for n in graph.nodes.values() if n.kind == NodeKind.EXIT]
    if not exits:
        errors.append('no exit node (add a node with type="exit")')

    # 4. required attrs per kind
    for node in graph.nodes.values():
        if node.kind in (NodeKind.AGENT, NodeKind.JUDGE) and not node.prompt.strip():
            errors.append(f"node {node.id!r} (type={node.kind.value}) needs a prompt=")
        if node.kind == NodeKind.SCRIPT and not (node.attr("cmd") or node.attr("gate")):
            errors.append(f"script node {node.id!r} needs cmd= or gate=")

    # 5. reachability + dead ends (only if structure is otherwise sound)
    if starts and not errors:
        reachable = _reachable_from(graph, starts[0].id)
        for node in graph.nodes.values():
            if node.id not in reachable:
                warnings.append(f"node {node.id!r} is unreachable from start")
            if node.kind != NodeKind.EXIT and not graph.edges_from(node.id):
                errors.append(f"node {node.id!r} is a dead end (no outgoing edge "
                              "and not an exit)")
        if not any(e.id in reachable for e in exits):
            errors.append("no exit node is reachable from start")

    # 6. unbounded loops — every cycle must have a node with a finite visit bound
    if not errors:
        for cycle in _find_cycles(graph):
            graph_capped = graph.graph_attrs.get("max_node_visits") not in (None, "")
            node_capped = any(
                "max_visits" in graph.nodes[nid].attrs for nid in cycle
            )
            if not (graph_capped or node_capped):
                errors.append(
                    "loop " + " -> ".join(cycle + [cycle[0]]) + " has no visit bound; "
                    "add max_visits to a node on the loop or a graph-level "
                    "max_node_visits"
                )

    if errors:
        raise FlowConfigError("invalid flow graph:\n  - " + "\n  - ".join(errors))
    return warnings


def _reachable_from(graph: FlowGraph, start_id: str) -> Set[str]:
    seen: Set[str] = set()
    stack = [start_id]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        for edge in graph.edges_from(nid):
            stack.append(edge.target)
    return seen


def _find_cycles(graph: FlowGraph) -> List[List[str]]:
    """Return one representative node list per cycle (DFS back-edge detection)."""
    cycles: List[List[str]] = []
    color: dict = {}  # 0=unvisited,1=in-stack,2=done
    stack: List[str] = []

    def dfs(nid: str) -> None:
        color[nid] = 1
        stack.append(nid)
        for edge in graph.edges_from(nid):
            tgt = edge.target
            c = color.get(tgt, 0)
            if c == 0:
                dfs(tgt)
            elif c == 1:  # back-edge → cycle from tgt to current
                if tgt in stack:
                    cycle = stack[stack.index(tgt):]
                    cycles.append(list(cycle))
        stack.pop()
        color[nid] = 2

    for node_id in graph.nodes:
        if color.get(node_id, 0) == 0:
            dfs(node_id)
    return cycles
