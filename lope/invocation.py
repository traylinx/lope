"""Common budget, retry, circuit-breaker, and telemetry call boundary."""

from __future__ import annotations

from typing import Any, Optional

from .retry_policy import classify_failure, decide_retry
from .runtime import BudgetExhausted, InvocationContext, OutcomeClass


def _outcome_for(error: object) -> OutcomeClass:
    kind = classify_failure(error).kind
    return {
        "rate_limited": OutcomeClass.RATE_LIMITED,
        "provider_timeout": OutcomeClass.PROVIDER_TIMEOUT,
        "output_limit": OutcomeClass.OUTPUT_LIMIT,
        "input_limit": OutcomeClass.INPUT_LIMIT,
        "parse_error": OutcomeClass.PARSE_ERROR,
        "service_unavailable": OutcomeClass.TRANSIENT_FAILURE,
        "connection_reset": OutcomeClass.TRANSIENT_FAILURE,
    }.get(kind, OutcomeClass.LAUNCH_ERROR)


def _call_context(
    context: InvocationContext,
    *,
    stage: Optional[str],
    validator: str,
    call_id: str,
    metadata: Optional[dict],
) -> InvocationContext:
    extra = dict(metadata or {})
    extra["call_id"] = call_id
    return context.child(stage=stage, validator=validator, metadata=extra)


def _wait(context: InvocationContext, seconds: float) -> bool:
    if seconds <= 0:
        return True
    return not context.cancellation.wait(seconds)


def invoke_generate(
    validator: Any,
    prompt: str,
    timeout: float,
    *,
    context: Optional[InvocationContext] = None,
    stage: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Generate once, with at most one typed transient retry inside budget."""

    if context is None:
        bridge = getattr(validator, "generate_with_context", None)
        return (
            bridge(prompt, timeout, None)
            if callable(bridge)
            else validator.generate(prompt, timeout)
        )

    root = context.child(stage=stage or context.stage, validator=validator.name)
    attempt = 0
    while True:
        if root.budget.remaining_seconds() < float(timeout):
            root.budget.mark_partial(
                "run_budget_exhausted: remaining run budget cannot fund "
                f"complete {float(timeout):g}s call"
            )
            raise BudgetExhausted(
                OutcomeClass.RUN_BUDGET_EXHAUSTED.value,
                f"remaining run budget cannot fund complete {float(timeout):g}s call",
            )
        try:
            lease = root.budget.reserve_call(
                stage=root.stage,
                validator=validator.name,
                prompt=prompt,
                requested_timeout=timeout,
                transport="adapter",
            )
        except BudgetExhausted as exc:
            if exc.reason == OutcomeClass.RUN_BUDGET_EXHAUSTED.value:
                root.budget.mark_partial(f"{exc.reason}: {exc}")
            raise
        root.register_lease(lease, transport="adapter")
        call_context = _call_context(
            root,
            stage=stage,
            validator=validator.name,
            call_id=lease.record.call_id,
            metadata=metadata,
        )
        try:
            bridge = getattr(validator, "generate_with_context", None)
            answer = (
                bridge(prompt, lease.effective_timeout, call_context)
                if callable(bridge)
                else validator.generate(prompt, lease.effective_timeout)
            ) or ""
        except Exception as exc:
            outcome = _outcome_for(exc)
            lease.finish(
                outcome,
                cleanup_result=(
                    "clean"
                    if outcome in {
                        OutcomeClass.PROVIDER_TIMEOUT,
                        OutcomeClass.RATE_LIMITED,
                        OutcomeClass.TRANSIENT_FAILURE,
                        OutcomeClass.INPUT_LIMIT,
                        OutcomeClass.OUTPUT_LIMIT,
                    }
                    else "unknown"
                ),
                reason=str(exc)[:300],
            )
            root.finish_lease(lease)
            decision = decide_retry(
                exc,
                attempt=attempt,
                remaining_seconds=root.budget.remaining_seconds(),
                requested_timeout=float(timeout),
                seed=f"{root.run_id}:{validator.name}:{attempt}",
            )
            root.budget.add_event(
                "retry_scheduled" if decision.retry else "retry_skipped",
                validator=validator.name,
                stage=root.stage,
                classification=decision.classification,
                reason=decision.reason,
                delay_seconds=decision.delay_seconds,
                attempt=attempt + 1,
            )
            if not decision.retry:
                raise
            progress = root.metadata.get("progress")
            if progress is not None:
                progress.retry(validator.name, decision.delay_seconds, decision.classification)
            if not _wait(root, decision.delay_seconds):
                raise BudgetExhausted(
                    OutcomeClass.CANCELLED.value,
                    root.cancellation.reason or "run cancelled during retry delay",
                )
            attempt += 1
            continue
        lease.finish(
            OutcomeClass.OK,
            output_bytes=len(answer.encode("utf-8")),
            cleanup_result="clean",
        )
        root.finish_lease(lease)
        return answer


def invoke_validate(
    validator: Any,
    prompt: str,
    timeout: float,
    *,
    context: Optional[InvocationContext] = None,
    stage: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    """Validate with the same call lease, retry, and breaker semantics."""

    if context is None:
        bridge = getattr(validator, "validate_with_context", None)
        return (
            bridge(prompt, timeout, None)
            if callable(bridge)
            else validator.validate(prompt, timeout)
        )

    from .models import VerdictStatus

    root = context.child(stage=stage or context.stage, validator=validator.name)
    attempt = 0
    while True:
        if root.budget.remaining_seconds() < float(timeout):
            root.budget.mark_partial(
                "run_budget_exhausted: remaining run budget cannot fund "
                f"complete {float(timeout):g}s call"
            )
            raise BudgetExhausted(
                OutcomeClass.RUN_BUDGET_EXHAUSTED.value,
                f"remaining run budget cannot fund complete {float(timeout):g}s call",
            )
        try:
            lease = root.budget.reserve_call(
                stage=root.stage,
                validator=validator.name,
                prompt=prompt,
                requested_timeout=timeout,
                transport="adapter",
            )
        except BudgetExhausted as exc:
            if exc.reason == OutcomeClass.RUN_BUDGET_EXHAUSTED.value:
                root.budget.mark_partial(f"{exc.reason}: {exc}")
            raise
        root.register_lease(lease, transport="adapter")
        call_context = _call_context(
            root,
            stage=stage,
            validator=validator.name,
            call_id=lease.record.call_id,
            metadata=metadata,
        )
        try:
            bridge = getattr(validator, "validate_with_context", None)
            result = (
                bridge(prompt, lease.effective_timeout, call_context)
                if callable(bridge)
                else validator.validate(prompt, lease.effective_timeout)
            )
        except Exception as exc:
            error: object = exc
            result = None
        else:
            if result.verdict.status not in {
                VerdictStatus.INFRA_ERROR,
                VerdictStatus.INCONCLUSIVE,
            }:
                lease.finish(
                    OutcomeClass.OK,
                    output_bytes=len((result.raw_response or "").encode("utf-8")),
                    cleanup_result="clean",
                )
                root.finish_lease(lease)
                return result
            error = result.error or result.verdict.rationale or "invalid validator output"

        outcome = _outcome_for(error)
        lease.finish(
            outcome,
            output_bytes=(
                len((result.raw_response or "").encode("utf-8"))
                if result is not None else 0
            ),
            cleanup_result=(
                "clean"
                if outcome in {
                    OutcomeClass.PROVIDER_TIMEOUT,
                    OutcomeClass.RATE_LIMITED,
                    OutcomeClass.TRANSIENT_FAILURE,
                    OutcomeClass.PARSE_ERROR,
                }
                else "unknown"
            ),
            reason=str(error)[:300],
        )
        root.finish_lease(lease)
        decision = decide_retry(
            error,
            attempt=attempt,
            remaining_seconds=root.budget.remaining_seconds(),
            requested_timeout=float(timeout),
            seed=f"{root.run_id}:{validator.name}:{attempt}",
        )
        root.budget.add_event(
            "retry_scheduled" if decision.retry else "retry_skipped",
            validator=validator.name,
            stage=root.stage,
            classification=decision.classification,
            reason=decision.reason,
            delay_seconds=decision.delay_seconds,
            attempt=attempt + 1,
        )
        if not decision.retry:
            if result is not None:
                return result
            raise error
        progress = root.metadata.get("progress")
        if progress is not None:
            progress.retry(validator.name, decision.delay_seconds, decision.classification)
        if not _wait(root, decision.delay_seconds):
            raise BudgetExhausted(
                OutcomeClass.CANCELLED.value,
                root.cancellation.reason or "run cancelled during retry delay",
            )
        attempt += 1


__all__ = ["invoke_generate", "invoke_validate"]
