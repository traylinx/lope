"""Integration: the ValidatorPool repair seam.

Proves the behaviour change end-to-end -- a validator whose analysis was sound
but whose formatting was not no longer loses its verdict to the fallback
chain.
"""

from __future__ import annotations

import pytest

from lope.models import PhaseVerdict, ValidatorResult, VerdictStatus
from lope.validators import Validator, ValidatorPool


class ScriptedValidator(Validator):
    """Returns a canned validate() result and a canned generate() reply."""

    def __init__(self, name, result, repair_reply=None, repair_raises=False):
        self._name = name
        self._result = result
        self._repair_reply = repair_reply
        self._repair_raises = repair_raises
        self.generate_calls = []

    @property
    def name(self):
        return self._name

    def validate(self, prompt, timeout=900, **kwargs):
        return self._result

    def generate(self, prompt, timeout=900, *, context=None):
        self.generate_calls.append((prompt, timeout))
        if self._repair_raises:
            raise RuntimeError("repair call blew up")
        return self._repair_reply


def _missing_block(name="codex", raw="Looks good to me overall."):
    return ValidatorResult(
        validator_name=name,
        verdict=PhaseVerdict(
            status=VerdictStatus.INFRA_ERROR,
            rationale="no VERDICT: block found in validator response",
            validator_name=name,
        ),
        raw_response=raw,
    )


def _timed_out(name="codex"):
    return ValidatorResult(
        validator_name=name,
        verdict=PhaseVerdict(status=VerdictStatus.INFRA_ERROR, validator_name=name),
        raw_response="",
        error="validator timed out after 900s",
    )


@pytest.fixture(autouse=True)
def _isolate_memory(tmp_path, monkeypatch):
    """Keep audit rows out of the real ~/.lope/memory.db."""
    monkeypatch.setenv("LOPE_MEMORY_DB", str(tmp_path / "memory.db"))


def test_missing_block_is_repaired_and_halts_the_chain():
    primary = ScriptedValidator("codex", _missing_block(), repair_reply="VERDICT: PASS")
    backup = ScriptedValidator(
        "opencode",
        ValidatorResult(
            validator_name="opencode",
            verdict=PhaseVerdict(status=VerdictStatus.FAIL, validator_name="opencode"),
        ),
    )

    result = ValidatorPool([primary, backup], primary="codex").validate("p", timeout=1)

    assert result.verdict.status is VerdictStatus.PASS
    assert result.validator_name == "codex"
    assert result.repair_attempted is True
    assert result.repair_status == "accepted"
    assert result.initial_parse_status is VerdictStatus.INFRA_ERROR
    # The backup was never needed.
    assert backup.generate_calls == []


def test_repair_is_attempted_exactly_once():
    primary = ScriptedValidator("codex", _missing_block(), repair_reply="VERDICT: PASS")
    ValidatorPool([primary], primary="codex").validate("p", timeout=1)
    assert len(primary.generate_calls) == 1


def test_prose_reply_is_rejected_and_falls_through():
    primary = ScriptedValidator(
        "codex", _missing_block(), repair_reply="Sure! I think it passes."
    )
    backup = ScriptedValidator(
        "opencode",
        ValidatorResult(
            validator_name="opencode",
            verdict=PhaseVerdict(
                status=VerdictStatus.NEEDS_FIX, validator_name="opencode"
            ),
        ),
    )

    result = ValidatorPool([primary, backup], primary="codex").validate("p", timeout=1)

    # Repair refused, so the chain behaves exactly as it did before.
    assert result.validator_name == "opencode"
    assert result.verdict.status is VerdictStatus.NEEDS_FIX


def test_timeout_never_triggers_repair():
    """A tool that did not run is not a formatting problem."""
    primary = ScriptedValidator("codex", _timed_out(), repair_reply="VERDICT: PASS")
    backup = ScriptedValidator(
        "opencode",
        ValidatorResult(
            validator_name="opencode",
            verdict=PhaseVerdict(status=VerdictStatus.PASS, validator_name="opencode"),
        ),
    )

    result = ValidatorPool([primary, backup], primary="codex").validate("p", timeout=1)

    assert primary.generate_calls == []
    assert result.validator_name == "opencode"


def test_repair_failure_leaves_the_chain_intact():
    primary = ScriptedValidator("codex", _missing_block(), repair_raises=True)
    backup = ScriptedValidator(
        "opencode",
        ValidatorResult(
            validator_name="opencode",
            verdict=PhaseVerdict(status=VerdictStatus.PASS, validator_name="opencode"),
        ),
    )

    result = ValidatorPool([primary, backup], primary="codex").validate("p", timeout=1)

    assert result.validator_name == "opencode"
    assert result.verdict.status is VerdictStatus.PASS


def test_clean_verdict_never_triggers_repair():
    primary = ScriptedValidator(
        "codex",
        ValidatorResult(
            validator_name="codex",
            verdict=PhaseVerdict(status=VerdictStatus.PASS, validator_name="codex"),
        ),
    )

    result = ValidatorPool([primary], primary="codex").validate("p", timeout=1)

    assert primary.generate_calls == []
    assert result.repair_attempted is False


def test_validator_without_generate_support_degrades_gracefully():
    """Not every CLI implements generate(); repair must not crash the pool."""

    class NoDraft(Validator):
        @property
        def name(self):
            return "nodraft"

        def validate(self, prompt, timeout=900, **kwargs):
            return _missing_block("nodraft")

    result = ValidatorPool([NoDraft()], primary="nodraft").validate("p", timeout=1)

    assert result.verdict.status is VerdictStatus.INFRA_ERROR
    assert "all validators exhausted" in result.verdict.rationale
