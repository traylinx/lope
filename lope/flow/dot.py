"""Minimal Graphviz DOT parser for flow graphs — stdlib (`re`) only.

We do NOT parse arbitrary Graphviz (no HTML labels, subgraphs, ports, ranks).
We parse the tight subset flow needs:

    digraph NAME {
        graph [ k="v", ... ]              // graph-level attributes
        node  [ ... ]                     // default node attrs (parsed, ignored)
        Ident [ k="v", ... ]              // a node
        A -> B [ k="v", ... ]             // an edge (chains A -> B -> C supported)
        // and # line comments
    }

Quoted values may span multiple lines (for prompt= / cli_stylesheet=) and use
`\\"` escapes. Same hand-rolled lineage as models.parse_verdict_block and
curl_parser.py. Errors carry a line number.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from .model import (
    FlowConfigError,
    FlowEdge,
    FlowGraph,
    FlowNode,
    _truthy,
    normalize_kind,
)


class FlowSyntaxError(FlowConfigError):
    """DOT could not be tokenized/parsed (carries a line number)."""


# ─── Tokenizer ───────────────────────────────────────────────────

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>//[^\n]* | \#[^\n]*)
  | (?P<arrow>->)
  | (?P<lbrace>\{) | (?P<rbrace>\})
  | (?P<lbrack>\[) | (?P<rbrack>\])
  | (?P<comma>,) | (?P<semi>;) | (?P<eq>=)
  | (?P<string>"(?:\\.|[^"\\])*")
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<number>-?\d+(?:\.\d+)?)
    """,
    re.VERBOSE | re.DOTALL,
)


class Token:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind: str, value: str, line: int):
        self.kind = kind
        self.value = value
        self.line = line

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Token({self.kind}, {self.value!r}, L{self.line})"


def tokenize(src: str) -> List[Token]:
    tokens: List[Token] = []
    pos = 0
    line = 1
    n = len(src)
    while pos < n:
        m = _TOKEN.match(src, pos)
        if not m:
            raise FlowSyntaxError(f"line {line}: unexpected character {src[pos]!r}")
        kind = m.lastgroup
        text = m.group()
        if kind in ("ws", "comment"):
            line += text.count("\n")
            pos = m.end()
            continue
        tokens.append(Token(kind, text, line))
        line += text.count("\n")
        pos = m.end()
    tokens.append(Token("eof", "", line))
    return tokens


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        body = value[1:-1]
        return body.replace('\\"', '"').replace("\\\\", "\\")
    return value


# ─── Parser ──────────────────────────────────────────────────────


class _Parser:
    def __init__(self, tokens: List[Token]):
        self.toks = tokens
        self.i = 0

    def peek(self, offset: int = 0) -> Token:
        idx = min(self.i + offset, len(self.toks) - 1)
        return self.toks[idx]

    def next(self) -> Token:
        tok = self.toks[self.i]
        if self.i < len(self.toks) - 1:
            self.i += 1
        return tok

    def expect(self, kind: str) -> Token:
        tok = self.peek()
        if tok.kind != kind:
            raise FlowSyntaxError(
                f"line {tok.line}: expected {kind}, got {tok.kind} {tok.value!r}"
            )
        return self.next()

    def parse(self) -> FlowGraph:
        # optional 'strict'
        if self.peek().kind == "ident" and self.peek().value == "strict":
            self.next()
        head = self.peek()
        if head.kind != "ident" or head.value not in ("digraph", "graph"):
            raise FlowSyntaxError(
                f"line {head.line}: expected 'digraph', got {head.value!r}"
            )
        self.next()
        name = "flow"
        if self.peek().kind in ("ident", "string"):
            name = _unquote(self.next().value)
        self.expect("lbrace")

        nodes: Dict[str, FlowNode] = {}
        edges: List[FlowEdge] = []
        graph_attrs: Dict[str, str] = {}

        while self.peek().kind != "rbrace":
            if self.peek().kind == "eof":
                raise FlowSyntaxError(
                    f"line {self.peek().line}: unexpected end of input (missing '}}')"
                )
            self._statement(nodes, edges, graph_attrs)
            # tolerate optional separators
            while self.peek().kind in ("semi", "comma"):
                self.next()

        self.expect("rbrace")
        return FlowGraph(name=name, nodes=nodes, edges=edges, graph_attrs=graph_attrs)

    def _statement(self, nodes, edges, graph_attrs) -> None:
        tok = self.peek()
        if tok.kind != "ident":
            raise FlowSyntaxError(
                f"line {tok.line}: expected a node/edge statement, got "
                f"{tok.kind} {tok.value!r}"
            )

        # 'graph'/'node'/'edge' default-attr blocks
        if tok.value == "graph" and self.peek(1).kind == "lbrack":
            self.next()
            graph_attrs.update(self._attr_list())
            return
        if tok.value in ("node", "edge") and self.peek(1).kind == "lbrack":
            self.next()
            self._attr_list()  # default attrs: parsed and ignored in v1
            return

        first = self.next().value  # node id

        if self.peek().kind == "arrow":
            # edge chain: A -> B [-> C ...] [attrs]
            chain = [first]
            while self.peek().kind == "arrow":
                self.next()
                tgt = self.peek()
                if tgt.kind not in ("ident", "string"):
                    raise FlowSyntaxError(
                        f"line {tgt.line}: expected node id after '->', got {tgt.value!r}"
                    )
                chain.append(_unquote(self.next().value))
            attrs = self._attr_list() if self.peek().kind == "lbrack" else {}
            line = tok.line
            for a, b in zip(chain, chain[1:]):
                edges.append(
                    FlowEdge(
                        source=a,
                        target=b,
                        condition=attrs.get("condition") or attrs.get("when"),
                        label=attrs.get("label", ""),
                        loop_restart=_truthy(attrs.get("loop_restart")),
                        line=line,
                    )
                )
            return

        # node statement (optionally with attrs)
        attrs = self._attr_list() if self.peek().kind == "lbrack" else {}
        node_id = first
        kind = normalize_kind(attrs.get("type", ""), attrs.get("shape", ""))
        if node_id in nodes:
            # merge attrs on redeclaration (last wins)
            nodes[node_id].attrs.update(attrs)
            return
        nodes[node_id] = FlowNode(id=node_id, kind=kind, attrs=attrs, line=tok.line)

    def _attr_list(self) -> Dict[str, str]:
        self.expect("lbrack")
        attrs: Dict[str, str] = {}
        while self.peek().kind != "rbrack":
            if self.peek().kind == "eof":
                raise FlowSyntaxError(
                    f"line {self.peek().line}: unexpected end of input in attribute "
                    "list (missing ']')"
                )
            key_tok = self.peek()
            if key_tok.kind not in ("ident", "string"):
                raise FlowSyntaxError(
                    f"line {key_tok.line}: expected attribute name, got {key_tok.value!r}"
                )
            key = _unquote(self.next().value)
            self.expect("eq")
            val_tok = self.peek()
            if val_tok.kind not in ("ident", "string", "number"):
                raise FlowSyntaxError(
                    f"line {val_tok.line}: expected value for {key!r}, got {val_tok.value!r}"
                )
            attrs[key] = _unquote(self.next().value)
            while self.peek().kind in ("comma", "semi"):
                self.next()
        self.expect("rbrack")
        return attrs


# ─── Public entry points ─────────────────────────────────────────


def parse_dot(src: str, base_dir: Optional[str] = None) -> FlowGraph:
    """Parse DOT text into a FlowGraph. Resolves `@file` prompts against base_dir."""
    graph = _Parser(tokenize(src)).parse()
    _resolve_prompt_files(graph, base_dir)
    return graph


def load_flow_graph(path: str) -> FlowGraph:
    """Read and parse a .dot workflow file from disk."""
    p = Path(path).expanduser()
    if not p.exists():
        raise FlowConfigError(f"workflow file not found: {p}")
    return parse_dot(p.read_text(encoding="utf-8"), base_dir=str(p.parent))


def _resolve_prompt_files(graph: FlowGraph, base_dir: Optional[str]) -> None:
    """Expand `prompt="@relative/file.md"` to the file's contents."""
    if base_dir is None:
        return
    base = Path(base_dir)
    for node in graph.nodes.values():
        prompt = node.attrs.get("prompt", "")
        if prompt.startswith("@"):
            target = base / prompt[1:].strip()
            if not target.exists():
                raise FlowConfigError(
                    f"node {node.id!r}: prompt file not found: {target}"
                )
            node.attrs["prompt"] = target.read_text(encoding="utf-8")
