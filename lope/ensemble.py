"""Parallel ensemble — fan out to every validator, synthesize a vote.

This module exists so consumers (both lope internals and third-party code
using `lope` as a library) can import the ensemble primitive without
pulling in the entire validators.py subclass zoo. It depends only on:

- `Validator` + `_infra_error` from `validators` (these are the narrow
  ABC + helper, not the full CLI-specific subclasses).
- `ValidatorResult`, `PhaseVerdict`, `VerdictStatus` from `models`.

Extracted from `validators.py` in v0.5.0 as part of the cleanup that
also added `ask`/`review`/`vote`/`compare`/`pipe` — those commands all
consume the ensemble fan-out directly and it was awkward having the
class live in the same file as the 10+ CLI-specific `Validator`
subclasses it doesn't depend on.

The re-exports at the end of `validators.py` preserve backward
compatibility — `from lope.validators import EnsemblePool` still works,
and so does `from lope import EnsemblePool` via `__init__.py`.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import List, Optional

from .models import PhaseVerdict, ValidatorResult, VerdictStatus
from .runtime import (
    BudgetExhausted,
    DEFAULT_MODEL_CALL_TIMEOUT_SECONDS,
    InvocationContext,
    OutcomeClass,
)

DEFAULT_TIMEOUT_SECONDS = DEFAULT_MODEL_CALL_TIMEOUT_SECONDS


class EnsemblePool:
    """Run all validators, synthesize a majority-vote verdict.

    Unlike ValidatorPool (which is a fallback chain), EnsemblePool invokes all
    validators (parallel by default, sequential when requested) and synthesizes
    a single result using:
      - PASS/NEEDS_FIX/FAIL majority vote
      - Any FAIL is a veto (synthesized result is FAIL)
      - Tie on PASS vs NEEDS_FIX → NEEDS_FIX (conservative)
      - Confidence is the mean of decisive results
      - required_fixes is the union of all NEEDS_FIX fix lists, deduplicated
    """

    def __init__(
        self,
        validators: List["Validator"],  # noqa: F821 — forward ref to validators.Validator
        primary: Optional[str] = None,
        max_workers: int = 5,
        parallel: bool = True,
    ):
        if not validators:
            raise ValueError("EnsemblePool needs at least one validator")
        self._validators = list(validators)
        self._primary = primary
        self._max_workers = max_workers
        self._parallel = parallel

    def names(self) -> List[str]:
        return [v.name for v in self._validators]

    def validators(self) -> List:
        """Return all validators in the ensemble."""
        return list(self._validators)

    def primary_validator(self):
        """Return the primary validator — the one used as the drafter."""
        if self._primary:
            for v in self._validators:
                if v.name == self._primary:
                    return v
        return self._validators[0]

    def reviewers(self) -> List:
        """Return the non-primary validators, used to vote on drafts."""
        primary = self.primary_validator()
        return [v for v in self._validators if v is not primary]

    def validate(
        self,
        prompt: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        *,
        context: Optional[InvocationContext] = None,
    ) -> ValidatorResult:
        from .validators import _infra_error

        if context is None:
            context = getattr(self, "_invocation_context", None)

        available = [v for v in self._validators if v.available()]
        if not available:
            return _infra_error(
                "ensemble",
                f"no validators available in pool: {[v.name for v in self._validators]}",
            )

        results: List[ValidatorResult] = []
        deadline = time.monotonic() + float(timeout)
        if context is not None:
            deadline = min(deadline, time.monotonic() + context.budget.remaining_seconds())

        def timeout_result(validator, reason: str) -> ValidatorResult:
            return ValidatorResult(
                validator_name=validator.name,
                verdict=PhaseVerdict(
                    status=VerdictStatus.INFRA_ERROR,
                    rationale=reason,
                    validator_name=validator.name,
                ),
                error=reason,
            )

        def invoke(validator) -> ValidatorResult:
            lease = None
            effective = max(0.0, deadline - time.monotonic())
            if context is not None:
                try:
                    lease = context.budget.reserve_call(
                        stage=context.stage,
                        validator=validator.name,
                        prompt=prompt,
                        requested_timeout=min(float(timeout), effective),
                        transport="adapter",
                    )
                    context.register_lease(lease, transport="adapter")
                    effective = lease.effective_timeout
                except BudgetExhausted as exc:
                    return timeout_result(validator, f"{exc.reason}: {exc}")
            if effective <= 0:
                return timeout_result(validator, "run_budget_exhausted: fan-out deadline elapsed")
            try:
                result = validator.validate(prompt, effective)
            except Exception as exc:
                result = timeout_result(validator, f"validator raised: {exc}")
            if lease is not None:
                outcome = (
                    OutcomeClass.OK
                    if result.verdict.status not in (VerdictStatus.INFRA_ERROR, VerdictStatus.INCONCLUSIVE)
                    else OutcomeClass.PARSE_ERROR
                )
                lease.finish(
                    outcome,
                    output_bytes=len((result.raw_response or "").encode("utf-8")),
                    cleanup_result="clean" if not result.error else "unknown",
                    reason=result.error or result.verdict.rationale,
                )
                context.finish_lease(lease)
            return result

        if not self._parallel:
            for v in available:
                if time.monotonic() >= deadline:
                    results.append(timeout_result(v, f"{v.name} fanout timed out after {timeout}s"))
                    continue
                results.append(invoke(v))
            return synthesize(results, primary=self._primary, expected_count=len(available))

        # Daemon workers are intentional. A broken legacy in-process adapter
        # cannot be killed safely, but it also must not keep Python alive after
        # the fan-out deadline. External adapters move behind the killable
        # supervisor boundary in Phase 2.
        completed: "queue.Queue[tuple]" = queue.Queue()
        pending = list(available)
        active = {}

        def worker(validator) -> None:
            completed.put((validator, invoke(validator)))

        while pending or active:
            while pending and len(active) < min(len(available), self._max_workers):
                if time.monotonic() >= deadline:
                    break
                validator = pending.pop(0)
                thread = threading.Thread(target=worker, args=(validator,), daemon=True)
                active[validator.name] = (validator, thread)
                thread.start()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                validator, result = completed.get(timeout=remaining)
            except queue.Empty:
                break
            active.pop(validator.name, None)
            results.append(result)

        for validator in pending:
            results.append(timeout_result(validator, "run_budget_exhausted: validator was never scheduled"))
        for validator, _thread in active.values():
            results.append(timeout_result(validator, f"{validator.name} fanout timed out after {timeout}s"))

        return synthesize(results, primary=self._primary, expected_count=len(available))


def synthesize(
    results: List[ValidatorResult],
    primary: Optional[str] = None,
    expected_count: Optional[int] = None,
) -> ValidatorResult:
    """Majority-vote synthesis across a list of ValidatorResults.

    Public since v0.5.0 — third-party code may want the aggregation logic
    without the ThreadPool fan-out (e.g. when results come from an HTTP
    API or a cached run). The previous private name `_synthesize` remains
    as a module-level alias for one release.
    """
    decisive_statuses = (VerdictStatus.PASS, VerdictStatus.NEEDS_FIX, VerdictStatus.FAIL)

    def substantive(result: ValidatorResult) -> bool:
        if result.error or result.verdict.status not in decisive_statuses:
            return False
        raw = (result.raw_response or "").strip()
        rationale = (result.verdict.rationale or "").strip()
        if not raw:
            return bool(rationale)
        lowered = raw.lower()
        tool_only = (
            ("tool_calls" in lowered or "<tool" in lowered or "tool_use" in lowered)
            and "verdict" not in lowered
        )
        return not tool_only

    decisive = [r for r in results if substantive(r)]
    infra_errors = [r for r in results if r not in decisive]
    expected = expected_count if expected_count is not None else len(results)
    quorum = expected // 2 + 1

    if len(decisive) < quorum:
        reasons = []
        for result in infra_errors:
            reason = result.error or result.verdict.rationale or "invalid or empty output"
            reasons.append(f"{result.validator_name}: {reason[:160]}")
        reason_text = "; ".join(reasons) or "no substantive validator output"
        return ValidatorResult(
            validator_name="ensemble",
            verdict=PhaseVerdict(
                status=VerdictStatus.INCONCLUSIVE,
                rationale=(
                    f"substantive-result quorum not met: {len(decisive)}/{quorum} "
                    f"required from {expected}; {reason_text}"
                ),
                validator_name="ensemble",
            ),
            error="inconclusive: substantive-result quorum not met",
        )

    if not decisive:
        last = infra_errors[-1] if infra_errors else None
        last_err = (
            (last.error or last.verdict.rationale) if last else "all validators failed"
        )
        return ValidatorResult(
            validator_name="ensemble",
            verdict=PhaseVerdict(
                status=VerdictStatus.INFRA_ERROR,
                rationale=f"all validators infra error: {last_err[:300]}",
                validator_name="ensemble",
            ),
            error=last_err,
        )

    vote: dict = {
        VerdictStatus.PASS: 0,
        VerdictStatus.NEEDS_FIX: 0,
        VerdictStatus.FAIL: 0,
    }
    for r in decisive:
        vote[r.verdict.status] += 1

    if vote[VerdictStatus.FAIL] > 0:
        final_status = VerdictStatus.FAIL
    elif vote[VerdictStatus.PASS] > vote[VerdictStatus.NEEDS_FIX]:
        final_status = VerdictStatus.PASS
    elif vote[VerdictStatus.PASS] == vote[VerdictStatus.NEEDS_FIX]:
        final_status = VerdictStatus.NEEDS_FIX
    else:
        final_status = VerdictStatus.NEEDS_FIX

    confidence_vals = [
        r.verdict.confidence for r in decisive if r.verdict.confidence > 0
    ]
    confidence = sum(confidence_vals) / len(confidence_vals) if confidence_vals else 0.0

    all_fixes: List[str] = []
    seen: set = set()
    for r in decisive:
        if r.verdict.status == VerdictStatus.NEEDS_FIX:
            for fix in r.verdict.required_fixes:
                if fix not in seen:
                    seen.add(fix)
                    all_fixes.append(fix)

    primary_rationale = ""
    if primary:
        for r in decisive:
            if r.validator_name == primary and r.verdict.rationale:
                primary_rationale = f" Primary ({primary}): {r.verdict.rationale[:200]}"
                break

    vote_summary = (
        f"PASS={vote[VerdictStatus.PASS]} "
        f"NEEDS_FIX={vote[VerdictStatus.NEEDS_FIX]} "
        f"FAIL={vote[VerdictStatus.FAIL]}"
    )

    return ValidatorResult(
        validator_name="ensemble",
        verdict=PhaseVerdict(
            status=final_status,
            confidence=confidence,
            rationale=f"Ensemble ({len(decisive)} validators): {vote_summary}.{primary_rationale}",
            required_fixes=all_fixes,
            validator_name="ensemble",
        ),
        raw_response="",
        error="",
    )


# Back-compat alias for code that imported `_synthesize` from validators.py.
_synthesize = synthesize


__all__ = ["EnsemblePool", "synthesize"]
