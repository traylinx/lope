"""lope flow — declarative, autonomous graph workflows over lope's executors.

A workflow is a DOT graph whose nodes dispatch into lope primitives (agent
turns, ensemble reviews, shell gates, judge/routers) and whose edges carry
conditions and loops. Bounded by per-node `max_visits` and a graph-wide
`max_node_visits`, so an unsupervised run can never loop forever.

Public API:
    load_flow_graph(path) -> FlowGraph
    FlowRunner(graph, pool, cfg, cwd=...).run(task) -> FlowReport
    validate_graph(graph) -> list[str]   (raises FlowConfigError on errors)
"""

from __future__ import annotations

from .dot import FlowSyntaxError, load_flow_graph, parse_dot
from .model import (
    FlowConfigError,
    FlowEdge,
    FlowGraph,
    FlowNode,
    NodeKind,
    NodeResult,
)
from .report import FlowReport, flow_report_to_execution_report, write_flow_run
from .runner import FlowRunner
from .templates import get_template, template_names
from .validate import validate_graph

__all__ = [
    "load_flow_graph",
    "parse_dot",
    "FlowRunner",
    "FlowGraph",
    "FlowNode",
    "FlowEdge",
    "FlowReport",
    "NodeKind",
    "NodeResult",
    "FlowConfigError",
    "FlowSyntaxError",
    "validate_graph",
    "flow_report_to_execution_report",
    "write_flow_run",
    "get_template",
    "template_names",
]
