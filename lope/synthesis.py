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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

DEFAULT_SOURCE_BYTE_LIMIT = 64 * 1024
HARD_SOURCE_BYTE_LIMIT = 256 * 1024
DEFAULT_TOTAL_PROMPT_BYTE_LIMIT = 96 * 1024
HARD_TOTAL_PROMPT_BYTE_LIMIT = 1024 * 1024
_TRUNCATION_MARKER = "\n...[source truncated; head and tail retained]...\n"


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
    truncations: List[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.truncations is None:
            self.truncations = []


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


def _truncate_utf8(text: str, limit: int) -> Tuple[str, int]:
    raw = (text or "").encode("utf-8")
    if len(raw) <= limit:
        return text, 0
    marker = _TRUNCATION_MARKER.encode("utf-8")[:limit]
    remaining = max(0, limit - len(marker))
    head_size = remaining // 2
    tail_size = remaining - head_size
    head = raw[:head_size]
    tail = raw[-tail_size:] if tail_size else b""
    while head:
        try:
            head_text = head.decode("utf-8")
            break
        except UnicodeDecodeError:
            head = head[:-1]
    else:
        head_text = ""
    while tail:
        try:
            tail_text = tail.decode("utf-8")
            break
        except UnicodeDecodeError:
            tail = tail[1:]
    else:
        tail_text = ""
    rendered = head_text + marker.decode("utf-8", errors="ignore") + tail_text
    retained = len(rendered.encode("utf-8"))
    return rendered, max(0, len(raw) - retained)


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
    source_byte_limit: int = DEFAULT_SOURCE_BYTE_LIMIT,
    total_byte_limit: int = DEFAULT_TOTAL_PROMPT_BYTE_LIMIT,
    truncation_log: Optional[List[Dict[str, Any]]] = None,
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

    source_byte_limit = max(1024, min(int(source_byte_limit), HARD_SOURCE_BYTE_LIMIT))
    total_byte_limit = max(16 * 1024, min(int(total_byte_limit), HARD_TOTAL_PROMPT_BYTE_LIMIT))
    truncations = truncation_log if truncation_log is not None else []
    task_text = redact_text(task or "").strip() or "(no task description provided)"
    task_text, task_omitted = _truncate_utf8(task_text, 32 * 1024)
    if task_omitted:
        truncations.append({
            "source": "task",
            "omitted_bytes": task_omitted,
            "retained_bytes": len(task_text.encode("utf-8")),
        })
    parts = [
        "You are synthesizing N independent AI critiques of the same task.",
        "",
        f"Task: {task_text}",
        "",
    ]

    successes = []
    for name, answer, error in responses:
        if error or not (answer or "").strip():
            continue
        safe_name = redact_text(name or "").strip()
        safe_answer = redact_text(answer or "").strip()
        safe_answer, omitted = _truncate_utf8(safe_answer, source_byte_limit)
        if omitted:
            truncations.append({
                "source": safe_name or "unknown",
                "omitted_bytes": omitted,
                "retained_bytes": len(safe_answer.encode("utf-8")),
            })
        successes.append((safe_name, safe_answer))
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
            findings_budget = max(8 * 1024, total_byte_limit // 2)
            used = 0
            for index, f in enumerate(structured_findings):
                line = redact_text(_format_finding_line(f, name_mapper=label_of))
                line, omitted = _truncate_utf8(line, min(8 * 1024, findings_budget))
                encoded = len((line + "\n").encode("utf-8"))
                if used + encoded > findings_budget:
                    remaining = max(0, findings_budget - used)
                    if remaining >= 1024:
                        line, extra_omitted = _truncate_utf8(line, remaining)
                        parts.append(line)
                        omitted += extra_omitted
                    truncations.append({
                        "source": f"structured_findings[{index}]",
                        "omitted_bytes": omitted + sum(
                            len(_format_finding_line(rest).encode("utf-8"))
                            for rest in structured_findings[index + 1:]
                        ),
                        "retained_bytes": max(0, remaining),
                    })
                    break
                parts.append(line)
                used += encoded
                if omitted:
                    truncations.append({
                        "source": f"structured_findings[{index}]",
                        "omitted_bytes": omitted,
                        "retained_bytes": len(line.encode("utf-8")),
                    })
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

    prompt = "\n".join(parts).rstrip() + "\n"
    if len(prompt.encode("utf-8")) > total_byte_limit:
        prompt, omitted = _truncate_utf8(prompt, total_byte_limit)
        truncations.append({
            "source": "aggregate_synthesis_prompt",
            "omitted_bytes": omitted,
            "retained_bytes": len(prompt.encode("utf-8")),
        })
    return prompt


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_synthesis(
    primary: Any,
    prompt: str,
    timeout: int,
    *,
    truncations: Optional[Sequence[Dict[str, Any]]] = None,
    context=None,
) -> SynthesisResult:
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
            truncations=list(truncations or []),
        )
    try:
        from .invocation import invoke_generate

        text = invoke_generate(
            primary,
            prompt,
            timeout,
            context=context,
            stage="synthesis",
        )
    except Exception as exc:
        return SynthesisResult(
            ok=False,
            primary=name,
            error=redact_text(f"{type(exc).__name__}: {exc}").strip(),
            truncations=list(truncations or []),
        )

    redacted = redact_text(text or "").strip()
    if not redacted:
        return SynthesisResult(
            ok=False,
            primary=name,
            error="Primary returned empty synthesis output.",
            truncations=list(truncations or []),
        )

    return SynthesisResult(
        ok=True,
        primary=name,
        text=redacted,
        truncations=list(truncations or []),
    )


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
    truncation_note = (
        f"[bounded input: {len(result.truncations)} source truncation(s)]\n"
        if result.truncations else ""
    )
    return f"{header}\n{truncation_note}{result.text.rstrip()}\n"


__all__ = [
    "FanoutResult",
    "OPTIONAL_SECTIONS",
    "REQUIRED_SECTIONS",
    "SynthesisResult",
    "DEFAULT_SOURCE_BYTE_LIMIT",
    "DEFAULT_TOTAL_PROMPT_BYTE_LIMIT",
    "build_synthesis_prompt",
    "format_synthesis",
    "run_synthesis",
]
