"""Tests for :mod:`lope.verdict_repair`.

The repair contract is deliberately mechanical, so it is exercised as a table
of structural cases rather than through model behaviour.
"""

from __future__ import annotations

import pytest

from lope.models import VerdictStatus
from lope.verdict_repair import (
    DEFAULT_REPAIR_TIMEOUT,
    ENV_REPAIR_TIMEOUT,
    ParseErrorCategory,
    RepairStatus,
    build_repair_prompt,
    evaluate_repair_reply,
    is_repairable,
    repair_timeout,
)


# ---------------------------------------------------------------------------
# Eligibility -- only a missing block is a formatting fault
# ---------------------------------------------------------------------------


def test_only_missing_verdict_block_is_repairable():
    assert is_repairable(ParseErrorCategory.MISSING_VERDICT_BLOCK) is True


@pytest.mark.parametrize(
    "category",
    [
        ParseErrorCategory.UNKNOWN_ENUM,
        ParseErrorCategory.TRANSPORT,
        ParseErrorCategory.TIMEOUT,
        ParseErrorCategory.PROCESS_EXIT,
        ParseErrorCategory.OUTPUT_LIMIT,
        None,
    ],
)
def test_real_infrastructure_failures_are_not_repairable(category):
    assert is_repairable(category) is False


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------


ACCEPTED_CASES = {
    "bare verdict": "VERDICT: PASS",
    "verdict with confidence": "VERDICT: PASS (confidence=0.9, 1.5s)",
    "verdict + rationale": "VERDICT: NEEDS_FIX\nRATIONALE:\nMissing error handling.\n",
    "verdict + fixes": (
        "VERDICT: NEEDS_FIX\n"
        "RATIONALE:\n"
        "Two problems remain.\n"
        "REQUIRED_FIXES:\n"
        "- add a timeout\n"
        "- handle the empty case\n"
    ),
    "spaced section name": (
        "VERDICT: NEEDS_FIX\nREQUIRED FIXES:\n- tighten the check\n"
    ),
    "nice to have": "VERDICT: PASS\nNICE_TO_HAVE:\n- rename the helper\n",
    "wrapped in a code fence": "```\nVERDICT: FAIL\nRATIONALE:\nUnsalvageable.\n```",
    "trailing blank lines": "VERDICT: PASS\n\n\n",
}


@pytest.mark.parametrize("label", sorted(ACCEPTED_CASES))
def test_clean_verdict_blocks_are_accepted(label):
    outcome = evaluate_repair_reply(ACCEPTED_CASES[label], validator_name="codex")
    assert outcome.accepted, f"{label}: {outcome.reason}"
    assert outcome.verdict is not None
    assert outcome.verdict.status is not VerdictStatus.INFRA_ERROR


def test_accepted_verdict_carries_status_and_fixes():
    outcome = evaluate_repair_reply(ACCEPTED_CASES["verdict + fixes"], validator_name="codex")
    assert outcome.verdict.status is VerdictStatus.NEEDS_FIX
    assert outcome.verdict.required_fixes == ["add a timeout", "handle the empty case"]
    assert outcome.verdict.validator_name == "codex"


# ---------------------------------------------------------------------------
# Rejection -- extra prose
# ---------------------------------------------------------------------------


PROSE_CASES = {
    "preamble": "Sure! Here is the verdict.\nVERDICT: PASS",
    "trailing commentary": "VERDICT: PASS\nHope that helps!",
    "commentary after section": (
        "VERDICT: PASS\nRATIONALE:\nLooks fine.\n\nLet me know if you want more detail."
    ),
    "unknown section": "VERDICT: PASS\nSUMMARY:\nAll good.",
}


@pytest.mark.parametrize("label", sorted(PROSE_CASES))
def test_extra_prose_is_rejected(label):
    outcome = evaluate_repair_reply(PROSE_CASES[label])
    assert not outcome.accepted
    assert outcome.status is RepairStatus.REJECTED_PROSE, f"{label}: {outcome.reason}"
    assert outcome.verdict is None


def test_trailing_commentary_after_section_is_rejected_not_silently_kept():
    outcome = evaluate_repair_reply(PROSE_CASES["commentary after section"])
    assert outcome.status is RepairStatus.REJECTED_PROSE


# ---------------------------------------------------------------------------
# Rejection -- structurally invalid
# ---------------------------------------------------------------------------


INVALID_CASES = {
    "empty": "",
    "whitespace only": "   \n\n  ",
    "no verdict line": "RATIONALE:\nI think it is fine.",
    "two verdicts": "VERDICT: PASS\nVERDICT: FAIL",
    "unknown token": "VERDICT: MAYBE",
    "lowercase token": "VERDICT: pass",
    "infra error laundering": "VERDICT: INFRA_ERROR\nRATIONALE:\nTool broke.",
}


@pytest.mark.parametrize("label", sorted(INVALID_CASES))
def test_structurally_invalid_replies_are_rejected(label):
    outcome = evaluate_repair_reply(INVALID_CASES[label])
    assert not outcome.accepted
    assert outcome.status is RepairStatus.REJECTED_INVALID, f"{label}: {outcome.reason}"
    assert outcome.verdict is None


def test_repair_cannot_manufacture_infra_error():
    """The state repair exists to resolve must never be its output."""
    outcome = evaluate_repair_reply(INVALID_CASES["infra error laundering"])
    assert outcome.status is RepairStatus.REJECTED_INVALID
    assert "INFRA_ERROR" in outcome.reason


def test_none_is_rejected_without_raising():
    assert evaluate_repair_reply(None).status is RepairStatus.REJECTED_INVALID


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def test_repair_prompt_is_extraction_only():
    prompt = build_repair_prompt()
    lowered = prompt.lower()
    assert "do not perform any new analysis" in lowered
    assert "do not change your assessment" in lowered
    assert "restate" in lowered
    # It must not invite fresh review work.
    assert "review the code" not in lowered


def test_repair_prompt_does_not_offer_infra_error():
    """Offering it as a choice would invite exactly the laundering we reject."""
    assert "INFRA_ERROR" not in build_repair_prompt()


# ---------------------------------------------------------------------------
# Timeout budget
# ---------------------------------------------------------------------------


def test_repair_timeout_defaults():
    assert repair_timeout({}) == DEFAULT_REPAIR_TIMEOUT


def test_repair_timeout_reads_env():
    assert repair_timeout({ENV_REPAIR_TIMEOUT: "12.5"}) == 12.5


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", None])
def test_repair_timeout_falls_back_on_bad_values(raw):
    env = {} if raw is None else {ENV_REPAIR_TIMEOUT: raw}
    assert repair_timeout(env) == DEFAULT_REPAIR_TIMEOUT


def test_repair_budget_is_shorter_than_primary_validator_timeout():
    """Repair is a short extraction; it must never dominate a run."""
    assert DEFAULT_REPAIR_TIMEOUT < 900
