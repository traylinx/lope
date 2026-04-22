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

from typing import List, Optional

from .models import PhaseVerdict, ValidatorResult, VerdictStatus

DEFAULT_TIMEOUT_SECONDS = 480


class EnsemblePool:
    """Run all validators concurrently, synthesize a majority-vote verdict.

    Unlike ValidatorPool (which is a fallback chain), EnsemblePool fires all
    validators in parallel threads and synthesizes a single result using:
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
    ):
        if not validators:
            raise ValueError("EnsemblePool needs at least one validator")
        self._validators = list(validators)
        self._primary = primary
        self._max_workers = max_workers

    def names(self) -> List[str]:
        return [v.name for v in self._validators]

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
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from .validators import _infra_error

        available = [v for v in self._validators if v.available()]
        if not available:
            return _infra_error(
                "ensemble",
                f"no validators available in pool: {[v.name for v in self._validators]}",
            )

        results: List[ValidatorResult] = []
        with ThreadPoolExecutor(
            max_workers=min(len(available), self._max_workers)
        ) as executor:
            futures = {
                executor.submit(v.validate, prompt, timeout): v for v in available
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    v = futures[future]
                    results.append(
                        ValidatorResult(
                            validator_name=v.name,
                            verdict=PhaseVerdict(
                                status=VerdictStatus.INFRA_ERROR,
                                rationale=f"thread raised: {e}",
                                validator_name=v.name,
                            ),
                            error=str(e),
                        )
                    )

        return synthesize(results, primary=self._primary)


def synthesize(
    results: List[ValidatorResult], primary: Optional[str] = None
) -> ValidatorResult:
    """Majority-vote synthesis across a list of ValidatorResults.

    Public since v0.5.0 — third-party code may want the aggregation logic
    without the ThreadPool fan-out (e.g. when results come from an HTTP
    API or a cached run). The previous private name `_synthesize` remains
    as a module-level alias for one release.
    """
    decisive = [r for r in results if r.verdict.status != VerdictStatus.INFRA_ERROR]
    infra_errors = [r for r in results if r.verdict.status == VerdictStatus.INFRA_ERROR]

    if not decisive:
        last_err = infra_errors[-1].error if infra_errors else "all validators failed"
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
