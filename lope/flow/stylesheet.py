"""CLI-routing stylesheet — lope's answer to fabro's `model_stylesheet`.

fabro routes a node's CSS class to a raw model name. lope routes to a
CLI/validator role instead (it picks CLIs, not bare models). Grammar is the
same CSS-like shape:

    cli_stylesheet="
      *          { primary: opencode; }
      .frontier  { primary: claude; }
      #Reviewers { validators: claude,codex,gemini; }
    "

Recognized properties: primary, validators (comma list), model,
reasoning_effort, timeout. Cascade lowest→highest:
  global config  <  `*`  <  `.class`  <  `#id`  <  inline node attrs
mirroring config.load_layered's field-by-field precedence. Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import FlowNode

_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_PROP_KEYS = ("primary", "validators", "model", "reasoning_effort", "timeout")


@dataclass
class StyleRule:
    selector: str  # "*" | ".class" | "#NodeId"
    props: Dict[str, str] = field(default_factory=dict)


@dataclass
class Stylesheet:
    rules: List[StyleRule] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> "Stylesheet":
        rules: List[StyleRule] = []
        if not text or not text.strip():
            return cls(rules)
        for m in _RULE_RE.finditer(text):
            selector = m.group(1).strip()
            body = m.group(2)
            props: Dict[str, str] = {}
            for decl in body.split(";"):
                decl = decl.strip()
                if not decl or ":" not in decl:
                    continue
                key, _, value = decl.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key in _PROP_KEYS and value:
                    props[key] = value
            if selector and props:
                rules.append(StyleRule(selector=selector, props=props))
        return cls(rules)

    def resolve(self, node: FlowNode) -> Dict[str, str]:
        """Compute the effective style for a node by cascading the rules,
        then letting inline node attributes win."""
        out: Dict[str, str] = {}
        # 1. universal
        for r in self.rules:
            if r.selector == "*":
                out.update(r.props)
        # 2. class
        cls_name = node.node_class
        if cls_name:
            wanted = {f".{c.strip()}" for c in cls_name.split() if c.strip()}
            for r in self.rules:
                if r.selector in wanted:
                    out.update(r.props)
        # 3. id
        for r in self.rules:
            if r.selector == f"#{node.id}":
                out.update(r.props)
        # 4. inline node attrs win
        for k in _PROP_KEYS:
            if k in node.attrs:
                out[k] = node.attrs[k]
        return out

    def unknown_validator_warnings(self, known: List[str]) -> List[str]:
        """Surface stylesheet `validators:`/`primary:` names not in `known`."""
        warnings: List[str] = []
        known_set = set(known)
        for r in self.rules:
            for key in ("primary", "validators"):
                raw = r.props.get(key)
                if not raw:
                    continue
                for name in [n.strip() for n in raw.split(",") if n.strip()]:
                    if name not in known_set:
                        warnings.append(
                            f"stylesheet {r.selector} {key}: {name!r} is not a known validator"
                        )
        return warnings


def parse_stylesheet(text: str) -> Optional[Stylesheet]:
    sheet = Stylesheet.parse(text)
    return sheet if sheet.rules else None


def split_names(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [n.strip() for n in raw.split(",") if n.strip()]
