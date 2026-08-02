"""Tests for :mod:`lope.verdict_events` -- error classification and capture."""

from __future__ import annotations

import sqlite3

import pytest

from lope.memory import LopeMemory
from lope.models import PhaseVerdict, ValidatorResult, VerdictStatus
from lope.verdict_events import (
    ENV_DISABLE_EVENTS,
    classify_parse_error,
    events_disabled,
    record_verdict_event,
    task_spec_hash,
)
from lope.verdict_repair import ParseErrorCategory, RepairStatus


def _result(status, *, error="", rationale="", raw="some output", name="codex"):
    return ValidatorResult(
        validator_name=name,
        verdict=PhaseVerdict(status=status, rationale=rationale, validator_name=name),
        raw_response=raw,
        error=error,
    )


# ---------------------------------------------------------------------------
# Classification -- broken tool vs badly formatted reply
# ---------------------------------------------------------------------------


def test_successful_verdict_has_no_error_category():
    assert classify_parse_error(_result(VerdictStatus.PASS)) is None
    assert classify_parse_error(_result(VerdictStatus.NEEDS_FIX)) is None


def test_missing_block_is_classified_as_formatting_fault():
    result = _result(
        VerdictStatus.INFRA_ERROR,
        rationale="no VERDICT: block found in validator response",
    )
    assert classify_parse_error(result) is ParseErrorCategory.MISSING_VERDICT_BLOCK


def test_timeout_is_not_a_formatting_fault():
    result = _result(VerdictStatus.INFRA_ERROR, error="validator timed out after 900s")
    assert classify_parse_error(result) is ParseErrorCategory.TIMEOUT


def test_output_limit_is_classified():
    result = _result(VerdictStatus.INFRA_ERROR, error="output limit exceeded")
    assert classify_parse_error(result) is ParseErrorCategory.OUTPUT_LIMIT


def test_unknown_enum_is_classified():
    result = _result(
        VerdictStatus.INFRA_ERROR, rationale="unknown verdict token: 'MAYBE'"
    )
    assert classify_parse_error(result) is ParseErrorCategory.UNKNOWN_ENUM


def test_subprocess_failure_is_process_exit():
    result = _result(VerdictStatus.INFRA_ERROR, error="exit status 1: command failed")
    assert classify_parse_error(result) is ParseErrorCategory.PROCESS_EXIT


def test_empty_response_with_no_error_is_transport():
    result = _result(VerdictStatus.INFRA_ERROR, raw="")
    assert classify_parse_error(result) is ParseErrorCategory.TRANSPORT


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def test_event_is_written_with_full_context(tmp_path):
    mem = LopeMemory(tmp_path / "memory.db")
    result = _result(
        VerdictStatus.INFRA_ERROR,
        rationale="no VERDICT: block found in validator response",
        raw="I reviewed it and it looks fine to me.",
    )

    assert record_verdict_event(
        result,
        prompt="## Goal\nShip the thing",
        run_id="run-42",
        gate_id="phase-1",
        latency_s=12.5,
        exit_status=0,
        memory=mem,
    )

    conn = sqlite3.connect(str(mem.db_path))
    row = conn.execute(
        "SELECT run_id, gate_id, validator_name, initial_parse_status,"
        " parse_error_category, final_verdict, latency_s, task_spec_hash,"
        " rendered_prompt, raw_response, repair_attempted"
        " FROM verdict_events"
    ).fetchone()
    conn.close()

    assert row[0] == "run-42"
    assert row[1] == "phase-1"
    assert row[2] == "codex"
    assert row[3] == "INFRA_ERROR"
    assert row[4] == "missing-verdict-block"
    assert row[5] == "INFRA_ERROR"
    assert row[6] == 12.5
    assert row[7] == task_spec_hash("## Goal\nShip the thing")
    # The reasoning that used to be discarded is now on disk.
    assert "looks fine to me" in row[9]
    assert row[10] == 0


def test_repair_outcome_is_recorded(tmp_path):
    mem = LopeMemory(tmp_path / "memory.db")
    result = _result(VerdictStatus.PASS, raw="VERDICT: PASS")

    record_verdict_event(
        result,
        prompt="p",
        run_id="run-1",
        repair_attempted=True,
        repair_status=RepairStatus.ACCEPTED,
        repaired_response="VERDICT: PASS",
        initial_status=VerdictStatus.INFRA_ERROR,
        memory=mem,
    )

    conn = sqlite3.connect(str(mem.db_path))
    row = conn.execute(
        "SELECT initial_parse_status, final_verdict, repair_attempted, repair_status"
        " FROM verdict_events"
    ).fetchone()
    conn.close()

    # A repaired verdict is distinguishable from one that parsed cleanly.
    assert row == ("INFRA_ERROR", "PASS", 1, "accepted")


def test_secrets_are_redacted_before_storage(tmp_path):
    mem = LopeMemory(tmp_path / "memory.db")
    secret = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    result = _result(VerdictStatus.PASS, raw=f"the key is {secret}")

    record_verdict_event(
        result, prompt=f"use {secret}", run_id="run-1", memory=mem
    )

    conn = sqlite3.connect(str(mem.db_path))
    prompt, raw = conn.execute(
        "SELECT rendered_prompt, raw_response FROM verdict_events"
    ).fetchone()
    conn.close()

    assert secret not in prompt
    assert secret not in raw


def test_recording_never_raises_on_a_broken_backend(tmp_path):
    """Observability must not be able to fail the review it observes."""

    class Broken:
        def _connect(self):
            raise RuntimeError("database is gone")

    assert (
        record_verdict_event(
            _result(VerdictStatus.PASS), prompt="p", run_id="r", memory=Broken()
        )
        is False
    )


def test_events_can_be_disabled(monkeypatch, tmp_path):
    mem = LopeMemory(tmp_path / "memory.db")
    monkeypatch.setenv(ENV_DISABLE_EVENTS, "off")

    assert events_disabled() is True
    assert record_verdict_event(
        _result(VerdictStatus.PASS), prompt="p", run_id="r", memory=mem
    ) is False

    conn = sqlite3.connect(str(mem.db_path))
    count = conn.execute("SELECT COUNT(*) FROM verdict_events").fetchone()[0]
    conn.close()
    assert count == 0


def test_task_spec_hash_is_stable_and_distinguishing():
    assert task_spec_hash("a") == task_spec_hash("a")
    assert task_spec_hash("a") != task_spec_hash("b")
