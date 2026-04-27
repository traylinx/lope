"""Synthesis pass for Lope's single-shot verbs.

When the user passes ``--synth`` to ``ask`` / ``review`` / ``pipe`` / ``vote``
/ ``compare``, Lope hands the fan-out responses (or, for
``review --consensus --synth``, the deduplicated consensus findings) to a
single primary validator and asks for an executive summary in a fixed
section layout. The point is to turn N raw model opinions into one
durable, action-shaped artifact.

This module owns three things:

* :func:`build_synthesis_prompt` assembles the synthesis prompt from
  responses and optional structured findings. Optional anonymous mode
  strips validator names so the synthesizer cannot bias on identity.
* :func:`run_synthesis` executes the synthesis call with fail-soft
  semantics — infrastructure errors are captured on the result object
  rather than propagated. Callers print the original fan-out output
  whenever ``result.ok`` is false.
* :func:`format_synthesis` renders the synthesis block for human stdout
  or returns the redacted body for embedding inside a JSON envelope.

Stdlib only; redaction is applied at every boundary that touches
validator output before it leaves the module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from .findings import ConsensusFinding
from .redaction import redact_text


FanoutResult = Tuple[str, str, Optional[str]]


REQUIRED_SECTIONS: Tuple[str, ...] = (
    "## Consensus",
    "## Disagreements",
    "## Highest-risk item",
    "## Recommended action",
)

OPTIONAL_SECTIONS: Tuple[str, ...] = (
    "## Follow-up questions",
)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class SynthesisResult:
    """Outcome of one synthesis attempt.

    ``ok`` is true when the primary returned non-empty text. ``text`` is
    redaction-clean. On failure, ``error`` carries a short, redacted
    description so callers can surface "synthesis failed" without leaking
    secrets from the primary's stderr.
    """

    ok: bool
    text: str = ""
    error: str = ""
    primary: str = ""


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _anon_label(index: int) -> str:
    """Stable ``Response A`` / ``Response B`` / ``Response AA`` label.

    Indices past 25 spill into double letters so the alphabet promise
    holds for any roster size — ``chr(ord('A') + 30)`` would otherwise
    emit punctuation. We never realistically run more than ~10
    validators per fan-out, but the safety net is cheap.
    """

    if index < 26:
        return f"Response {chr(ord('A') + index)}"
    first = (index // 26) - 1
    second = index % 26
    return f"Response {chr(ord('A') + first)}{chr(ord('A') + second)}"


def _format_finding_line(f: ConsensusFinding, name_mapper=None) -> str:
    location = ""
    if f.file:
        location = f.file
        if f.line is not None:
            location += f":{f.line}"
    mapper = name_mapper or (lambda x: x)
    detected = ", ".join(mapper(d) for d in f.detected_by) or "—"
    return (
        f"- [{f.consensus_level.value.upper()}] [{f.severity.upper()}] "
        f"{location} — {f.message} "
        f"(consensus {f.consensus_score:.2f}, agreement {f.agreement_count}/{f.total_validators}, "
        f"detected_by: {detected})"
    )


def _build_anonymizer(
    responses: Sequence[FanoutResult],
    structured_findings: Optional[Sequence[ConsensusFinding]],
):
    """Return a callable mapping validator names to ``Response A/B/C`` labels.

    Names appearing first in ``responses`` come first (successes and
    errors interleaved in their original order); any remaining names
    discovered inside ``structured_findings.detected_by`` are appended in
    encounter order so the same validator gets the same label across
    every prompt surface (provenance line, finding ``detected_by``,
    error block).
    """

    ordered: List[str] = []
    for name, _answer, _error in responses:
        if name and name not in ordered:
            ordered.append(name)
    if structured_findings:
        for finding in structured_findings:
            for detector in finding.detected_by:
                if detector and detector not in ordered:
                    ordered.append(detector)

    label_for = {name: _anon_label(index) for index, name in enumerate(ordered)}

    def _map(name: str) -> str:
        return label_for.get(name, "Response ?")

    return _map


def build_synthesis_prompt(
    task: str,
    responses: Sequence[FanoutResult],
    *,
    structured_findings: Optional[Sequence[ConsensusFinding]] = None,
    anonymous: bool = False,
) -> str:
    """Build the synthesis prompt the primary validator will execute.

    ``responses`` is the raw fan-out tuple list ``(name, answer, error)``.
    Successful answers are listed in the prompt; errors are listed in a
    separate "Validator errors" section so synthesis can mention them
    without inventing claims about what those validators thought.

    When ``structured_findings`` is provided (review --consensus --synth
    path), they replace the raw answer transcripts as the synthesis input
    so the primary works on deduped, scored findings rather than raw spam.
    Errors are still listed; raw answers are summarized with names + a
    one-line ack so the synthesizer has provenance.
    """

    task_text = redact_text(task or "").strip() or "(no task description provided)"
    parts = [
        "You are synthesizing N independent AI critiques of the same task.",
        "",
        f"Task: {task_text}",
        "",
    ]

    successes = [
        (redact_text(name or "").strip(), redact_text(answer or "").strip())
        for name, answer, error in responses
        if not error and (answer or "").strip()
    ]
    errors = [
        (redact_text(name or "").strip(), redact_text(error or "").strip())
        for name, _answer, error in responses
        if error
    ]

    # Single source of truth for every label the prompt will print. In
    # anonymous mode the mapper rewrites validator names to a stable
    # ``Response A/B/C`` ordering across responses, errors, and finding
    # ``detected_by`` lists. In named mode the mapper is a no-op.
    if anonymous:
        anonymizer = _build_anonymizer(responses, structured_findings)
        label_of = lambda name: anonymizer(name)  # noqa: E731
    else:
        label_of = lambda name: name or "unknown"  # noqa: E731

    if structured_findings is not None:
        parts.append(
            "Consensus findings (already deduped + ranked across "
            f"{len(responses)} validators):"
        )
        if not structured_findings:
            parts.append("- (no findings parsed by the consensus pipeline)")
        else:
            for f in structured_findings:
                parts.append(
                    redact_text(_format_finding_line(f, name_mapper=label_of))
                )
        parts.append("")
        if successes:
            label_kind = "validators" if not anonymous else "responses"
            names = ", ".join(label_of(name) for name, _ in successes)
            parts.append(f"Source {label_kind}: {names}")
            parts.append("")
    else:
        parts.append("Validator responses:")
        parts.append("")
        if not successes:
            parts.append("(no validator returned a non-empty response)")
            parts.append("")
        for name, answer in successes:
            parts.append(f"[{label_of(name)}]")
            parts.append(answer)
            parts.append("")

    if errors:
        parts.append("Validator errors (these validators did NOT contribute opinions):")
        for name, error in errors:
            parts.append(f"- {label_of(name)}: {error or '(empty error)'}")
        parts.append("")

    parts.append(
        "Produce a synthesis using these EXACT section headings, in this order:"
    )
    parts.extend(REQUIRED_SECTIONS)
    for optional in OPTIONAL_SECTIONS:
        parts.append(f"{optional}   (only if blocking decisions need clarification)")
    parts.extend(
        [
            "",
            "Rules:",
            "- Never invent a finding not present in the responses or consensus "
            "findings above. If you must extrapolate, prefix the bullet with "
            "`Inference:`.",
            "- Be concise. Each section is at most 5 short bullets.",
            "- Do not repeat the original responses verbatim.",
            "- Identify the single highest-risk item and explain why in one "
            "sentence.",
            "- Make ONE Recommended action — the next concrete step.",
            "- If validators errored, acknowledge the gap; do not fabricate "
            "their position.",
        ]
    )

    if anonymous:
        parts.append(
            "- Refer to sources only by Response A/B/C labels. Do not infer or "
            "guess validator identity."
        )

    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_synthesis(primary: Any, prompt: str, timeout: int) -> SynthesisResult:
    """Run the synthesis call against ``primary`` with fail-soft semantics.

    ``primary`` must expose ``.generate(prompt, timeout)`` returning text.
    Any exception (subprocess failure, timeout, missing CLI) is caught and
    returned on the result object instead of propagating, so calling
    commands can still print the original fan-out and exit cleanly.
    """

    name = getattr(primary, "name", "") or primary.__class__.__name__
    if primary is None:
        return SynthesisResult(
            ok=False,
            primary="",
            error="No primary validator available for synthesis.",
        )
    try:
        text = primary.generate(prompt, timeout)
    except Exception as exc:
        return SynthesisResult(
            ok=False,
            primary=name,
            error=redact_text(f"{type(exc).__name__}: {exc}").strip(),
        )

    redacted = redact_text(text or "").strip()
    if not redacted:
        return SynthesisResult(
            ok=False,
            primary=name,
            error="Primary returned empty synthesis output.",
        )

    return SynthesisResult(ok=True, primary=name, text=redacted)


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


def format_synthesis(
    result: SynthesisResult,
    *,
    machine_json: bool = False,
) -> str:
    """Render a :class:`SynthesisResult` for human stdout or JSON embedding.

    In ``machine_json`` mode the redacted body is returned verbatim so the
    caller can drop it into a larger JSON envelope without re-decorating.
    """

    if machine_json:
        return result.text

    if not result.ok:
        return (
            f"━━━ synthesis ━━━\n"
            f"[synthesis unavailable: {result.error or 'unknown error'}]\n"
        )

    header = f"━━━ synthesis ({result.primary or 'primary'}) ━━━"
    return f"{header}\n{result.text.rstrip()}\n"


__all__ = [
    "FanoutResult",
    "OPTIONAL_SECTIONS",
    "REQUIRED_SECTIONS",
    "SynthesisResult",
    "build_synthesis_prompt",
    "format_synthesis",
    "run_synthesis",
]
