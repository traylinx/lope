"""Command-wide budgets, invocation context, and structured call telemetry.

The public validator API remains ``validate(prompt, timeout)`` / ``generate``.
New orchestration code passes :class:`InvocationContext` internally so every
retry, fallback, synthesis, and provider call spends from the same monotonic
budget.  No mutable global or thread-local state is used.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


SCHEMA_VERSION = 1
DEFAULT_MODEL_CALL_TIMEOUT_SECONDS = 960
DEFAULT_MAX_EXTERNAL_CALLS = 96
DEFAULT_MAX_INPUT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_CHUNKS = 32
DEFAULT_CLEANUP_RESERVE_SECONDS = 5.0

MODE_RUN_TIMEOUT_SECONDS = {
    "ask": 1800,
    "review": 1800,
    "vote": 1800,
    "compare": 1800,
    "pipe": 1800,
    "team": 1800,
    "negotiate": 3600,
    "deliberate": 3600,
    "execute": 7200,
    "implement": 7200,
    "flow": 7200,
    "gate": 1800,
    "check": 1800,
}


class OutcomeClass(str, Enum):
    OK = "ok"
    PROVIDER_TIMEOUT = "provider_timeout"
    RUN_BUDGET_EXHAUSTED = "run_budget_exhausted"
    RATE_LIMITED = "rate_limited"
    LAUNCH_ERROR = "launch_error"
    NONZERO_EXIT = "nonzero_exit"
    PARSE_ERROR = "parse_error"
    INPUT_LIMIT = "input_limit"
    OUTPUT_LIMIT = "output_limit"
    CANCELLED = "cancelled"
    INVALID_OUTPUT = "invalid_output"
    CLEANUP_FAILED = "cleanup_failed"
    TRANSIENT_FAILURE = "transient_failure"
    CIRCUIT_OPEN = "circuit_open"


class BudgetExhausted(RuntimeError):
    """A typed admission failure raised before external work starts."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class InputProfile:
    utf8_bytes: int
    lines: int
    estimated_tokens: int

    @classmethod
    def from_text(cls, text: str) -> "InputProfile":
        raw = (text or "").encode("utf-8")
        return cls(
            utf8_bytes=len(raw),
            lines=(text or "").count("\n") + (1 if text else 0),
            estimated_tokens=int(math.ceil(len(raw) / 3.0)),
        )


@dataclass
class CallRecord:
    run_id: str
    call_id: str
    mode: str
    stage: str
    validator: str
    queued_at: float
    started_at: float
    ended_at: float = 0.0
    prompt_bytes: int = 0
    prompt_lines: int = 0
    estimated_tokens: int = 0
    requested_timeout: float = 0.0
    effective_timeout: float = 0.0
    transport: str = "unknown"
    outcome: str = ""
    output_bytes: int = 0
    cleanup_result: str = "pending"
    reason: str = ""

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or self.started_at
        return max(0.0, end - self.started_at)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["duration_seconds"] = self.duration_seconds
        return data


class CancellationToken:
    """Small cooperative cancellation primitive shared by one run."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if not self._event.is_set():
                self._reason = reason
                self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)


class CallLease:
    """Atomic budget reservation for one external call."""

    def __init__(
        self,
        budget: "RunBudget",
        record: CallRecord,
        reserved_output_bytes: int,
    ) -> None:
        self._budget = budget
        self.record = record
        self._reserved_output_bytes = reserved_output_bytes
        self._finished = False

    @property
    def effective_timeout(self) -> float:
        return self.record.effective_timeout

    def finish(
        self,
        outcome: OutcomeClass = OutcomeClass.OK,
        *,
        output_bytes: int = 0,
        cleanup_result: str = "clean",
        reason: str = "",
    ) -> CallRecord:
        if self._finished:
            return self.record
        self._finished = True
        self._budget._finish_call(  # noqa: SLF001 - lease is budget-owned
            self.record,
            self._reserved_output_bytes,
            outcome,
            output_bytes,
            cleanup_result,
            reason,
        )
        return self.record

    def __enter__(self) -> "CallLease":
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        if self._finished:
            return
        if exc_type is None:
            self.finish()
        else:
            self.finish(
                OutcomeClass.LAUNCH_ERROR,
                cleanup_result="unknown",
                reason=str(exc)[:300],
            )


class RunBudget:
    """Thread-safe monotonic wall/call/byte budget for one CLI command."""

    def __init__(
        self,
        *,
        mode: str,
        run_timeout: Optional[float],
        allow_unbounded_run: bool = False,
        max_external_calls: int = DEFAULT_MAX_EXTERNAL_CALLS,
        max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        cleanup_reserve_seconds: float = DEFAULT_CLEANUP_RESERVE_SECONDS,
        run_id: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if run_timeout is not None and run_timeout <= 0:
            raise ValueError("run_timeout must be positive")
        if run_timeout is None and not allow_unbounded_run:
            run_timeout = float(MODE_RUN_TIMEOUT_SECONDS.get(mode, 1800))
        if max_external_calls <= 0 or max_input_bytes <= 0 or max_output_bytes <= 0:
            raise ValueError("call and byte limits must be positive")
        self.mode = mode
        self.run_id = run_id or uuid.uuid4().hex
        self._clock = clock
        self.started_at = clock()
        self.deadline = (
            None if run_timeout is None else self.started_at + float(run_timeout)
        )
        self.cleanup_reserve_seconds = max(0.0, float(cleanup_reserve_seconds))
        self.max_external_calls = int(max_external_calls)
        self.max_input_bytes = int(max_input_bytes)
        self.max_output_bytes = int(max_output_bytes)
        self.cancellation = CancellationToken()
        self._lock = threading.RLock()
        self._calls_reserved = 0
        self._input_reserved = 0
        self._output_reserved = 0
        self._output_actual = 0
        self._records: List[CallRecord] = []
        self._partial_reason = ""
        self._request_plans: List[Dict[str, Any]] = []
        self._artifacts: List[Dict[str, Any]] = []
        self._events: List[Dict[str, Any]] = []
        self._circuit_failures: Dict[str, int] = {}
        self._circuit_open: Dict[str, str] = {}
        self._circuit_threshold = 2

    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self.started_at)

    def remaining_seconds(self, *, include_cleanup: bool = False) -> float:
        if self.deadline is None:
            return float("inf")
        reserve = 0.0 if include_cleanup else self.cleanup_reserve_seconds
        return max(0.0, self.deadline - self._clock() - reserve)

    @property
    def exhausted(self) -> bool:
        return self.remaining_seconds() <= 0 or self.cancellation.cancelled

    def effective_timeout(
        self,
        requested_timeout: Optional[float],
        provider_timeout: Optional[float] = None,
    ) -> float:
        values = [self.remaining_seconds()]
        if requested_timeout is not None:
            if requested_timeout <= 0:
                raise ValueError("requested timeout must be positive")
            values.append(float(requested_timeout))
        if provider_timeout is not None:
            if provider_timeout <= 0:
                raise ValueError("provider timeout must be positive")
            values.append(float(provider_timeout))
        effective = min(values)
        if effective <= 0:
            raise BudgetExhausted(
                OutcomeClass.RUN_BUDGET_EXHAUSTED.value,
                "run deadline leaves no time for another external call",
            )
        return effective

    def reserve_call(
        self,
        *,
        stage: str,
        validator: str,
        prompt: str,
        requested_timeout: Optional[float],
        provider_timeout: Optional[float] = None,
        output_limit_bytes: int = 2 * 1024 * 1024,
        transport: str = "unknown",
        queued_at: Optional[float] = None,
    ) -> CallLease:
        profile = InputProfile.from_text(prompt)
        if output_limit_bytes <= 0:
            raise ValueError("output_limit_bytes must be positive")
        now = self._clock()
        with self._lock:
            circuit_reason = self._circuit_open.get(validator)
            if circuit_reason:
                raise BudgetExhausted(
                    OutcomeClass.CIRCUIT_OPEN.value,
                    f"validator {validator} circuit open: {circuit_reason}",
                )
            if self.cancellation.cancelled:
                raise BudgetExhausted(
                    OutcomeClass.CANCELLED.value,
                    self.cancellation.reason or "run cancelled",
                )
            effective = self.effective_timeout(requested_timeout, provider_timeout)
            if self._calls_reserved + 1 > self.max_external_calls:
                raise BudgetExhausted(
                    OutcomeClass.RUN_BUDGET_EXHAUSTED.value,
                    f"external-call limit {self.max_external_calls} exhausted",
                )
            if self._input_reserved + profile.utf8_bytes > self.max_input_bytes:
                raise BudgetExhausted(
                    OutcomeClass.INPUT_LIMIT.value,
                    f"run input limit {self.max_input_bytes} bytes exceeded",
                )
            if self._output_reserved + output_limit_bytes > self.max_output_bytes:
                raise BudgetExhausted(
                    OutcomeClass.OUTPUT_LIMIT.value,
                    f"run output reservation limit {self.max_output_bytes} bytes exceeded",
                )
            self._calls_reserved += 1
            self._input_reserved += profile.utf8_bytes
            self._output_reserved += output_limit_bytes
            record = CallRecord(
                run_id=self.run_id,
                call_id=uuid.uuid4().hex,
                mode=self.mode,
                stage=stage,
                validator=validator,
                queued_at=now if queued_at is None else queued_at,
                started_at=now,
                prompt_bytes=profile.utf8_bytes,
                prompt_lines=profile.lines,
                estimated_tokens=profile.estimated_tokens,
                requested_timeout=float(requested_timeout or effective),
                effective_timeout=effective,
                transport=transport,
            )
            self._records.append(record)
            return CallLease(self, record, output_limit_bytes)

    def _finish_call(
        self,
        record: CallRecord,
        reserved_output_bytes: int,
        outcome: OutcomeClass,
        output_bytes: int,
        cleanup_result: str,
        reason: str,
    ) -> None:
        actual = max(0, int(output_bytes))
        with self._lock:
            self._output_reserved -= reserved_output_bytes
            self._output_actual += actual
            record.ended_at = self._clock()
            record.outcome = outcome.value
            record.output_bytes = actual
            record.cleanup_result = cleanup_result
            record.reason = reason
            if actual > reserved_output_bytes or self._output_actual > self.max_output_bytes:
                record.outcome = OutcomeClass.OUTPUT_LIMIT.value
                if not record.reason:
                    record.reason = "actual output exceeded reserved run allowance"
            self._record_provider_outcome_locked(
                record.validator,
                record.outcome,
                record.reason,
            )
            observed_validator = record.validator
            observed_duration = record.duration_seconds
            observed_outcome = record.outcome
        # Outside the lock: the ledger touches the filesystem, and it is
        # advisory telemetry that must never delay or break a call.
        try:
            from .latency import record as record_latency

            record_latency(
                observed_validator,
                observed_duration,
                outcome=observed_outcome,
            )
        except Exception:
            pass

    def records(self) -> List[CallRecord]:
        with self._lock:
            return list(self._records)

    def mark_partial(self, reason: str) -> None:
        with self._lock:
            if not self._partial_reason:
                self._partial_reason = reason

    def add_event(self, kind: str, **fields: Any) -> None:
        event: Dict[str, Any] = {
            "kind": str(kind),
            "elapsed_seconds": self.elapsed_seconds(),
        }
        for key, value in fields.items():
            if value is not None:
                event[str(key)] = value
        with self._lock:
            self._events.append(event)

    def can_fund(
        self,
        seconds: float,
        *,
        calls: int = 0,
        input_bytes: int = 0,
    ) -> bool:
        with self._lock:
            return (
                self.remaining_seconds() >= max(0.0, float(seconds))
                and self._calls_reserved + max(0, int(calls)) <= self.max_external_calls
                and self._input_reserved + max(0, int(input_bytes)) <= self.max_input_bytes
                and not self.cancellation.cancelled
            )

    def calls_reserved(self) -> int:
        with self._lock:
            return self._calls_reserved

    def completed_calls(self) -> int:
        with self._lock:
            return sum(1 for record in self._records if record.ended_at > 0)

    def _record_provider_outcome_locked(
        self,
        validator: str,
        outcome: str,
        reason: str,
    ) -> None:
        if not validator:
            return
        if outcome == OutcomeClass.OK.value:
            self._circuit_failures.pop(validator, None)
            return
        if outcome not in {
            OutcomeClass.PROVIDER_TIMEOUT.value,
            OutcomeClass.RATE_LIMITED.value,
        }:
            return
        failures = self._circuit_failures.get(validator, 0) + 1
        self._circuit_failures[validator] = failures
        if failures >= self._circuit_threshold and validator not in self._circuit_open:
            circuit_reason = (
                f"{failures} consecutive {outcome} failures"
                + (f": {reason[:160]}" if reason else "")
            )
            self._circuit_open[validator] = circuit_reason
            self._events.append({
                "kind": "circuit_opened",
                "elapsed_seconds": self.elapsed_seconds(),
                "validator": validator,
                "reason": circuit_reason,
            })

    def circuit_reason(self, validator: str) -> str:
        with self._lock:
            return self._circuit_open.get(validator, "")

    def add_request_plan(self, plan: Dict[str, Any]) -> None:
        """Attach an additive, JSON-safe admission forecast to run telemetry."""

        with self._lock:
            self._request_plans.append(dict(plan))

    def add_artifact(self, kind: str, path: str, **metadata: Any) -> None:
        """Expose a user-facing run artifact without storing its body."""

        item: Dict[str, Any] = {
            "kind": str(kind),
            "path": str(path),
        }
        for key, value in metadata.items():
            if value is not None:
                item[str(key)] = value
        with self._lock:
            if item not in self._artifacts:
                self._artifacts.append(item)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            reason = self.cancellation.reason or self._partial_reason
            partial = bool(self._partial_reason) or any(
                r.outcome and r.outcome != OutcomeClass.OK.value for r in self._records
            )
            forecast_calls = max(
                [int(plan.get("planned_calls") or 0) for plan in self._request_plans]
                or [0]
            )
            forecast_wall = max(
                [float(plan.get("nominal_wall_ceiling_seconds") or 0.0) for plan in self._request_plans]
                or [0.0]
            )
            per_validator: Dict[str, Dict[str, Any]] = {}
            for record in self._records:
                row = per_validator.setdefault(record.validator, {
                    "calls": 0,
                    "completed": 0,
                    "latency_seconds": 0.0,
                    "prompt_bytes": 0,
                    "output_bytes": 0,
                    "outcomes": {},
                })
                row["calls"] += 1
                row["completed"] += int(record.ended_at > 0)
                row["latency_seconds"] += record.duration_seconds
                row["prompt_bytes"] += record.prompt_bytes
                row["output_bytes"] += record.output_bytes
                if record.outcome:
                    outcomes = row["outcomes"]
                    outcomes[record.outcome] = outcomes.get(record.outcome, 0) + 1
            return {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "plan": {
                    "mode": self.mode,
                    "requests": list(self._request_plans),
                },
                "timing": {
                    "elapsed_seconds": self.elapsed_seconds(),
                    "remaining_seconds": self.remaining_seconds(),
                    "deadline_monotonic": self.deadline,
                },
                "limits": {
                    "max_external_calls": self.max_external_calls,
                    "calls_reserved": self._calls_reserved,
                    "max_input_bytes": self.max_input_bytes,
                    "input_bytes": self._input_reserved,
                    "max_output_bytes": self.max_output_bytes,
                    "output_bytes": self._output_actual,
                },
                "partial": partial,
                "reason": reason or None,
                "artifacts": list(self._artifacts),
                "forecast": {
                    "calls": forecast_calls,
                    "wall_seconds": forecast_wall,
                },
                "actual": {
                    "calls": self._calls_reserved,
                    "completed_calls": sum(1 for record in self._records if record.ended_at > 0),
                    "wall_seconds": self.elapsed_seconds(),
                    "input_bytes": self._input_reserved,
                    "output_bytes": self._output_actual,
                    "per_validator": per_validator,
                },
                "circuits": {
                    "failures": dict(self._circuit_failures),
                    "open": dict(self._circuit_open),
                },
                "events": list(self._events),
                "calls": [r.to_dict() for r in self._records],
            }


@dataclass(frozen=True)
class InvocationContext:
    """Versioned context passed through new internal adapter boundaries."""

    budget: RunBudget
    mode: str
    stage: str = "provider"
    validator: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = SCHEMA_VERSION

    @property
    def run_id(self) -> str:
        return self.budget.run_id

    @property
    def cancellation(self) -> CancellationToken:
        return self.budget.cancellation

    def child(
        self,
        *,
        stage: Optional[str] = None,
        validator: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "InvocationContext":
        merged = dict(self.metadata)
        if metadata:
            merged.update(metadata)
        return InvocationContext(
            budget=self.budget,
            mode=self.mode,
            stage=stage or self.stage,
            validator=validator if validator is not None else self.validator,
            metadata=merged,
        )

    def register_lease(self, lease: CallLease, *, transport: str = "adapter") -> None:
        registry = self.metadata.get("registry")
        record = lease.record
        if registry is not None:
            registry.register_call(self.run_id, {
                "call_id": record.call_id,
                "validator": record.validator,
                "stage": record.stage,
                "state": "active",
                "started_at": time.time(),
                "heartbeat_at": time.time(),
                "deadline_at": time.time() + record.effective_timeout,
                "transport": transport,
                "owned_paths": [],
                "cleanup_result": "pending",
            })
        progress = self.metadata.get("progress")
        if progress is not None:
            progress.call_started(record.validator, record.stage)

    def add_request_plan(self, plan: Dict[str, Any]) -> None:
        self.budget.add_request_plan(plan)
        registry = self.metadata.get("registry")
        if registry is not None:
            try:
                registry.set_planned_calls(
                    self.run_id,
                    int(plan.get("planned_calls") or 0),
                )
            except Exception:
                pass

    def finish_lease(self, lease: CallLease) -> None:
        registry = self.metadata.get("registry")
        record = lease.record
        if registry is not None:
            registry.update_call(
                self.run_id,
                record.call_id,
                state="finished",
                outcome=record.outcome,
                cleanup_result=record.cleanup_result,
                ended_at=time.time(),
                reason=record.reason,
            )
        progress = self.metadata.get("progress")
        if progress is not None:
            progress.call_finished(record.validator, record.outcome)


def mode_run_timeout(mode: str) -> int:
    return MODE_RUN_TIMEOUT_SECONDS.get(mode, 1800)


__all__ = [
    "BudgetExhausted",
    "CallLease",
    "CallRecord",
    "CancellationToken",
    "DEFAULT_CLEANUP_RESERVE_SECONDS",
    "DEFAULT_MAX_CHUNKS",
    "DEFAULT_MAX_EXTERNAL_CALLS",
    "DEFAULT_MAX_INPUT_BYTES",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_MODEL_CALL_TIMEOUT_SECONDS",
    "InputProfile",
    "InvocationContext",
    "MODE_RUN_TIMEOUT_SECONDS",
    "OutcomeClass",
    "RunBudget",
    "mode_run_timeout",
]
