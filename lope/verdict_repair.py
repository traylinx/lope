"""Deterministic repair for validator responses that omit the VERDICT block.

A validator that reasons correctly but formats badly is currently scored as an
infrastructure failure: :func:`lope.models.parse_verdict_block` finds no
``VERDICT:`` line, returns ``INFRA_ERROR``, and the analysis is discarded. That
is a formatting fault being counted as a broken tool.

This module fixes exactly that fault and nothing else. The contract is
deliberately mechanical so its behaviour is testable rather than a judgement
call:

* **Only** the ``missing-verdict-block`` failure category is eligible. Transport
  errors, timeouts, non-zero exits and output-limit kills are real
  infrastructure failures and never trigger a repair.
* **Exactly one** retry, with an extraction-only prompt: the validator must
  re-state the verdict for analysis it already produced, never perform new
  analysis.
* The reply is accepted **only** if it is a well-formed verdict block and
  nothing else -- one ``VERDICT:`` line with an allowed status, optionally
  followed by ``RATIONALE:`` / ``REQUIRED_FIXES:`` / ``NICE_TO_HAVE:`` sections.
  Any extra prose, any second verdict, any unknown section means the repair is
  rejected and the original ``INFRA_ERROR`` stands.

Repaired verdicts are marked in ``verdict_events.repair_status`` so downstream
consumers can exclude them: a verdict that needed coaxing is weaker evidence
than one produced cleanly, and must never be treated as an adjudicated label.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .models import PhaseVerdict, VerdictStatus, parse_verdict_block

# Repair uses its own budget: it is a short, mechanical extraction, not a fresh
# review, and must always finish well inside the primary validator timeout.
ENV_REPAIR_TIMEOUT = "LOPE_REPAIR_TIMEOUT"
DEFAULT_REPAIR_TIMEOUT = 30.0

PARSER_VERSION = "verdict-repair/1"

#: Sections a repaired reply may contain besides the VERDICT line itself.
_ALLOWED_SECTIONS = ("RATIONALE", "REQUIRED_FIXES", "REQUIRED FIXES", "NICE_TO_HAVE")

_VERDICT_LINE_RE = re.compile(
    r"^\s*VERDICT:\s*(?P<status>[A-Z_]+)\s*"
    r"(?:\(confidence=(?P<conf>[0-9.]+)\s*,\s*(?P<dur>[0-9.]+)s\))?\s*$"
)
_SECTION_RE = re.compile(r"^\s*(?P<name>[A-Z_ ]+):\s*$")
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*$")


class ParseErrorCategory(str, Enum):
    """Why a validator response failed to yield a verdict.

    Only :attr:`MISSING_VERDICT_BLOCK` is repairable -- the rest describe a
    tool that did not run correctly, where retrying the *format* would be
    papering over a real failure.
    """

    MISSING_VERDICT_BLOCK = "missing-verdict-block"
    UNKNOWN_ENUM = "unknown-enum"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    PROCESS_EXIT = "process-exit"
    OUTPUT_LIMIT = "output-limit"


class RepairStatus(str, Enum):
    """Outcome of a repair attempt.

    Distinct failure modes stay distinct: collapsing "this CLI cannot draft"
    into "timed out" produces telemetry that reads like an infrastructure
    problem when nothing timed out.
    """

    ACCEPTED = "accepted"
    REJECTED_PROSE = "rejected-prose"
    REJECTED_INVALID = "rejected-invalid"
    REJECTED_TIMEOUT = "rejected-timeout"
    REJECTED_UNSUPPORTED = "rejected-unsupported"
    REJECTED_PROCESS = "rejected-process"
    REJECTED_NO_ORIGINAL = "rejected-no-original"


@dataclass(frozen=True)
class RepairOutcome:
    """Result of evaluating a repair reply. ``verdict`` is set iff accepted."""

    status: RepairStatus
    verdict: Optional[PhaseVerdict] = None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status is RepairStatus.ACCEPTED


def repair_timeout(env: Optional[dict] = None) -> float:
    """Repair budget in seconds, from ``LOPE_REPAIR_TIMEOUT``.

    Falls back to the default on anything unparseable or non-positive rather
    than raising -- a malformed env var must not take down a review run.
    """
    source = os.environ if env is None else env
    raw = source.get(ENV_REPAIR_TIMEOUT)
    if raw is None:
        return DEFAULT_REPAIR_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_REPAIR_TIMEOUT
    return value if value > 0 else DEFAULT_REPAIR_TIMEOUT


def is_repairable(category: Optional[ParseErrorCategory]) -> bool:
    """True only for a missing VERDICT block -- see module docstring."""
    return category is ParseErrorCategory.MISSING_VERDICT_BLOCK


def build_repair_prompt(original_response: str, validator_name: str = "") -> str:
    """Extraction-only prompt over *supplied* text.

    The original response must be embedded here. Validators are stateless
    subprocess invocations: a second call has no memory of the first, so
    "restate what you concluded" would be an instruction to invent one, and an
    invented ``PASS`` would halt the pool on a verdict nobody reached. Quoting
    the text back turns the task into extraction from evidence that is present
    in the prompt.
    """
    if not (original_response or "").strip():
        raise ValueError(
            "repair requires the original response; extraction without it "
            "would let the validator fabricate a verdict"
        )
    return (
        "Below is a code-review response you produced. It is missing the "
        "required VERDICT block.\n"
        "\n"
        "Your task is pure extraction. Read the response and express the "
        "conclusion it already contains. Do NOT perform new analysis, do NOT "
        "re-review anything, and do NOT reach a conclusion the text does not "
        "support. If the response does not clearly reach a conclusion, answer "
        "INCONCLUSIVE.\n"
        "\n"
        "--- BEGIN RESPONSE ---\n"
        f"{original_response.strip()}\n"
        "--- END RESPONSE ---\n"
        "\n"
        "Output exactly this format and nothing else:\n"
        "\n"
        "VERDICT: <PASS|NEEDS_FIX|FAIL|INCONCLUSIVE>\n"
        "RATIONALE:\n"
        "<one or two sentences drawn from the response above>\n"
        "REQUIRED_FIXES:\n"
        "- <one line per fix named in the response; omit this section if none>\n"
        "\n"
        "No preamble, no explanation, no code fences, no closing remarks."
    )


def evaluate_repair_reply(
    text: str,
    *,
    validator_name: str = "",
    fallback_duration: float = 0.0,
) -> RepairOutcome:
    """Decide whether a repair reply is a clean verdict block -- pure function.

    Mechanical by design: every rejection maps to a concrete structural fact
    about the text, so the contract can be exercised with a table of cases
    instead of relying on a model's judgement of "substantive content".
    """
    if text is None or not text.strip():
        return RepairOutcome(RepairStatus.REJECTED_INVALID, reason="empty reply")

    lines = _strip_optional_fence(text.strip().splitlines())

    verdict_lines = [i for i, line in enumerate(lines) if _VERDICT_LINE_RE.match(line)]
    if not verdict_lines:
        return RepairOutcome(
            RepairStatus.REJECTED_INVALID, reason="no VERDICT line in repair reply"
        )
    if len(verdict_lines) > 1:
        return RepairOutcome(
            RepairStatus.REJECTED_INVALID,
            reason=f"{len(verdict_lines)} VERDICT lines; expected exactly one",
        )

    verdict_index = verdict_lines[0]
    if verdict_index != 0:
        return RepairOutcome(
            RepairStatus.REJECTED_PROSE,
            reason="text precedes the VERDICT line",
        )

    status_token = _VERDICT_LINE_RE.match(lines[0]).group("status")
    try:
        VerdictStatus(status_token)
    except ValueError:
        return RepairOutcome(
            RepairStatus.REJECTED_INVALID,
            reason=f"unknown verdict token: {status_token!r}",
        )
    # A repair may not manufacture an infrastructure failure -- that is the very
    # state it exists to resolve, and accepting it would launder a formatting
    # fault into a tooling verdict.
    if status_token == VerdictStatus.INFRA_ERROR.value:
        return RepairOutcome(
            RepairStatus.REJECTED_INVALID,
            reason="repair may not return INFRA_ERROR",
        )

    violation = _find_prose_violation(lines[1:])
    if violation is not None:
        return RepairOutcome(RepairStatus.REJECTED_PROSE, reason=violation)

    # The shared section regexes consume body lines as ``(?:.*\n)*?``, so the
    # final line needs its terminator or the last REQUIRED_FIXES entry is
    # silently dropped.
    verdict = parse_verdict_block(
        "\n".join(lines) + "\n",
        validator_name=validator_name,
        fallback_duration=fallback_duration,
    )
    if verdict.status is VerdictStatus.INFRA_ERROR:
        # Defensive: the shared parser disagreed with our structural check.
        return RepairOutcome(
            RepairStatus.REJECTED_INVALID,
            reason=f"shared parser rejected the block: {verdict.rationale}",
        )

    return RepairOutcome(RepairStatus.ACCEPTED, verdict=verdict)


def _strip_optional_fence(lines: list) -> list:
    """Drop a single wrapping code fence -- common and harmless formatting."""
    if len(lines) >= 2 and _FENCE_RE.match(lines[0]) and _FENCE_RE.match(lines[-1]):
        return lines[1:-1]
    return lines


def _find_prose_violation(rest: list) -> Optional[str]:
    """Return a reason string if anything after the VERDICT line is not an
    allowed section header or that section's body; otherwise ``None``.

    Body content is only permitted while a recognised section is open, and a
    blank line closes the open section. That is what stops a validator from
    appending a friendly sign-off below the block -- the common failure mode.
    The repair prompt asks for one or two sentences and bullet lines, so no
    legitimate reply needs a blank line inside a section.
    """
    in_section = False
    for line in rest:
        stripped = line.strip()
        if not stripped:
            in_section = False
            continue
        header = _SECTION_RE.match(line)
        if header:
            name = header.group("name").strip()
            if name not in _ALLOWED_SECTIONS:
                return f"unexpected section: {name!r}"
            in_section = True
            continue
        if not in_section:
            return "prose after VERDICT line outside any allowed section"
    return None
