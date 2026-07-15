from __future__ import annotations

import threading
import time

import pytest

from lope.runtime import (
    BudgetExhausted,
    InputProfile,
    InvocationContext,
    OutcomeClass,
    RunBudget,
)
from lope.models import PhaseVerdict, ValidatorResult, VerdictStatus


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_input_profile_is_utf8_aware_and_conservative():
    profile = InputProfile.from_text("ä\nabc")
    assert profile.utf8_bytes == 6
    assert profile.lines == 2
    assert profile.estimated_tokens == 2


def test_effective_timeout_is_smallest_positive_cap():
    clock = Clock()
    budget = RunBudget(mode="ask", run_timeout=100, clock=clock, cleanup_reserve_seconds=5)
    assert budget.effective_timeout(80, 20) == 20
    clock.now = 180
    assert budget.effective_timeout(80, 50) == 15


@pytest.mark.parametrize("value", [0, -1])
def test_run_timeout_must_be_positive(value):
    with pytest.raises(ValueError, match="positive"):
        RunBudget(mode="ask", run_timeout=value)


def test_explicit_unbounded_keeps_other_limits():
    budget = RunBudget(
        mode="ask",
        run_timeout=None,
        allow_unbounded_run=True,
        max_external_calls=1,
    )
    lease = budget.reserve_call(
        stage="fanout", validator="a", prompt="x", requested_timeout=10,
    )
    lease.finish()
    with pytest.raises(BudgetExhausted, match="call limit"):
        budget.reserve_call(
            stage="fanout", validator="b", prompt="x", requested_timeout=10,
        )


def test_call_input_and_output_reservations_are_atomic():
    budget = RunBudget(
        mode="review",
        run_timeout=60,
        max_external_calls=8,
        max_input_bytes=8,
        max_output_bytes=8,
    )
    barrier = threading.Barrier(3)
    accepted = []
    rejected = []

    def reserve(name):
        barrier.wait()
        try:
            accepted.append(budget.reserve_call(
                stage="review", validator=name, prompt="12345",
                requested_timeout=10, output_limit_bytes=5,
            ))
        except BudgetExhausted as exc:
            rejected.append(exc.reason)

    threads = [threading.Thread(target=reserve, args=(str(i),)) for i in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert len(rejected) == 1
    accepted[0].finish(output_bytes=3)


def test_deadline_exhaustion_is_typed():
    clock = Clock()
    budget = RunBudget(mode="ask", run_timeout=10, clock=clock, cleanup_reserve_seconds=2)
    clock.now = 109
    with pytest.raises(BudgetExhausted) as raised:
        budget.reserve_call(
            stage="fanout", validator="a", prompt="x", requested_timeout=10,
        )
    assert raised.value.reason == OutcomeClass.RUN_BUDGET_EXHAUSTED.value


def test_invocation_context_children_share_budget_without_globals():
    budget = RunBudget(mode="execute", run_timeout=60)
    root = InvocationContext(budget=budget, mode="execute")
    child = root.child(stage="quality", validator="claude", metadata={"phase": 1})
    assert child.budget is root.budget
    assert child.cancellation is root.cancellation
    assert child.stage == "quality"
    assert child.validator == "claude"
    assert child.metadata == {"phase": 1}


def test_snapshot_has_additive_machine_contract_and_telemetry():
    budget = RunBudget(mode="ask", run_timeout=60)
    lease = budget.reserve_call(
        stage="fanout", validator="claude", prompt="hello",
        requested_timeout=20, output_limit_bytes=100,
        transport="stdin",
    )
    lease.finish(OutcomeClass.OK, output_bytes=2, cleanup_result="clean")
    payload = budget.snapshot()
    assert payload["schema_version"] == 1
    assert payload["plan"]["mode"] == "ask"
    assert set(("timing", "limits", "partial", "reason", "calls")) <= set(payload)
    assert payload["calls"][0]["validator"] == "claude"
    assert payload["calls"][0]["outcome"] == "ok"


def test_actual_output_over_reservation_is_visible():
    budget = RunBudget(mode="ask", run_timeout=60, max_output_bytes=100)
    lease = budget.reserve_call(
        stage="fanout", validator="a", prompt="x", requested_timeout=10,
        output_limit_bytes=10,
    )
    record = lease.finish(output_bytes=11)
    assert record.outcome == OutcomeClass.OUTPUT_LIMIT.value


def test_ensemble_deadline_returns_when_legacy_validator_ignores_timeout():
    from lope.ensemble import EnsemblePool

    class Stuck:
        name = "stuck"

        def available(self):
            return True

        def validate(self, _prompt, _timeout):
            time.sleep(5)
            return ValidatorResult(
                validator_name=self.name,
                verdict=PhaseVerdict(VerdictStatus.PASS, rationale="late"),
                raw_response="late",
            )

    started = time.monotonic()
    result = EnsemblePool([Stuck()]).validate("x", timeout=0.1)
    assert time.monotonic() - started < 0.6
    assert result.verdict.status == VerdictStatus.INCONCLUSIVE


def test_quorum_rejects_two_errors_plus_tool_only_output():
    from lope.ensemble import synthesize

    results = [
        ValidatorResult(
            validator_name="a",
            verdict=PhaseVerdict(VerdictStatus.INFRA_ERROR, rationale="timeout"),
            error="timeout",
        ),
        ValidatorResult(
            validator_name="b",
            verdict=PhaseVerdict(VerdictStatus.INFRA_ERROR, rationale="timeout"),
            error="",
        ),
        ValidatorResult(
            validator_name="c",
            verdict=PhaseVerdict(VerdictStatus.PASS, rationale="tool intent"),
            raw_response='{"tool_calls":[{"name":"read"}]}',
        ),
    ]
    result = synthesize(results, expected_count=3)
    assert result.verdict.status == VerdictStatus.INCONCLUSIVE
    assert "quorum" in result.verdict.rationale
    assert result.error.startswith("inconclusive")


def test_blank_infra_error_uses_verdict_rationale():
    from lope.ensemble import synthesize

    result = synthesize([
        ValidatorResult(
            validator_name="claude",
            verdict=PhaseVerdict(VerdictStatus.INFRA_ERROR, rationale="provider timed out"),
            error="",
        )
    ])
    assert "provider timed out" in result.verdict.rationale


def test_flow_bridge_preserves_measured_duration_when_verdict_reports_zero():
    from lope.flow.model import NodeResult
    from lope.flow.report import FlowReport, flow_report_to_execution_report

    verdict = PhaseVerdict(VerdictStatus.PASS, rationale="ok", duration_seconds=0)
    flow = FlowReport("measured", node_results=[
        NodeResult("review", "succeeded", verdict=verdict, duration_seconds=12.5),
    ], total_duration_seconds=12.5)
    report = flow_report_to_execution_report(flow)
    assert report.phase_verdicts[0].duration_seconds == 12.5
