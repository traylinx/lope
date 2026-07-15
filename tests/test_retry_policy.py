from __future__ import annotations

import time

import pytest

from lope.invocation import invoke_generate
from lope.retry_policy import classify_failure, decide_retry
from lope.runtime import BudgetExhausted, InvocationContext, RunBudget


class _SequenceValidator:
    name = "stub"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, _prompt, _timeout):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _context(run_timeout=60):
    return InvocationContext(
        budget=RunBudget(mode="ask", run_timeout=run_timeout),
        mode="ask",
    )


def test_failure_classifier_retries_only_transient_classes():
    assert classify_failure("HTTP 429 Retry-After: 2").kind == "rate_limited"
    assert classify_failure("HTTP 503 service unavailable").retryable
    assert classify_failure("ConnectionResetError").retryable
    assert not classify_failure("HTTP 401 unauthorized").retryable
    assert not classify_failure("parse error: missing verdict").retryable
    assert not classify_failure("provider timed out after 3s").retryable


def test_short_retry_after_retries_once_inside_budget(monkeypatch):
    monkeypatch.setenv("LOPE_RETRY_JITTER", "off")
    validator = _SequenceValidator([
        RuntimeError("HTTP 429 Retry-After: 0"),
        "recovered",
    ])
    context = _context()

    assert invoke_generate(validator, "prompt", 1, context=context) == "recovered"
    assert validator.calls == 2
    snapshot = context.budget.snapshot()
    assert snapshot["actual"]["calls"] == 2
    assert any(event["kind"] == "retry_scheduled" for event in snapshot["events"])


def test_long_retry_after_is_skipped_without_sleep():
    decision = decide_retry(
        "HTTP 429 Retry-After: 120",
        attempt=0,
        remaining_seconds=10,
        requested_timeout=1,
    )
    assert not decision.retry
    assert decision.reason == "retry_after_exceeds_budget"

    validator = _SequenceValidator([RuntimeError("HTTP 429 Retry-After: 120")])
    context = _context(run_timeout=10)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="429"):
        invoke_generate(validator, "prompt", 1, context=context)
    assert time.monotonic() - started < 0.5
    assert validator.calls == 1


def test_deterministic_failure_never_retries():
    validator = _SequenceValidator([RuntimeError("HTTP 401 unauthorized")])
    context = _context()
    with pytest.raises(RuntimeError, match="401"):
        invoke_generate(validator, "prompt", 1, context=context)
    assert validator.calls == 1
    assert context.budget.snapshot()["events"][-1]["reason"] == "not_retryable"


def test_per_run_circuit_opens_after_two_timeout_failures():
    validator = _SequenceValidator([
        RuntimeError("provider timed out after 1s"),
        RuntimeError("provider timed out after 1s"),
    ])
    context = _context()
    for _ in range(2):
        with pytest.raises(RuntimeError, match="timed out"):
            invoke_generate(validator, "prompt", 1, context=context)

    with pytest.raises(BudgetExhausted) as raised:
        invoke_generate(validator, "prompt", 1, context=context)
    assert raised.value.reason == "circuit_open"
    assert validator.calls == 2
    snapshot = context.budget.snapshot()
    assert "stub" in snapshot["circuits"]["open"]
    assert any(event["kind"] == "circuit_opened" for event in snapshot["events"])
