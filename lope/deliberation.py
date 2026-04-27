"""Council-style deliberation for Lope (v0.7 phase 7).

``lope deliberate`` is a structured adversarial-reasoning verb. It is **not**
code execution: nothing in this module modifies source files, touches git, or
runs commands beyond ``Validator.generate``. The goal is to take a scenario
(an ADR question, a PRD draft, a build-vs-buy debate) and produce a synthesis
artifact that has survived an anonymized peer-critique round and a rubric
review by the council itself.

Protocol (per the v0.7 sprint contract):

1. **Scenario intake** — the input file or literal prompt is captured.
2. **Independent positions** — each validator writes its own draft without
   seeing peers.
3. **Anonymized critique** — each validator critiques the other validators'
   positions, with names stripped to ``Response A/B/C`` so identity bias
   cannot leak. Skipped in ``--depth quick``.
4. **Revision** — each validator revises or defends its own position based on
   the (still anonymized) critiques it received. Skipped in ``--depth quick``.
5. **Synthesis** — the primary writes the final artifact using the template's
   rubric.
6. **Rubric review** — each validator scores the synthesis with PASS or
   NEEDS_FIX and lists any objections.
7. **Minority report** — major NEEDS_FIX objections are preserved verbatim,
   anonymized by default.

Stdlib only. The orchestrator accepts an injected ``generate`` callable so
tests never run a real CLI; production callers wire it up from a Lope pool.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .redaction import redact_text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEPTHS = ("quick", "standard", "deep")
DEFAULT_DEPTH = "standard"

HUMAN_QUESTION_MODES = ("never", "blocking", "always")
DEFAULT_HUMAN_QUESTIONS = "never"

MINORITY_HIGH_SEVERITY = {"high", "critical", "blocker"}


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


GenerateFn = Callable[[str, str, int], str]
"""``(validator_name, prompt, timeout_seconds) -> text`` — injected by callers."""


@dataclass
class TemplateSpec:
    """Rubric + output contract for one deliberation kind.

    Each spec carries the prompt fragments for every protocol stage plus
    the canonical section list the synthesis must produce. Templates are
    plain Python data so callers can monkey-patch in tests without
    parsing markdown frontmatter.
    """

    name: str
    title: str
    sections: Tuple[str, ...]
    rubric: Tuple[str, ...]
    position_intro: str
    critique_intro: str
    revision_intro: str
    synthesis_intro: str
    rubric_intro: str

    def required_section_block(self) -> str:
        return "\n".join(f"- {section}" for section in self.sections)

    def rubric_block(self) -> str:
        return "\n".join(f"- {item}" for item in self.rubric)


@dataclass
class CouncilTurn:
    """One stage's output from one validator (or the primary at synthesis)."""

    stage: str  # position | critique | revision | synthesis | rubric
    validator: str
    text: str  # redaction-clean
    label: Optional[str] = None  # anonymized label assigned during the run
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RubricVerdict:
    """One validator's rubric judgement on the synthesis."""

    validator: str
    label: Optional[str]
    status: str  # PASS | NEEDS_FIX
    severity: str  # low | medium | high
    text: str  # redaction-clean
    objections: List[str] = field(default_factory=list)


@dataclass
class DeliberationRun:
    """Final state of a deliberation."""

    template: TemplateSpec
    scenario: str
    validators: List[str]
    primary: str
    depth: str
    anonymous: bool
    turns: List[CouncilTurn]
    synthesis: str
    rubric: List[RubricVerdict]
    minority_report: str
    decision_log: str
    output_dir: Optional[Path] = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template": self.template.name,
            "scenario_preview": self.scenario[:240],
            "validators": list(self.validators),
            "primary": self.primary,
            "depth": self.depth,
            "anonymous": self.anonymous,
            "synthesis_present": bool(self.synthesis.strip()),
            "rubric": [
                {
                    "validator": r.validator,
                    "label": r.label,
                    "status": r.status,
                    "severity": r.severity,
                    "objections": list(r.objections),
                }
                for r in self.rubric
            ],
            "minority_report_present": bool(self.minority_report.strip()),
            "turns": [
                {
                    "stage": t.stage,
                    "validator": t.validator,
                    "label": t.label,
                    "text_preview": t.text[:240],
                }
                for t in self.turns
            ],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output_dir": str(self.output_dir) if self.output_dir else None,
        }


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------


_TEMPLATES: Dict[str, TemplateSpec] = {
    "adr": TemplateSpec(
        name="adr",
        title="Architecture Decision Record",
        sections=(
            "Context",
            "Decision",
            "Consequences",
            "Alternatives Considered",
        ),
        rubric=(
            "Decision is stated explicitly with one-line rationale.",
            "At least two alternatives are documented with their trade-offs.",
            "Consequences include both positive and negative outcomes.",
            "Assumptions are explicit, not implied.",
        ),
        position_intro=(
            "You are drafting an Architecture Decision Record (ADR). "
            "Read the scenario and write your independent position. Cover: "
            "the underlying problem, the decision you recommend, the "
            "alternatives you considered, and the trade-offs of each."
        ),
        critique_intro=(
            "You are critiquing peer ADR drafts. Identify gaps, hidden "
            "assumptions, missing alternatives, and unstated consequences. "
            "Be direct. Cite specific lines or claims."
        ),
        revision_intro=(
            "You are revising your ADR position based on the critiques "
            "below. Fold in valid corrections, defend positions you still "
            "stand by, and explicitly note any objections you reject."
        ),
        synthesis_intro=(
            "You are synthesizing the council's final ADR using the "
            "rubric below. Produce a single coherent record with the "
            "exact section headings listed."
        ),
        rubric_intro=(
            "Score the synthesized ADR against the rubric. Reply with one "
            "line of the form ``VERDICT: PASS`` or ``VERDICT: NEEDS_FIX``, "
            "followed by a one-line ``SEVERITY: low|medium|high``, then "
            "bullet objections (one per line, prefix ``- ``)."
        ),
    ),
    "prd": TemplateSpec(
        name="prd",
        title="Product Requirements Document",
        sections=(
            "Problem",
            "Users and Use Cases",
            "Goals and Non-Goals",
            "Solution Sketch",
            "Acceptance Criteria",
            "Risks",
        ),
        rubric=(
            "Problem is grounded in a specific user pain, not a feature wish.",
            "Users / use cases are concrete and bounded.",
            "Non-goals are explicit and ruthless.",
            "Acceptance criteria are testable.",
            "At least one major risk is identified.",
        ),
        position_intro=(
            "You are drafting a Product Requirements Document. Read the "
            "scenario and write your independent take. Be opinionated about "
            "what we should build, who it's for, and what it explicitly "
            "won't do."
        ),
        critique_intro=(
            "You are critiquing peer PRD drafts. Look for vague problems, "
            "feature creep, untestable acceptance criteria, and missing "
            "non-goals. Quote the specific text you take issue with."
        ),
        revision_intro=(
            "Revise your PRD based on the critiques. Tighten scope, sharpen "
            "the problem statement, and answer concrete objections."
        ),
        synthesis_intro=(
            "Synthesize the council's PRD using the rubric below. Use the "
            "exact section headings listed. Be terse — a PRD is a contract, "
            "not a story."
        ),
        rubric_intro=(
            "Score the synthesized PRD against the rubric. Reply with "
            "``VERDICT: PASS`` or ``VERDICT: NEEDS_FIX``, then "
            "``SEVERITY: low|medium|high``, then bullet objections."
        ),
    ),
    "rfc": TemplateSpec(
        name="rfc",
        title="Request for Comments",
        sections=(
            "Summary",
            "Motivation",
            "Detailed Design",
            "Drawbacks",
            "Rationale and Alternatives",
            "Open Questions",
        ),
        rubric=(
            "Motivation explains why the change is worth doing now.",
            "Detailed design is concrete enough to implement from.",
            "Drawbacks are honest, not perfunctory.",
            "Open Questions section is non-empty.",
        ),
        position_intro=(
            "You are writing your independent position for an RFC. Outline "
            "the change you propose, the reasoning, and any open questions "
            "you cannot yet answer."
        ),
        critique_intro=(
            "Critique peer RFC drafts. Hunt for hand-waved details, "
            "missing alternatives, and incomplete drawbacks. Be specific."
        ),
        revision_intro=(
            "Revise your RFC position based on the critiques."
        ),
        synthesis_intro=(
            "Synthesize the council's final RFC using the rubric and "
            "section list below."
        ),
        rubric_intro=(
            "Score the synthesized RFC. ``VERDICT:`` then ``SEVERITY:`` "
            "then bullet objections."
        ),
    ),
    "build-vs-buy": TemplateSpec(
        name="build-vs-buy",
        title="Build vs Buy Analysis",
        sections=(
            "Decision Statement",
            "Requirements",
            "Build Option",
            "Buy Options",
            "Total Cost of Ownership",
            "Recommendation",
        ),
        rubric=(
            "At least one build option and at least two buy options are evaluated.",
            "TCO covers a 3-year horizon, not a launch-day estimate.",
            "Recommendation is unambiguous.",
            "Lock-in / exit risk is acknowledged for buy options.",
        ),
        position_intro=(
            "You are writing your independent recommendation on whether "
            "to build or buy this capability. Compare options on a "
            "3-year horizon. Be opinionated."
        ),
        critique_intro=(
            "Critique peer build-vs-buy analyses. Press for missing TCO "
            "components, ignored vendors, and weakly-defended assumptions."
        ),
        revision_intro=(
            "Revise your build-vs-buy position based on the critiques."
        ),
        synthesis_intro=(
            "Synthesize the council's final build-vs-buy decision using "
            "the rubric and sections below. State the recommendation in "
            "the first sentence."
        ),
        rubric_intro=(
            "Score the synthesized build-vs-buy artifact. Reply with "
            "VERDICT, SEVERITY, and bullet objections."
        ),
    ),
    "migration-plan": TemplateSpec(
        name="migration-plan",
        title="Migration Plan",
        sections=(
            "Migration Goal",
            "Source and Target State",
            "Phases and Sequence",
            "Rollback Strategy",
            "Validation Gates",
            "Risks",
        ),
        rubric=(
            "Phases are sequenced and each one is independently shippable or revertible.",
            "Rollback strategy is explicit, not implied.",
            "Validation gates are observable, not subjective.",
            "At least one risk is severe enough to require mitigation.",
        ),
        position_intro=(
            "You are writing your independent migration plan. Outline the "
            "source and target state, the phase sequence, the rollback "
            "strategy at each phase, and the gates that prove each phase "
            "succeeded."
        ),
        critique_intro=(
            "Critique peer migration plans. Find phase ordering errors, "
            "missing rollback paths, and validation gates that are not "
            "actually observable."
        ),
        revision_intro=(
            "Revise your migration plan based on the critiques."
        ),
        synthesis_intro=(
            "Synthesize the council's final migration plan using the "
            "rubric and sections below."
        ),
        rubric_intro=(
            "Score the synthesized migration plan. Reply with VERDICT, "
            "SEVERITY, and bullet objections."
        ),
    ),
    "incident-review": TemplateSpec(
        name="incident-review",
        title="Incident Review",
        sections=(
            "Incident Summary",
            "Timeline",
            "Root Cause",
            "Contributing Factors",
            "Action Items",
            "Lessons Learned",
        ),
        rubric=(
            "Root cause is a specific mechanism, not a category.",
            "Action items are owned and have a deadline.",
            "Lessons learned are non-blamey and structural.",
            "Timeline is in UTC and includes detection + mitigation times.",
        ),
        position_intro=(
            "You are writing your independent incident review. Reconstruct "
            "the timeline, identify the root cause, list contributing "
            "factors, and propose owned action items."
        ),
        critique_intro=(
            "Critique peer incident reviews. Push back on category-level "
            "root causes, owner-less action items, and blamey framing."
        ),
        revision_intro=(
            "Revise your incident review based on the critiques."
        ),
        synthesis_intro=(
            "Synthesize the council's final incident review using the "
            "rubric and sections below."
        ),
        rubric_intro=(
            "Score the synthesized incident review. Reply with VERDICT, "
            "SEVERITY, and bullet objections."
        ),
    ),
}


def list_templates() -> List[str]:
    return sorted(_TEMPLATES.keys())


def get_template(name: str) -> TemplateSpec:
    key = (name or "").strip().lower()
    if key not in _TEMPLATES:
        raise KeyError(
            f"Unknown deliberation template: {name!r}. "
            f"Available: {', '.join(list_templates())}"
        )
    return _TEMPLATES[key]


# ---------------------------------------------------------------------------
# Anonymization helpers
# ---------------------------------------------------------------------------


def _anon_label(index: int) -> str:
    if index < 26:
        return f"Response {chr(ord('A') + index)}"
    first = (index // 26) - 1
    second = index % 26
    return f"Response {chr(ord('A') + first)}{chr(ord('A') + second)}"


def _build_label_map(validators: Sequence[str]) -> Dict[str, str]:
    return {name: _anon_label(i) for i, name in enumerate(validators)}


def _label_for(
    name: str,
    label_map: Dict[str, str],
    *,
    anonymous: bool,
) -> str:
    if anonymous:
        return label_map.get(name, "Response ?")
    return name


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


SCENARIO_BLOCK_HEADER = "<<< Scenario >>>"
SCENARIO_BLOCK_FOOTER = "<<< End scenario >>>"


def _wrap_scenario(scenario: str) -> str:
    body = redact_text(scenario or "").strip() or "(empty scenario)"
    return f"{SCENARIO_BLOCK_HEADER}\n{body}\n{SCENARIO_BLOCK_FOOTER}\n"


def _peer_block(
    turns: Sequence[CouncilTurn],
    label_map: Dict[str, str],
    *,
    anonymous: bool,
    exclude: Optional[str] = None,
) -> str:
    chunks: List[str] = []
    for turn in turns:
        if exclude and turn.validator == exclude:
            continue
        label = _label_for(turn.validator, label_map, anonymous=anonymous)
        chunks.append(f"[{label}]\n{turn.text.rstrip()}\n")
    if not chunks:
        return "(no peer outputs available)"
    return "\n".join(chunks).rstrip()


def build_position_prompt(template: TemplateSpec, scenario: str) -> str:
    return (
        f"{template.position_intro}\n\n"
        f"{_wrap_scenario(scenario)}\n"
        "Required sections in your final ADR / PRD / RFC are listed below; "
        "your initial position can be looser, but cover them at least in "
        "outline:\n"
        f"{template.required_section_block()}\n\n"
        "Be opinionated. Cite assumptions explicitly. Mark anything you are "
        "uncertain about with `Inference:` so the council can challenge it."
    )


def build_critique_prompt(
    template: TemplateSpec,
    scenario: str,
    peer_block: str,
) -> str:
    return (
        f"{template.critique_intro}\n\n"
        f"{_wrap_scenario(scenario)}\n"
        "<<< Peer positions (anonymized) >>>\n"
        f"{peer_block}\n"
        "<<< End peer positions >>>\n\n"
        "Critique each peer position by its label. Be specific: quote the "
        "claim and explain why it is weak, missing, or wrong. Do not "
        "speculate about which model wrote which response — labels are "
        "stripped on purpose."
    )


def build_revision_prompt(
    template: TemplateSpec,
    scenario: str,
    own_position: str,
    peer_critiques: str,
) -> str:
    return (
        f"{template.revision_intro}\n\n"
        f"{_wrap_scenario(scenario)}\n"
        "<<< Your prior position >>>\n"
        f"{own_position.rstrip()}\n"
        "<<< End your prior position >>>\n\n"
        "<<< Critiques you received (anonymized) >>>\n"
        f"{peer_critiques}\n"
        "<<< End critiques >>>\n\n"
        "Produce a revised version of your position. Where you agree, "
        "incorporate the critique. Where you disagree, defend your stance "
        "with one explicit sentence."
    )


def build_synthesis_prompt(
    template: TemplateSpec,
    scenario: str,
    revisions_block: str,
) -> str:
    return (
        f"{template.synthesis_intro}\n\n"
        f"{_wrap_scenario(scenario)}\n"
        "<<< Council revised positions (anonymized) >>>\n"
        f"{revisions_block}\n"
        "<<< End council revised positions >>>\n\n"
        "Required section headings (use them verbatim, in this order):\n"
        f"{template.required_section_block()}\n\n"
        "Rubric you will be scored against:\n"
        f"{template.rubric_block()}\n\n"
        "Produce the final document. Do not invent positions that no "
        "council member raised; if you must extrapolate, prefix with "
        "`Inference:`."
    )


def build_rubric_prompt(template: TemplateSpec, synthesis_text: str) -> str:
    return (
        f"{template.rubric_intro}\n\n"
        "<<< Synthesis to score >>>\n"
        f"{synthesis_text.rstrip()}\n"
        "<<< End synthesis >>>\n\n"
        "Rubric:\n"
        f"{template.rubric_block()}\n\n"
        "Reply format (exact):\n"
        "VERDICT: PASS|NEEDS_FIX\n"
        "SEVERITY: low|medium|high\n"
        "- objection 1\n"
        "- objection 2\n"
        "Use no other prose."
    )


# ---------------------------------------------------------------------------
# Rubric parsing
# ---------------------------------------------------------------------------


_VERDICT_RE = re.compile(r"^\s*VERDICT\s*:\s*(?P<v>PASS|NEEDS_FIX)\b", re.IGNORECASE | re.MULTILINE)
_SEVERITY_RE = re.compile(r"^\s*SEVERITY\s*:\s*(?P<s>low|medium|high|critical|blocker)\b", re.IGNORECASE | re.MULTILINE)
_OBJECTION_RE = re.compile(r"^\s*-\s+(?P<o>.+)$", re.MULTILINE)


def parse_rubric_response(text: str) -> Tuple[str, str, List[str]]:
    """Parse a validator's rubric reply into ``(status, severity, objections)``.

    Tolerant of casing and extra prose. When VERDICT is missing the result
    defaults to NEEDS_FIX so unparseable replies do not silently pass.
    """

    redacted = redact_text(text or "")
    verdict_match = _VERDICT_RE.search(redacted)
    severity_match = _SEVERITY_RE.search(redacted)
    objections = [m.group("o").strip() for m in _OBJECTION_RE.finditer(redacted) if m.group("o").strip()]

    if verdict_match:
        status = verdict_match.group("v").upper()
        if status == "NEEDS_FIX" or status == "NEEDS-FIX":
            status = "NEEDS_FIX"
    else:
        status = "NEEDS_FIX"

    if severity_match:
        severity = severity_match.group("s").lower()
        if severity in ("critical", "blocker"):
            severity = "high"
    else:
        severity = "medium" if status == "NEEDS_FIX" else "low"

    return status, severity, objections


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_deliberation(
    *,
    template: TemplateSpec,
    scenario: str,
    validators: Sequence[str],
    primary: Optional[str] = None,
    generate: GenerateFn,
    depth: str = DEFAULT_DEPTH,
    timeout: int = 240,
    anonymous: bool = True,
    output_dir: Optional[Path] = None,
) -> DeliberationRun:
    """Run the full council protocol and return a :class:`DeliberationRun`.

    ``generate`` is the only side-effect-bearing dependency. Tests inject
    deterministic stubs; production callers wrap the validator pool. This
    function never modifies source files or git state — at most it writes
    the structured output directory when ``output_dir`` is supplied.
    """

    if depth not in DEPTHS:
        raise ValueError(f"depth must be one of {DEPTHS}, got {depth!r}")
    if not validators:
        raise ValueError("run_deliberation requires at least one validator")

    started_at = _dt.datetime.utcnow().isoformat(timespec="seconds")
    primary_name = primary or validators[0]
    if primary_name not in validators:
        raise ValueError(
            f"primary {primary_name!r} is not in validators {list(validators)!r}"
        )

    label_map = _build_label_map(validators)
    turns: List[CouncilTurn] = []

    # --- Phase 2: independent positions ---------------------------------
    position_prompt = build_position_prompt(template, scenario)
    positions: List[CouncilTurn] = []
    for name in validators:
        text = redact_text(generate(name, position_prompt, timeout)).strip()
        positions.append(
            CouncilTurn(
                stage="position",
                validator=name,
                label=label_map[name],
                text=text,
            )
        )
    turns.extend(positions)

    critiques: List[CouncilTurn] = []
    revisions: List[CouncilTurn] = list(positions)

    if depth in ("standard", "deep"):
        # --- Phase 3: anonymized critique ------------------------------
        for name in validators:
            peer_block = _peer_block(
                positions, label_map, anonymous=anonymous, exclude=name
            )
            prompt = build_critique_prompt(template, scenario, peer_block)
            text = redact_text(generate(name, prompt, timeout)).strip()
            critiques.append(
                CouncilTurn(
                    stage="critique",
                    validator=name,
                    label=label_map[name],
                    text=text,
                )
            )
        turns.extend(critiques)

        # --- Phase 4: revisions ----------------------------------------
        revisions = []
        for name in validators:
            own_position = next(p.text for p in positions if p.validator == name)
            received_critiques = _peer_block(
                [c for c in critiques if c.validator != name],
                label_map,
                anonymous=anonymous,
            )
            prompt = build_revision_prompt(
                template,
                scenario,
                own_position,
                received_critiques,
            )
            text = redact_text(generate(name, prompt, timeout)).strip()
            revisions.append(
                CouncilTurn(
                    stage="revision",
                    validator=name,
                    label=label_map[name],
                    text=text,
                )
            )
        turns.extend(revisions)

    # --- Phase 5: synthesis (primary only) ------------------------------
    revisions_block = _peer_block(revisions, label_map, anonymous=anonymous)
    synthesis_prompt = build_synthesis_prompt(template, scenario, revisions_block)
    synthesis_text = redact_text(generate(primary_name, synthesis_prompt, timeout)).strip()
    turns.append(
        CouncilTurn(
            stage="synthesis",
            validator=primary_name,
            label=label_map[primary_name],
            text=synthesis_text,
        )
    )

    # --- Phase 6: rubric review ----------------------------------------
    rubric_results: List[RubricVerdict] = []
    rubric_prompt = build_rubric_prompt(template, synthesis_text)
    for name in validators:
        text = redact_text(generate(name, rubric_prompt, timeout)).strip()
        status, severity, objections = parse_rubric_response(text)
        rubric_results.append(
            RubricVerdict(
                validator=name,
                label=label_map[name],
                status=status,
                severity=severity,
                text=text,
                objections=objections,
            )
        )
        turns.append(
            CouncilTurn(
                stage="rubric",
                validator=name,
                label=label_map[name],
                text=text,
                metadata={"status": status, "severity": severity},
            )
        )

    # --- Phase 7: minority report --------------------------------------
    minority_report = _build_minority_report(
        rubric_results, anonymous=anonymous, depth=depth
    )

    decision_log = _build_decision_log(
        template=template,
        scenario=scenario,
        validators=list(validators),
        primary=primary_name,
        depth=depth,
        anonymous=anonymous,
        rubric=rubric_results,
        started_at=started_at,
    )

    finished_at = _dt.datetime.utcnow().isoformat(timespec="seconds")

    run = DeliberationRun(
        template=template,
        scenario=scenario,
        validators=list(validators),
        primary=primary_name,
        depth=depth,
        anonymous=anonymous,
        turns=turns,
        synthesis=synthesis_text,
        rubric=rubric_results,
        minority_report=minority_report,
        decision_log=decision_log,
        output_dir=output_dir,
        started_at=started_at,
        finished_at=finished_at,
    )

    if output_dir is not None:
        write_run(run, output_dir)
        run.output_dir = output_dir

    return run


# ---------------------------------------------------------------------------
# Minority report + decision log
# ---------------------------------------------------------------------------


def _build_minority_report(
    rubric: Sequence[RubricVerdict],
    *,
    anonymous: bool,
    depth: str,
) -> str:
    objectors = [r for r in rubric if r.status == "NEEDS_FIX"]
    if not objectors:
        return (
            "# Minority Report\n\n"
            "No council member dissented; the synthesis passed the rubric "
            "unanimously.\n"
        )

    if depth == "deep":
        keep = objectors
    else:
        keep = [r for r in objectors if r.severity in MINORITY_HIGH_SEVERITY]
        if not keep:
            keep = objectors[:1]  # always preserve at least one dissent

    lines = ["# Minority Report", ""]
    lines.append(
        f"Council reported {len(objectors)} dissenting verdict(s); "
        f"{len(keep)} preserved here."
    )
    lines.append("")
    for entry in keep:
        who = entry.label if anonymous else entry.validator
        lines.append(f"## {who} — {entry.severity.upper()}")
        if entry.objections:
            for obj in entry.objections:
                lines.append(f"- {redact_text(obj)}")
        else:
            lines.append("(no specific objections supplied)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_decision_log(
    *,
    template: TemplateSpec,
    scenario: str,
    validators: List[str],
    primary: str,
    depth: str,
    anonymous: bool,
    rubric: Sequence[RubricVerdict],
    started_at: str,
) -> str:
    pass_count = sum(1 for r in rubric if r.status == "PASS")
    fix_count = sum(1 for r in rubric if r.status == "NEEDS_FIX")
    return (
        f"# Decision Log\n\n"
        f"- Template: {template.name} ({template.title})\n"
        f"- Started: {started_at}\n"
        f"- Council size: {len(validators)}\n"
        f"- Primary: {primary if not anonymous else '(anonymous)'}\n"
        f"- Depth: {depth}\n"
        f"- Anonymous critique: {anonymous}\n"
        f"- Rubric verdicts: {pass_count} PASS, {fix_count} NEEDS_FIX\n"
        f"- Scenario length: {len(scenario)} characters\n"
    )


# ---------------------------------------------------------------------------
# Output directory writer
# ---------------------------------------------------------------------------


def write_run(run: DeliberationRun, output_dir: Path) -> Path:
    """Persist a :class:`DeliberationRun` to ``output_dir`` per the spec layout.

    All file content is redaction-clean (turns and reports are produced
    that way). The directory is created if missing.
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "scenario.md").write_text(redact_text(run.scenario), encoding="utf-8")

    turns_dir = output_dir / "turns"
    stage_dirs = {
        "position": turns_dir / "01-positions",
        "critique": turns_dir / "02-critiques",
        "revision": turns_dir / "03-revisions",
    }
    for d in stage_dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    label_to_slug: Dict[str, str] = {}

    def _slug(label_or_name: str) -> str:
        existing = label_to_slug.get(label_or_name)
        if existing:
            return existing
        cleaned = re.sub(r"[^A-Za-z0-9]+", "-", (label_or_name or "")).strip("-")
        if not cleaned:
            cleaned = f"validator-{len(label_to_slug)+1}"
        # ensure uniqueness if multiple labels collide after slugify
        suffix = ""
        n = 1
        while (cleaned + suffix) in label_to_slug.values():
            n += 1
            suffix = f"-{n}"
        full = cleaned + suffix
        label_to_slug[label_or_name] = full
        return full

    name_for_filenames = (
        (lambda turn: turn.label or turn.validator)
        if run.anonymous
        else (lambda turn: turn.validator)
    )

    for turn in run.turns:
        if turn.stage not in stage_dirs:
            continue
        slug = _slug(name_for_filenames(turn))
        target = stage_dirs[turn.stage] / f"{slug}.md"
        target.write_text(redact_text(turn.text) + "\n", encoding="utf-8")

    final_dir = output_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / "report.md").write_text(redact_text(run.synthesis) + "\n", encoding="utf-8")
    (final_dir / "minority-report.md").write_text(run.minority_report, encoding="utf-8")
    (final_dir / "decision-log.md").write_text(run.decision_log, encoding="utf-8")

    trace_path = output_dir / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as fh:
        for turn in run.turns:
            payload = {
                "stage": turn.stage,
                "validator": turn.validator if not run.anonymous else "(anonymous)",
                "label": turn.label,
                "text": redact_text(turn.text),
                "metadata": {
                    str(k): redact_text(str(v)) for k, v in turn.metadata.items()
                },
            }
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    return output_dir


# ---------------------------------------------------------------------------
# Default output dir helper
# ---------------------------------------------------------------------------


def default_output_dir(template: TemplateSpec, *, root: Optional[Path] = None) -> Path:
    """Build ``lope-runs/<timestamp>-<template>/`` under ``root`` (or cwd)."""

    base = Path(root) if root is not None else Path.cwd() / "lope-runs"
    stamp = _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return base / f"{stamp}-{template.name}"


__all__ = [
    "DEFAULT_DEPTH",
    "DEFAULT_HUMAN_QUESTIONS",
    "DEPTHS",
    "HUMAN_QUESTION_MODES",
    "MINORITY_HIGH_SEVERITY",
    "CouncilTurn",
    "DeliberationRun",
    "GenerateFn",
    "RubricVerdict",
    "TemplateSpec",
    "build_critique_prompt",
    "build_position_prompt",
    "build_revision_prompt",
    "build_rubric_prompt",
    "build_synthesis_prompt",
    "default_output_dir",
    "get_template",
    "list_templates",
    "parse_rubric_response",
    "run_deliberation",
    "write_run",
]
