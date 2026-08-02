"""Persist what actually happened on every validator call.

Lope's most valuable signal is the stuff it currently throws away. When a
validator's response cannot be parsed, the run records ``INFRA_ERROR`` and
moves on -- the prompt that was sent, the text that came back, and the reason
parsing failed all vanish. That makes two things impossible: telling a broken
tool apart from a badly-formatted reply, and ever assembling a corpus of real
validator behaviour.

This module records one row per validator call into ``verdict_events``
(schema version 1, see :mod:`lope.memory`). It is deliberately best-effort:
recording is observability, and a failure to write an audit row must never
break the review it is observing.

Privacy: prompts and responses run through :func:`lope.redaction.redact_text`
before they touch the database, and the database is local-only
(``~/.lope/memory.db``) and never committed.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from .models import ValidatorResult, VerdictStatus
from .redaction import redact_text
from .verdict_repair import ParseErrorCategory, RepairStatus

log = logging.getLogger(__name__)

#: Bumped whenever the meaning of a recorded column changes, so stored rows
#: stay interpretable after the parser evolves.
PARSER_VERSION = "parse_verdict_block/1"
PROMPT_TEMPLATE_VERSION = "executor/_build_validation_prompt/1"
EVENT_SCHEMA_VERSION = "1.0.0"

ENV_DISABLE_EVENTS = "LOPE_VERDICT_EVENTS"


def events_enabled(env: Optional[dict] = None) -> bool:
    """Whether to persist verdict events. **Opt-in**, via ``LOPE_VERDICT_EVENTS=on``.

    Off by default on purpose. A row holds an entire rendered prompt and an
    entire model response, kept indefinitely. Redaction is a filter for known
    secret shapes, not a guarantee, and "the file is local" is not a privacy
    boundary -- it is still durable storage of whatever happened to be in a
    reviewed diff. Capturing that has to be a decision someone makes, not a
    side effect of upgrading.
    """
    source = os.environ if env is None else env
    return str(source.get(ENV_DISABLE_EVENTS, "")).strip().lower() in {
        "1",
        "on",
        "true",
        "yes",
    }


def events_disabled(env: Optional[dict] = None) -> bool:
    """Backwards-compatible inverse of :func:`events_enabled`."""
    return not events_enabled(env)


def task_spec_hash(text: str) -> str:
    """Stable identifier for the task a verdict was rendered against."""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def classify_parse_error(result: ValidatorResult) -> Optional[ParseErrorCategory]:
    """Work out *why* a validator call failed to yield a usable verdict.

    Returning ``None`` means the call succeeded and nothing needs explaining.
    The distinction that matters is between a tool that did not run
    (transport/timeout/process/output-limit) and a tool that ran fine but
    formatted its answer badly (``missing-verdict-block``) -- only the latter
    is a formatting fault worth repairing.
    """
    if result.verdict.status is not VerdictStatus.INFRA_ERROR:
        return None

    haystack = f"{result.error} {result.verdict.rationale}".lower()

    if "timed out" in haystack or "timeout" in haystack:
        return ParseErrorCategory.TIMEOUT
    if "output limit" in haystack or "output_limit" in haystack:
        return ParseErrorCategory.OUTPUT_LIMIT
    if "no verdict" in haystack or "unparseable" in haystack:
        return ParseErrorCategory.MISSING_VERDICT_BLOCK
    if "unknown verdict token" in haystack:
        return ParseErrorCategory.UNKNOWN_ENUM
    if result.error:
        # A non-empty error with no more specific signal means the subprocess
        # itself failed rather than the text being malformed.
        return ParseErrorCategory.PROCESS_EXIT
    if not (result.raw_response or "").strip():
        return ParseErrorCategory.TRANSPORT
    return ParseErrorCategory.MISSING_VERDICT_BLOCK


def record_verdict_event(
    result: ValidatorResult,
    *,
    prompt: str,
    run_id: str,
    gate_id: str = "",
    validator_version: str = "",
    validation_input_ref: str = "",
    candidate_output_ref: str = "",
    latency_s: Optional[float] = None,
    exit_status: Optional[int] = None,
    repair_attempted: bool = False,
    repair_status: Optional[RepairStatus] = None,
    repaired_response: str = "",
    initial_status: Optional[VerdictStatus] = None,
    memory=None,
) -> bool:
    """Write one audit row. Returns True if it landed.

    Never raises: observability must not be able to fail a review.
    """
    if not events_enabled():
        return False

    try:
        from .memory import LopeMemory, is_memory_disabled

        if is_memory_disabled():
            return False

        # Prefer the category recorded at parse time: a repaired result now
        # carries a real verdict, so re-classifying it would report no failure
        # and lose the reason this row was written.
        recorded = getattr(result, "parse_error_category", None)
        category_value = recorded or (
            c.value if (c := classify_parse_error(result)) else None
        )
        initial = initial_status or result.verdict.status

        redacted_prompt = redact_text(prompt)
        row = (
            EVENT_SCHEMA_VERSION,
            run_id,
            gate_id or None,
            result.validator_name,
            validator_version or None,
            PROMPT_TEMPLATE_VERSION,
            redacted_prompt,
            # Hash the redacted text: hashing the raw prompt would leave a
            # stable equality oracle over content we deliberately removed.
            task_spec_hash(redacted_prompt),
            validation_input_ref or None,
            redact_text(result.raw_response),
            PARSER_VERSION,
            initial.value,
            category_value,
            1 if repair_attempted else 0,
            repair_status.value if repair_status else None,
            redact_text(repaired_response) if repaired_response else None,
            result.verdict.status.value,
            candidate_output_ref or None,
            latency_s if latency_s is not None else result.verdict.duration_seconds,
            exit_status,
            1 if category_value == ParseErrorCategory.TIMEOUT.value else 0,
            datetime.now(timezone.utc).isoformat(),
        )

        mem = memory if memory is not None else LopeMemory()
        with mem._connect() as conn:
            conn.execute(
                """
                INSERT INTO verdict_events (
                    schema_version, run_id, gate_id, validator_name,
                    validator_version, prompt_template_version, rendered_prompt,
                    task_spec_hash, validation_input_ref, raw_response,
                    parser_version, initial_parse_status, parse_error_category,
                    repair_attempted, repair_status, repaired_response,
                    final_verdict, candidate_output_ref, latency_s, exit_status,
                    timed_out, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                row,
            )
            conn.commit()
        return True
    except Exception as exc:  # pragma: no cover - defensive by design
        log.debug("verdict event not recorded: %s", exc)
        return False
