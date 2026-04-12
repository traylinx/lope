"""
Lope dataclasses — the shared vocabulary for negotiator, executor, and auditor.

Kept deliberately minimal: no ORM, no external deps, no YAML. Just dataclasses
that serialize via dataclasses.asdict for logging and via explicit to_markdown
helpers for Brain-friendly output.

Invariants:
  - Round numbers are 1-indexed and monotonic (round 0 is invalid).
  - Verdict statuses live in a single enum — no magic strings elsewhere.
  - SprintDoc round-trips through markdown (from_markdown → to_markdown).
  - Phases are sorted by `index` which is 1-indexed and monotonic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# ─── Verdict primitives ──────────────────────────────────────────


class VerdictStatus(str, Enum):
    """The four terminal states an opencode/validator response can resolve to.

    Values match the exact uppercase tokens we parse out of VERDICT: blocks,
    so `VerdictStatus("PASS")` works as a single source of truth.
    """
    PASS = "PASS"
    NEEDS_FIX = "NEEDS_FIX"
    FAIL = "FAIL"
    INFRA_ERROR = "INFRA_ERROR"

    @property
    def is_terminal(self) -> bool:
        """PASS and FAIL halt the loop; NEEDS_FIX retries; INFRA_ERROR escalates."""
        return self in (VerdictStatus.PASS, VerdictStatus.FAIL)


@dataclass
class PhaseVerdict:
    """A validator's single read on one phase.

    `stage` distinguishes two-stage execute-mode passes:
      - None: legacy single-pass verdict (negotiate mode, v0.2.x behavior)
      - "spec": stage-1 spec-compliance verdict (does it match the phase goal?)
      - "quality": stage-2 code-quality verdict (is it well-built?)

    `evidence_gate_triggered` is True when a PASS was downgraded to NEEDS_FIX
    by the evidence heuristic (verification-before-completion gate, v0.3).
    """
    status: VerdictStatus
    confidence: float = 0.0
    rationale: str = ""
    required_fixes: List[str] = field(default_factory=list)
    nice_to_have: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    validator_name: str = ""
    stage: Optional[str] = None
    evidence_gate_triggered: bool = False

    def is_pass(self) -> bool:
        return self.status == VerdictStatus.PASS

    def needs_retry(self) -> bool:
        return self.status == VerdictStatus.NEEDS_FIX


@dataclass
class ValidatorResult:
    """Raw output from a Validator.validate() call + the parsed verdict."""
    validator_name: str
    verdict: PhaseVerdict
    raw_response: str = ""
    error: str = ""  # set when validator itself crashed (subprocess died, etc)

    def ok(self) -> bool:
        return not self.error and self.verdict.status != VerdictStatus.INFRA_ERROR


# ─── Phases + SprintDoc ──────────────────────────────────────────


@dataclass
class Phase:
    """A single phase inside a sprint doc."""
    index: int                                # 1-indexed, monotonic
    name: str                                 # short slug like "phase1-scaffold"
    goal: str                                 # human-readable sentence
    criteria: List[str] = field(default_factory=list)   # acceptance criteria
    artifacts: List[str] = field(default_factory=list)   # key artifacts (files, deliverables, docs)
    checks: List[str] = field(default_factory=list)      # validation checks (tests, metrics, reviews)
    verdict: Optional[PhaseVerdict] = None               # set after validation

    # Backward-compat aliases for code that uses the old field names
    @property
    def files(self) -> List[str]:
        return self.artifacts

    @property
    def tests(self) -> List[str]:
        return self.checks

    def is_validated(self) -> bool:
        return self.verdict is not None

    def is_pass(self) -> bool:
        return self.verdict is not None and self.verdict.is_pass()


DOMAINS = {
    "engineering": {
        "role": "senior staff engineer",
        "artifact_label": "Files",
        "check_label": "Tests",
        "review_task": "Read listed files. Check each criterion against real code. Find bugs, regressions, broken invariants.",
    },
    "business": {
        "role": "senior operations lead",
        "artifact_label": "Deliverables",
        "check_label": "Success Metrics",
        "review_task": "Review listed deliverables. Check each criterion against evidence. Find gaps in timeline, budget, targeting, or success metrics.",
    },
    "research": {
        "role": "principal researcher",
        "artifact_label": "Artifacts",
        "check_label": "Validation Criteria",
        "review_task": "Review methodology and data sources. Check each criterion against research standards. Find gaps in sampling, validity, ethics, or analysis plan.",
    },
}


@dataclass
class SprintDoc:
    """Top-level sprint document — negotiated output, executor input."""
    slug: str                                 # e.g. "auth-middleware"
    title: str                                # "SPRINT-AUTH-MIDDLEWARE"
    origin: str = ""                          # the user request that started it
    domain: str = "engineering"               # engineering | business | research
    phases: List[Phase] = field(default_factory=list)
    path: Optional[str] = None                # on-disk path when saved/loaded

    @property
    def domain_config(self) -> dict:
        return DOMAINS.get(self.domain, DOMAINS["engineering"])

    def get_phase(self, index: int) -> Optional[Phase]:
        for p in self.phases:
            if p.index == index:
                return p
        return None

    def to_markdown(self) -> str:
        """Render as markdown. Uses domain-appropriate labels for artifacts/checks."""
        dc = self.domain_config
        lines: List[str] = []
        lines.append(f"# {self.title}")
        lines.append("")
        if self.domain != "engineering":
            lines.append(f"**Domain:** {self.domain}")
            lines.append("")
        if self.origin:
            lines.append("## Origin")
            lines.append("")
            lines.append(self.origin)
            lines.append("")
        lines.append("## Phases")
        lines.append("")
        for phase in sorted(self.phases, key=lambda p: p.index):
            lines.append(f"### Phase {phase.index}: {phase.name}")
            lines.append("")
            lines.append(f"**Goal:** {phase.goal}")
            lines.append("")
            if phase.criteria:
                lines.append("**Criteria:**")
                for c in phase.criteria:
                    lines.append(f"- {c}")
                lines.append("")
            if phase.artifacts:
                lines.append(f"**{dc['artifact_label']}:**")
                for f in phase.artifacts:
                    lines.append(f"- {f}")
                lines.append("")
            if phase.checks:
                lines.append(f"**{dc['check_label']}:**")
                for t in phase.checks:
                    lines.append(f"- {t}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def from_markdown(cls, text: str, path: Optional[str] = None) -> "SprintDoc":
        """Parse a markdown sprint doc into a SprintDoc.

        Best-effort: looks for `# <title>`, `## Origin`, and `### Phase N: <name>`
        headings. Criteria / files / tests are parsed from their bulleted
        sections if present. Unknown sections are silently ignored (so
        round-tripping documents with extra sections is safe).
        """
        title = ""
        origin_lines: List[str] = []
        phases: List[Phase] = []

        current_phase: Optional[Phase] = None
        current_list_name: Optional[str] = None  # "criteria" | "files" | "tests"
        in_origin = False

        phase_re = re.compile(r"^###\s+Phase\s+(\d+)\s*:\s*(.+?)\s*$")

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if line.startswith("# ") and not title:
                title = line[2:].strip()
                continue
            if line.startswith("## Origin"):
                in_origin = True
                current_phase = None
                current_list_name = None
                continue
            m = phase_re.match(line)
            if m:
                in_origin = False
                current_list_name = None
                current_phase = Phase(
                    index=int(m.group(1)),
                    name=m.group(2).strip(),
                    goal="",
                )
                phases.append(current_phase)
                continue
            if line.startswith("## "):
                in_origin = False
                current_list_name = None
                continue
            if in_origin:
                if line:
                    origin_lines.append(line)
                continue
            if current_phase is None:
                continue

            if line.startswith("**Goal:**"):
                current_phase.goal = line[len("**Goal:**"):].strip()
                current_list_name = None
                continue
            if line.startswith("**Criteria:**"):
                current_list_name = "criteria"
                continue
            # Accept all artifact label variants
            if any(line.startswith(f"**{lbl}:**") for lbl in
                   ("Files", "Deliverables", "Artifacts")):
                current_list_name = "artifacts"
                continue
            # Accept all check label variants
            if any(line.startswith(f"**{lbl}:**") for lbl in
                   ("Tests", "Success Metrics", "Validation Criteria", "Checks")):
                current_list_name = "checks"
                continue
            if line.startswith("- ") and current_list_name is not None:
                item = line[2:].strip().strip("`")
                if current_list_name == "criteria":
                    current_phase.criteria.append(item)
                elif current_list_name == "artifacts":
                    current_phase.artifacts.append(item)
                elif current_list_name == "checks":
                    current_phase.checks.append(item)
                continue
            if not line:
                current_list_name = None

        # Detect domain from header
        domain = "engineering"
        for raw_line in text.splitlines():
            if raw_line.strip().startswith("**Domain:**"):
                domain = raw_line.split(":**", 1)[1].strip().lower()
                break

        slug = _slug_from_title(title)
        return cls(
            slug=slug,
            title=title,
            origin="\n".join(origin_lines).strip(),
            domain=domain,
            phases=sorted(phases, key=lambda p: p.index),
            path=path,
        )

    def save(self, path: str) -> str:
        """Write to-markdown() to disk, creating parent dirs if needed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_markdown())
        self.path = str(p)
        return self.path


def _slug_from_title(title: str) -> str:
    """Turn 'SPRINT-AUTH-MIDDLEWARE' → 'auth-middleware'."""
    if not title:
        return "untitled"
    t = title.strip().lower()
    if t.startswith("sprint-"):
        t = t[len("sprint-"):]
    t = re.sub(r"[^a-z0-9\-]+", "-", t).strip("-")
    return t or "untitled"


# ─── Negotiation ─────────────────────────────────────────────────


@dataclass
class Round:
    """One back-and-forth in the negotiation loop.

    number: 1-indexed (round 0 is invalid — the negotiator builds on round 1
    with the initial proposal). proposer identifies whose output this round
    captures: "drafter" for a proposal, "validator" for a critique/verdict.
    """
    number: int
    proposer: str              # "drafter" | "validator"
    text: str
    verdict: Optional[PhaseVerdict] = None

    def __post_init__(self):
        if self.number < 1:
            raise ValueError(f"Round.number must be >= 1, got {self.number}")
        if self.proposer not in ("drafter", "validator"):
            raise ValueError(
                f"Round.proposer must be 'drafter' or 'validator', got {self.proposer!r}"
            )


@dataclass
class Proposal:
    """A proposal emitted by the negotiator — one round of "here's my scope"."""
    round_number: int
    goal: str
    text: str                                 # the markdown proposal body
    prior_feedback: str = ""                  # what the validator said last round

    def __post_init__(self):
        if self.round_number < 1:
            raise ValueError(
                f"Proposal.round_number must be >= 1, got {self.round_number}"
            )


# ─── Execution ───────────────────────────────────────────────────


@dataclass
class ExecutionReport:
    """Output of PhaseExecutor.run — per-phase verdicts + overall ok/error."""
    sprint_doc: SprintDoc
    phase_verdicts: List[PhaseVerdict] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    total_duration_seconds: float = 0.0

    def scorecard(self) -> str:
        """One-line-per-phase summary for logs / journal."""
        lines = [f"Sprint: {self.sprint_doc.title}"]
        for phase in sorted(self.sprint_doc.phases, key=lambda p: p.index):
            v = phase.verdict
            if v is None:
                lines.append(f"  P{phase.index} {phase.name}: (not run)")
            else:
                lines.append(
                    f"  P{phase.index} {phase.name}: {v.status.value} "
                    f"conf={v.confidence:.2f} {v.duration_seconds:.0f}s"
                )
        status = "OK" if self.ok else "ESCALATED"
        lines.append(f"Overall: {status}")
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)

    def confidence_average(self) -> float:
        vs = [v.confidence for v in self.phase_verdicts if v.confidence > 0]
        return sum(vs) / len(vs) if vs else 0.0

    def count(self, status: VerdictStatus) -> int:
        return sum(1 for v in self.phase_verdicts if v.status == status)


@dataclass
class EscalationRequired(Exception):
    """Raised or returned when a phase cannot be closed by the autonomous loop.

    Carries enough context for the operator to act: which phase, which
    verdict status, what the validator said. Subclasses Exception so executors
    can opt to raise it up the call stack, but auditors prefer to receive it
    as a value in ExecutionReport.error.
    """
    phase_index: int
    phase_name: str
    reason: str                                 # "3 NEEDS_FIX exhausted" / "FAIL" / "INFRA_ERROR"
    last_verdict: Optional[PhaseVerdict] = None

    def __str__(self) -> str:
        status = self.last_verdict.status.value if self.last_verdict else "?"
        return (
            f"Phase {self.phase_index} ({self.phase_name}) escalated: "
            f"{self.reason} (last verdict: {status})"
        )


# ─── VERDICT block parser (shared) ───────────────────────────────


_VERDICT_RE = re.compile(
    r"^\s*VERDICT:\s*(?P<status>PASS|NEEDS_FIX|FAIL|INFRA_ERROR)\s*"
    r"(?:\(confidence=(?P<conf>[0-9.]+)\s*,\s*(?P<dur>[0-9.]+)s\))?\s*$",
    re.MULTILINE,
)
_RATIONALE_RE = re.compile(
    r"^\s*RATIONALE:\s*$\n(?P<body>(?:.*\n)*?)(?=^\s*(?:REQUIRED_FIXES|NICE_TO_HAVE|VERDICT|\Z))",
    re.MULTILINE,
)
_REQUIRED_FIXES_RE = re.compile(
    r"^\s*REQUIRED[_ ]FIXES:\s*$\n(?P<body>(?:.*\n)*?)(?=^\s*(?:NICE_TO_HAVE|VERDICT|\Z))",
    re.MULTILINE,
)


def parse_verdict_block(
    text: str,
    validator_name: str = "",
    fallback_duration: float = 0.0,
) -> PhaseVerdict:
    """Pure function: extract a PhaseVerdict from a validator's response.

    Used by every Validator implementation to stay consistent. If no VERDICT
    block is present, returns a PhaseVerdict with INFRA_ERROR + rationale
    pointing at the parsing failure — never raises.
    """
    m = _VERDICT_RE.search(text or "")
    if m is None:
        return PhaseVerdict(
            status=VerdictStatus.INFRA_ERROR,
            rationale="no VERDICT: block found in validator response",
            duration_seconds=fallback_duration,
            validator_name=validator_name,
        )
    try:
        status = VerdictStatus(m.group("status"))
    except ValueError:
        return PhaseVerdict(
            status=VerdictStatus.INFRA_ERROR,
            rationale=f"unknown verdict token: {m.group('status')!r}",
            duration_seconds=fallback_duration,
            validator_name=validator_name,
        )

    confidence = float(m.group("conf") or 0.0)
    duration = float(m.group("dur") or fallback_duration)

    rationale_match = _RATIONALE_RE.search(text)
    rationale = _strip_body(rationale_match.group("body")) if rationale_match else ""

    fixes_match = _REQUIRED_FIXES_RE.search(text)
    required_fixes: List[str] = []
    if fixes_match:
        for line in fixes_match.group("body").splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                required_fixes.append(stripped[2:].strip())
            elif stripped.startswith("* "):
                required_fixes.append(stripped[2:].strip())

    return PhaseVerdict(
        status=status,
        confidence=confidence,
        rationale=rationale,
        required_fixes=required_fixes,
        duration_seconds=duration,
        validator_name=validator_name,
    )


def _strip_body(body: str) -> str:
    """Trim whitespace from a parsed block body without losing internal newlines."""
    return "\n".join(line.rstrip() for line in body.splitlines()).strip()
