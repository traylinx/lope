"""Render a flow graph to SVG/PNG via the system Graphviz `dot` binary.

A flow file already IS valid DOT, so rendering shells out to `dot` when present
and degrades gracefully when it isn't (no dependency is added). We first
normalize node shapes from `type=` so a graph authored without explicit shapes
still renders with fabro-style visual semantics.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .dot import load_flow_graph
from .model import FlowGraph, NodeKind

_KIND_SHAPE = {
    NodeKind.START: "Mdiamond",
    NodeKind.EXIT: "Msquare",
    NodeKind.AGENT: "box",
    NodeKind.REVIEW: "doubleoctagon",
    NodeKind.JUDGE: "box",
    NodeKind.SCRIPT: "parallelogram",
    NodeKind.GATE: "hexagon",
}
GRAPHVIZ_TIMEOUT_SECONDS = 60


def to_canonical_dot(graph: FlowGraph) -> str:
    """Emit clean DOT with canonical shapes + edge labels, for visualization."""
    lines = [f"digraph {graph.name} {{"]
    rankdir = graph.graph_attrs.get("rankdir", "LR")
    lines.append(f'  rankdir="{rankdir}";')
    lines.append('  node [style="rounded,filled", fillcolor="#f5f5f5", fontname="Helvetica"];')
    for node in graph.nodes.values():
        shape = node.attr("shape") or _KIND_SHAPE.get(node.kind, "box")
        label = node.id
        lines.append(f'  {node.id} [shape="{shape}", label="{label}"];')
    for edge in graph.edges:
        attrs = []
        text = edge.label or (edge.condition or "")
        if text:
            attrs.append(f'label="{_escape(text)}"')
        if edge.loop_restart:
            attrs.append('style="dashed"')
        suffix = f" [{', '.join(attrs)}]" if attrs else ""
        lines.append(f"  {edge.source} -> {edge.target}{suffix};")
    lines.append("}")
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace('"', '\\"').replace("\n", " ")


def has_dot() -> bool:
    return shutil.which("dot") is not None


def render_file(path: str, out_path: str = "", fmt: str = "svg") -> str:
    """Render a .dot workflow. Returns a human-readable status string.

    If `out_path` is empty and fmt is 'dot', prints canonical DOT to the return
    value. Otherwise calls the system `dot` to write `out_path`.
    """
    graph = load_flow_graph(path)
    canonical = to_canonical_dot(graph)

    if fmt == "dot" and not out_path:
        return canonical

    if not has_dot():
        return (
            "Graphviz `dot` not found — the workflow is already valid DOT.\n"
            "Install it (`brew install graphviz`) to render, or paste the file "
            "into https://dreampuf.github.io/GraphvizOnline.\n"
            "Tip: `lope flow render <file> -T dot` prints normalized DOT to stdout."
        )

    out = out_path or str(Path(path).with_suffix(f".{fmt}"))
    try:
        proc = subprocess.run(
            ["dot", f"-T{fmt}", "-o", out],
            input=canonical, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=GRAPHVIZ_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"dot timed out after {GRAPHVIZ_TIMEOUT_SECONDS}s"
    if proc.returncode != 0:
        return f"dot failed (exit {proc.returncode}): {(proc.stderr or '').strip()[:300]}"
    return f"Rendered {out}"
