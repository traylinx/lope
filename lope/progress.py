"""Stderr-only progress, owner heartbeats, and runtime summaries."""

from __future__ import annotations

import math
import os
import sys
import threading
from typing import Optional, TextIO


DEFAULT_HEARTBEAT_SECONDS = 15.0


class ProgressReporter:
    def __init__(
        self,
        budget,
        *,
        registry=None,
        run_id: str = "",
        stream: Optional[TextIO] = None,
        interval: Optional[float] = None,
        emit: Optional[bool] = None,
    ) -> None:
        self.budget = budget
        self.registry = registry
        self.run_id = run_id or budget.run_id
        self.stream = stream or sys.stderr
        configured = os.environ.get("LOPE_HEARTBEAT_INTERVAL")
        self.interval = max(
            0.05,
            float(interval if interval is not None else configured or DEFAULT_HEARTBEAT_SECONDS),
        )
        if emit is None:
            emit = os.environ.get("LOPE_PROGRESS", "").strip().lower() not in {
                "0", "off", "false", "no",
            }
        self.emit = bool(emit)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stage = "provider"
        self._active = set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._heartbeat_owner()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"lope-heartbeat-{self.run_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(1.0, self.interval + 0.1))

    def set_stage(self, stage: str) -> None:
        with self._lock:
            self._stage = str(stage or "provider")

    def call_started(self, validator: str, stage: str) -> None:
        with self._lock:
            self._active.add(str(validator))
            self._stage = str(stage or self._stage)

    def call_finished(self, validator: str, outcome: str) -> None:
        with self._lock:
            self._active.discard(str(validator))
            stage = self._stage
        if self.emit:
            snapshot = self.budget.snapshot()
            actual = snapshot["actual"]
            forecast = snapshot["forecast"]["calls"] or actual["calls"]
            print(
                f"progress: {stage} · {validator} {outcome or 'finished'} · "
                f"{actual['completed_calls']}/{forecast} calls · "
                f"{snapshot['timing']['elapsed_seconds']:.1f}s elapsed",
                file=self.stream,
                flush=True,
            )

    def retry(self, validator: str, delay: float, reason: str) -> None:
        if self.emit:
            print(
                f"progress: retry {validator} in {delay:.2f}s · {reason}",
                file=self.stream,
                flush=True,
            )

    def heartbeat(self) -> None:
        self._heartbeat_owner()
        if not self.emit:
            return
        snapshot = self.budget.snapshot()
        timing = snapshot["timing"]
        actual = snapshot["actual"]
        forecast = snapshot["forecast"]["calls"] or actual["calls"]
        remaining = timing["remaining_seconds"]
        remaining_text = "unbounded" if math.isinf(remaining) else f"{remaining:.1f}s"
        with self._lock:
            active = ",".join(sorted(self._active)) or "none"
            stage = self._stage
        print(
            f"heartbeat: {stage} · {timing['elapsed_seconds']:.1f}s elapsed · "
            f"{remaining_text} remaining · {actual['completed_calls']}/{forecast} calls · "
            f"active={active}",
            file=self.stream,
            flush=True,
        )

    def _heartbeat_owner(self) -> None:
        if self.registry is None:
            return
        try:
            self.registry.heartbeat(self.run_id, source="owner")
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.heartbeat()


def format_runtime_summary(snapshot) -> str:
    forecast = snapshot.get("forecast") or {}
    actual = snapshot.get("actual") or {}
    calls_forecast = int(forecast.get("calls") or actual.get("calls") or 0)
    wall_forecast = float(forecast.get("wall_seconds") or 0.0)
    wall = float(actual.get("wall_seconds") or 0.0)
    line = (
        f"Runtime: calls {int(actual.get('calls') or 0)}/{calls_forecast} · "
        f"wall {wall:.1f}s"
    )
    if wall_forecast:
        line += f"/{wall_forecast:g}s nominal"
    line += (
        f" · input {int(actual.get('input_bytes') or 0)}B"
        f" · output {int(actual.get('output_bytes') or 0)}B"
    )
    artifacts = snapshot.get("artifacts") or []
    if artifacts:
        line += " · artifacts " + ", ".join(str(item.get("path")) for item in artifacts)
    open_circuits = (snapshot.get("circuits") or {}).get("open") or {}
    if open_circuits:
        line += " · circuits open=" + ",".join(sorted(open_circuits))
    per_validator = actual.get("per_validator") or {}
    if per_validator:
        line += "\n  Latency: " + ", ".join(
            f"{name}={float(row.get('latency_seconds') or 0.0):.1f}s"
            for name, row in sorted(per_validator.items())
        )
    cancellations = [
        f"{call.get('validator')}:{call.get('reason')}"
        for call in snapshot.get("calls") or []
        if call.get("outcome") not in {None, "", "ok"} and call.get("reason")
    ]
    if cancellations:
        line += "\n  Reasons: " + "; ".join(cancellations)
    return line


__all__ = ["DEFAULT_HEARTBEAT_SECONDS", "ProgressReporter", "format_runtime_summary"]
