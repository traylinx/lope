"""Flow data model — the vocabulary for declarative graph workflows.

A flow is a directed graph. Nodes are agent turns / ensemble reviews / shell
verify-steps / judge-routers; edges carry conditions and may form loops. The
graph is parsed from DOT (`lope/flow/dot.py`) and walked by the FlowRunner
(`lope/flow/runner.py`), which dispatches each node into lope's *existing*
executors — nothing here re-implements a validator or a CLI call.

Stdlib only. No external deps (lope ships `dependencies = []`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set


class FlowConfigError(ValueError):
    """Invalid flow graph — bad attribute, undefined node, structural error."""


# ─── Node kinds ──────────────────────────────────────────────────


class NodeKind(str, Enum):
    """The kinds of node a flow graph can contain.

    Each maps to one lope primitive (see runner.py):
      start  — seeds the run
      agent  — Validator.generate() (the single-writer implementer turn)
      review — EnsemblePool.validate() (multi-CLI majority vote)
      judge  — a router: ensemble vote OR generate + structured OUTCOME block
      script — gates.run_gate() (deterministic shell verify/build step)
      gate   — a human approval pause (OPTIONAL; omitted in autonomous flows)
      exit   — terminal
    """

    START = "start"
    AGENT = "agent"
    REVIEW = "review"
    JUDGE = "judge"
    SCRIPT = "script"
    GATE = "gate"
    EXIT = "exit"


# Friendly aliases normalized at load time so pasted fabro graphs keep working.
_KIND_ALIASES = {
    "implement": "agent",
    "ensemble": "review",
    "consensus": "judge",
    "steer": "judge",
    "verify": "script",
    "human": "gate",
    "approve": "gate",
}

# fabro `shape=` → node kind, so a graph authored with only shapes still works.
_SHAPE_KINDS = {
    "mdiamond": "start",
    "msquare": "exit",
    "parallelogram": "script",
    "hexagon": "gate",
}


def normalize_kind(raw_type: str, shape: str = "") -> NodeKind:
    """Resolve a node kind from `type=` (preferred) or fabro `shape=`."""
    key = (raw_type or "").strip().lower()
    if not key and shape:
        key = _SHAPE_KINDS.get(shape.strip().lower(), "")
    key = _KIND_ALIASES.get(key, key)
    if not key:
        raise FlowConfigError(
            "node is missing a type (expected one of: "
            f"{', '.join(k.value for k in NodeKind)})"
        )
    try:
        return NodeKind(key)
    except ValueError:
        raise FlowConfigError(
            f"unknown node type {raw_type!r} "
            f"(valid: {', '.join(k.value for k in NodeKind)})"
        ) from None


def _truthy(value: Optional[str]) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ─── Nodes + edges ───────────────────────────────────────────────

# Hard cap on per-node `retry`. Each retry is another handler execution (another
# model call), so an uncapped `retry` would let a single counted visit fan out
# into unbounded cost. With this cap total executions stay bounded by
# max_node_visits * (1 + MAX_RETRIES), and the runner additionally charges each
# retry to the global visit budget. validate_graph warns when a node asks for more.
MAX_RETRIES = 3


@dataclass
class FlowNode:
    """One node in a flow graph. All raw DOT attributes live in `attrs`;
    typed accessors coerce the ones the runner cares about."""

    id: str
    kind: NodeKind
    attrs: Dict[str, str] = field(default_factory=dict)
    line: int = 0  # source line, for diagnostics

    # ── typed accessors (single source of truth = attrs) ──
    def attr(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.attrs.get(key, default)

    def int_attr(self, key: str, default: int) -> int:
        raw = self.attrs.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return int(str(raw).strip())
        except ValueError:
            raise FlowConfigError(
                f"node {self.id!r}: attribute {key!r} must be an integer, got {raw!r}"
            ) from None

    @property
    def prompt(self) -> str:
        return self.attrs.get("prompt", "")

    @property
    def node_class(self) -> Optional[str]:
        return self.attrs.get("class")

    @property
    def max_visits(self) -> int:
        return self.int_attr("max_visits", 3)

    @property
    def timeout(self) -> Optional[int]:
        raw = self.attrs.get("timeout")
        return int(raw) if raw not in (None, "") else None

    @property
    def retry(self) -> int:
        # Clamp to [0, MAX_RETRIES] so one node visit can never trigger
        # unbounded handler re-execution. validate_graph surfaces a warning
        # when the raw attribute exceeds the cap, so the clamp is never silent.
        return max(0, min(self.int_attr("retry", 0), MAX_RETRIES))

    @property
    def retry_target(self) -> Optional[str]:
        return self.attrs.get("retry_target")

    @property
    def parallel(self) -> int:
        return max(1, self.int_attr("parallel", 1))

    @property
    def explicit_join(self) -> Optional[bool]:
        raw = self.attrs.get("join")
        if raw is None:
            return None
        return _truthy(raw)

    @property
    def status(self) -> str:
        """For exit nodes: 'pass' (default) or 'fail'."""
        return self.attrs.get("status", "pass")


@dataclass
class FlowEdge:
    """A directed edge with an optional routing condition."""

    source: str
    target: str
    condition: Optional[str] = None  # e.g. "outcome=succeeded"
    label: str = ""
    loop_restart: bool = False
    line: int = 0


@dataclass
class FlowGraph:
    """A parsed flow: named nodes + edges + graph-level attributes."""

    name: str
    nodes: Dict[str, FlowNode] = field(default_factory=dict)
    edges: List[FlowEdge] = field(default_factory=list)
    graph_attrs: Dict[str, str] = field(default_factory=dict)

    # ── lookups ──
    def node(self, node_id: str) -> FlowNode:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise FlowConfigError(f"reference to undefined node {node_id!r}") from None

    def edges_from(self, node_id: str) -> List[FlowEdge]:
        return [e for e in self.edges if e.source == node_id]

    def in_edges(self, node_id: str) -> List[FlowEdge]:
        return [e for e in self.edges if e.target == node_id]

    def barrier_sources(self, node_id: str) -> Set[str]:
        """In-sources via NON-loop_restart edges. A node with >=2 of these is a
        fan-in barrier (join) — it waits for all of them. loop_restart edges are
        excluded so a loop back-edge never makes a node wait on its own cycle."""
        return {e.source for e in self.in_edges(node_id) if not e.loop_restart}

    def is_join(self, node_id: str) -> bool:
        node = self.nodes.get(node_id)
        explicit = node.explicit_join if node else None
        if explicit is not None:
            return explicit
        return len(self.barrier_sources(node_id)) >= 2

    def start_node(self) -> FlowNode:
        starts = [n for n in self.nodes.values() if n.kind == NodeKind.START]
        if not starts:
            raise FlowConfigError("graph has no start node (type=\"start\")")
        if len(starts) > 1:
            raise FlowConfigError(
                f"graph has {len(starts)} start nodes; exactly one is required"
            )
        return starts[0]

    def exits(self) -> List[FlowNode]:
        return [n for n in self.nodes.values() if n.kind == NodeKind.EXIT]

    @property
    def max_node_visits(self) -> int:
        raw = self.graph_attrs.get("max_node_visits")
        if raw not in (None, ""):
            try:
                return int(raw)
            except ValueError:
                raise FlowConfigError(
                    f"graph max_node_visits must be an integer, got {raw!r}"
                ) from None
        return max(50, 8 * len(self.nodes))

    @property
    def stylesheet_text(self) -> str:
        return self.graph_attrs.get("cli_stylesheet") or self.graph_attrs.get(
            "model_stylesheet", ""
        )


# ─── Routing primitives ──────────────────────────────────────────


@dataclass
class NodeResult:
    """The uniform return from every node handler. `outcome` is the canonical
    lowercase token edges match on; everything else is for the trace/blackboard."""

    node_id: str
    outcome: str
    label: str = ""
    detail: str = ""
    verdict: Any = None  # Optional[PhaseVerdict] for review/judge-ensemble nodes
    duration_seconds: float = 0.0
    error: str = ""
    raw: str = ""
    attempts: int = 1  # handler executions incl. retries (charged to the global budget)


_VERDICT_OUTCOME = {
    "PASS": "succeeded",
    "NEEDS_FIX": "needs_fix",
    "FAIL": "failed",
    "INFRA_ERROR": "infra_error",
    "INCONCLUSIVE": "infra_error",
}


def verdict_to_outcome(status: Any) -> str:
    """Map a VerdictStatus (or its .value) to a routing token. Import-free."""
    val = getattr(status, "value", str(status))
    return _VERDICT_OUTCOME.get(val, "infra_error")


_CONDITION_RE = re.compile(r"^\s*([a-zA-Z_]+)\s*(==|!=|=)\s*(.+?)\s*$")


def edge_matches(edge: FlowEdge, result: NodeResult) -> bool:
    """True if `edge` should be taken given `result`.

    An edge with no condition is unconditional (always matches). Conditions are
    `outcome=<token>` or `outcome!=<token>` (v1 supports only the `outcome` key).
    """
    cond = edge.condition
    if not cond or not cond.strip():
        return True
    m = _CONDITION_RE.match(cond)
    if not m:
        return False
    key, op, val = m.group(1), m.group(2), m.group(3).strip().strip("\"'")
    if key != "outcome":
        return False
    actual = (result.outcome or "").strip().lower()
    val = val.strip().lower()
    return actual != val if op == "!=" else actual == val


# ─── Judge OUTCOME block parser (generate-mode judges) ──────────


_OUTCOME_RE = re.compile(r"^\s*outcome\s*:\s*([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE | re.MULTILINE)
_OUTCOME_NOTE_RE = re.compile(r"^\s*note\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_outcome_block(text: str, allowed: List[str]) -> tuple:
    """Extract (outcome, note) from a judge's free-form response. Never raises.

    Looks for an `outcome: <token>` line (optionally inside an ---OUTCOME--- block).
    Returns ("infra_error", reason) if no allowed token is found — the same
    sentinel discipline as parse_verdict_block.
    """
    allowed_lower = {a.strip().lower(): a for a in allowed if a.strip()}
    note_m = _OUTCOME_NOTE_RE.search(text or "")
    note = note_m.group(1).strip() if note_m else ""
    for m in _OUTCOME_RE.finditer(text or ""):
        token = m.group(1).strip().lower()
        if not allowed_lower or token in allowed_lower:
            return allowed_lower.get(token, token), note
    return "infra_error", note or "no recognized outcome token in judge response"


def outcome_instructions(allowed: List[str]) -> str:
    """The forced grammar appended to a generate-mode judge prompt."""
    opts = " | ".join(a for a in allowed if a.strip()) or "succeeded | failed"
    return (
        "\n\nWhen you have decided, end your reply with EXACTLY this block:\n"
        "---OUTCOME---\n"
        f"outcome: <{opts}>\n"
        "note: <one short sentence explaining the choice>\n"
        "---END---"
    )


# ─── Blackboard (in-memory shared state for routing) ────────────


class Blackboard:
    """Keyed store the runner uses for routing state + prompt interpolation.

    Bulk artifacts ride the working-directory filesystem (the CLIs read/write
    files themselves); the blackboard holds outcomes, verdict digests, and small
    summaries so routing never depends on parsing files.
    """

    _DOLLAR_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")
    _BRACE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_.]*)\}")

    def __init__(self, initial: Optional[Dict[str, str]] = None):
        self._d: Dict[str, str] = dict(initial or {})

    def set(self, key: str, value: str) -> None:
        self._d[key] = value

    def get(self, key: str, default: str = "") -> str:
        return self._d.get(key, default)

    def put_result(self, result: NodeResult) -> None:
        nid = result.node_id
        self._d[f"{nid}.outcome"] = result.outcome
        self._d[f"{nid}.detail"] = result.detail
        self._d[f"{nid}.out"] = result.raw or result.detail
        if result.verdict is not None:
            self._d[f"{nid}.verdict"] = getattr(result.verdict.status, "value", "")
            self._d[f"{nid}.rationale"] = getattr(result.verdict, "rationale", "")

    def render(self, template: str) -> str:
        """Substitute `$task`/`$VAR` and `{node.key}` placeholders. Unknown
        placeholders are left intact (so literal braces in prompts survive)."""
        if not template:
            return template
        s = self._DOLLAR_RE.sub(
            lambda m: self._d.get(m.group(1), m.group(0)), template
        )
        s = self._BRACE_RE.sub(lambda m: self._d.get(m.group(1), m.group(0)), s)
        return s

    def snapshot(self) -> Dict[str, str]:
        return dict(self._d)


# ─── Runtime context handed to every node handler ──────────────


@dataclass
class FlowContext:
    """Everything a node handler needs: shared state, the validator pool, config,
    the working directory, and the resolved stylesheet."""

    blackboard: Blackboard
    pool: Any  # ValidatorPool | EnsemblePool
    cfg: Any  # LopeCfg
    cwd: str
    stylesheet: Any = None  # flow.stylesheet.Stylesheet | None
    print_fn: Callable[..., None] = print

    def resolve_style(self, node: FlowNode) -> Dict[str, str]:
        if self.stylesheet is None:
            # No stylesheet: inline node attrs still apply.
            out: Dict[str, str] = {}
            for k in ("primary", "validators", "model", "reasoning_effort", "timeout"):
                if k in node.attrs:
                    out[k] = node.attrs[k]
            return out
        return self.stylesheet.resolve(node)

    def validator_by_name(self, name: Optional[str]) -> Any:
        if not name:
            return None
        for v in self.pool.validators():
            if v.name == name:
                return v
        return None
