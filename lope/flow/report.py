"""FlowReport — the result of a flow run, plus a bridge to lope's Auditor.

A flow is a graph, not a phase list, so ExecutionReport doesn't fit. FlowReport
records the path taken and per-node outcomes. `flow_report_to_execution_report`
synthesizes a SprintDoc/Phase shape so `Auditor.scorecard` / `write_journal`
(auditor.py) work unchanged — flow runs land in the same `[[lope]]` journal.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .model import NodeResult


@dataclass
class FlowReport:
    """Per-node outcomes + the route taken through the graph."""

    graph_name: str
    node_results: List[NodeResult] = field(default_factory=list)
    ok: bool = True
    escalation: Optional[object] = None  # EscalationRequired on guard breach
    total_duration_seconds: float = 0.0
    blackboard_snapshot: dict = field(default_factory=dict)

    @property
    def path(self) -> List[str]:
        return [r.node_id for r in self.node_results]

    def add(self, result: NodeResult) -> None:
        self.node_results.append(result)

    def count(self, outcome: str) -> int:
        return sum(1 for r in self.node_results if r.outcome == outcome)

    def scorecard(self) -> str:
        lines = [f"Flow: {self.graph_name}"]
        lines.append(f"Steps: {len(self.node_results)}  ·  path: {' -> '.join(self.path)}")
        lines.append(f"Total duration: {self.total_duration_seconds:.1f}s")
        lines.append("---")
        for r in self.node_results:
            tag = f"{r.node_id}: {r.outcome}"
            if r.label:
                tag += f" ({r.label})"
            tag += f" {r.duration_seconds:.0f}s"
            lines.append(tag)
            if r.error:
                lines.append(f"  error: {r.error[:160]}")
        lines.append("---")
        lines.append("Overall: " + ("OK" if self.ok else "ESCALATED"))
        if self.escalation is not None:
            lines.append(f"Escalation: {self.escalation}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "graph_name": self.graph_name,
            "ok": self.ok,
            "path": self.path,
            "total_duration_seconds": self.total_duration_seconds,
            "escalation": str(self.escalation) if self.escalation else "",
            "node_results": [
                {
                    "node_id": r.node_id,
                    "outcome": r.outcome,
                    "label": r.label,
                    "detail": r.detail[:500],
                    "duration_seconds": r.duration_seconds,
                    "error": r.error[:300],
                }
                for r in self.node_results
            ],
        }


def flow_report_to_execution_report(fr: FlowReport):
    """Adapt a FlowReport into an ExecutionReport so the existing Auditor can
    score it and write the journal. Each node becomes a Phase; its outcome maps
    to a PhaseVerdict (reusing a node's real verdict when it has one)."""
    from ..models import (
        ExecutionReport,
        Phase,
        PhaseVerdict,
        SprintDoc,
        VerdictStatus,
    )

    _OUTCOME_STATUS = {
        "succeeded": VerdictStatus.PASS,
        "started": VerdictStatus.PASS,
        "exited": VerdictStatus.PASS,
        "passed": VerdictStatus.PASS,
        "needs_fix": VerdictStatus.NEEDS_FIX,
        "failed": VerdictStatus.FAIL,
        "infra_error": VerdictStatus.INFRA_ERROR,
    }

    phases: List[Phase] = []
    verdicts: List[PhaseVerdict] = []
    for i, r in enumerate(fr.node_results, start=1):
        if r.verdict is not None:
            v = r.verdict
        else:
            v = PhaseVerdict(
                status=_OUTCOME_STATUS.get(r.outcome, VerdictStatus.PASS),
                confidence=0.0,
                rationale=r.detail[:200],
                duration_seconds=r.duration_seconds,
                validator_name=r.label,
            )
        phase = Phase(index=i, name=r.node_id, goal=r.outcome, verdict=v)
        phases.append(phase)
        verdicts.append(v)

    doc = SprintDoc(
        slug=fr.graph_name,
        title=f"FLOW-{fr.graph_name}",
        origin="lope flow run",
        phases=phases,
    )
    return ExecutionReport(
        sprint_doc=doc,
        phase_verdicts=verdicts,
        ok=fr.ok,
        error=str(fr.escalation) if fr.escalation else "",
        total_duration_seconds=fr.total_duration_seconds,
    )


def write_flow_run(fr: FlowReport, out_dir: str) -> Path:
    """Persist a flow run as trace.jsonl + report.md under out_dir. Redacts."""
    from ..redaction import redact_text

    base = Path(out_dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)

    trace = base / "trace.jsonl"
    with trace.open("w", encoding="utf-8") as f:
        for r in fr.node_results:
            line = {
                "node_id": r.node_id,
                "outcome": r.outcome,
                "label": r.label,
                "detail": redact_text(r.detail[:1000]),
                "duration_seconds": round(r.duration_seconds, 2),
                "error": redact_text(r.error[:500]),
            }
            f.write(json.dumps(line) + "\n")

    report_md = base / "report.md"
    report_md.write_text(
        f"# Flow run: {fr.graph_name}\n\n```\n{fr.scorecard()}\n```\n",
        encoding="utf-8",
    )
    return base


def default_flow_out_dir(graph_name: str, stamp: Optional[int] = None) -> str:
    """A `lope-runs/<ts>-flow-<name>/` directory shape (caller passes the stamp
    to keep this import-time deterministic)."""
    ts = stamp if stamp is not None else int(time.time())
    return str(Path("lope-runs") / f"{ts}-flow-{graph_name}")
