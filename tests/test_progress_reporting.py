from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lope.progress import ProgressReporter, format_runtime_summary
from lope.runtime import InvocationContext, OutcomeClass, RunBudget


class _Registry:
    def __init__(self):
        self.heartbeats = []

    def heartbeat(self, run_id, source="owner"):
        self.heartbeats.append((run_id, source))


def test_slow_call_emits_heartbeat_and_completion_to_stderr_stream():
    stream = io.StringIO()
    registry = _Registry()
    budget = RunBudget(mode="ask", run_timeout=10)
    reporter = ProgressReporter(
        budget,
        registry=registry,
        stream=stream,
        interval=0.05,
        emit=True,
    )
    context = InvocationContext(
        budget=budget,
        mode="ask",
        stage="fanout",
        metadata={"progress": reporter},
    )
    reporter.start()
    lease = budget.reserve_call(
        stage="fanout",
        validator="slow",
        prompt="prompt",
        requested_timeout=1,
    )
    context.register_lease(lease)
    time.sleep(0.08)
    lease.finish(OutcomeClass.OK, output_bytes=2, cleanup_result="clean")
    context.finish_lease(lease)
    reporter.stop()

    output = stream.getvalue()
    assert "heartbeat: fanout" in output
    assert "active=slow" in output
    assert "progress: fanout · slow ok" in output
    assert len(registry.heartbeats) >= 2


def test_runtime_summary_compares_forecast_and_actual_without_bodies():
    budget = RunBudget(mode="ask", run_timeout=10)
    budget.add_request_plan({
        "planned_calls": 3,
        "nominal_wall_ceiling_seconds": 30,
    })
    lease = budget.reserve_call(
        stage="fanout",
        validator="stub",
        prompt="hello",
        requested_timeout=1,
    )
    lease.finish(OutcomeClass.OK, output_bytes=2, cleanup_result="clean")
    summary = format_runtime_summary(budget.snapshot())
    assert "calls 1/3" in summary
    assert "wall" in summary
    assert "input 5B" in summary
    assert "hello" not in summary


def test_command_scope_maps_incomplete_run_budget_to_exit_124(
    tmp_path, monkeypatch
):
    from lope.cli import _command_runtime_scope, _ensure_runtime

    monkeypatch.setenv("LOPE_HOME", str(tmp_path / "home"))
    args = SimpleNamespace(command="ask", json=False)
    cfg = SimpleNamespace(
        run_timeout=60,
        allow_unbounded_run=False,
        max_calls=4,
        max_input_bytes=1024,
        max_output_bytes=1024,
    )
    pool = SimpleNamespace()
    with pytest.raises(SystemExit) as raised:
        with _command_runtime_scope(args):
            _ensure_runtime(args, cfg, pool)
            args._invocation_context.budget.mark_partial(
                "run_budget_exhausted: mandatory stage cannot fit"
            )
            raise SystemExit(1)
    assert raised.value.code == 124
    assert not list((tmp_path / "home" / "runs" / "active").glob("*.json"))


def test_command_scope_preserves_unsafe_admission_exit_2(tmp_path, monkeypatch):
    from lope.cli import _command_runtime_scope, _ensure_runtime

    monkeypatch.setenv("LOPE_HOME", str(tmp_path / "home"))
    args = SimpleNamespace(command="ask", json=True)
    cfg = SimpleNamespace(
        run_timeout=60,
        allow_unbounded_run=False,
        max_calls=4,
        max_input_bytes=1024,
        max_output_bytes=1024,
    )
    pool = SimpleNamespace()
    with pytest.raises(SystemExit) as raised:
        with _command_runtime_scope(args):
            _ensure_runtime(args, cfg, pool)
            raise SystemExit(2)
    assert raised.value.code == 2


def test_real_cli_run_deadline_exhaustion_exits_124_and_closes_registry(tmp_path):
    from lope.config import LopeCfg, save

    home = tmp_path / "home"
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    cfg = LopeCfg(
        validators=["slow-a", "slow-b"],
        primary="slow-a",
        timeout=1,
        parallel=False,
        providers=[
            {"name": "slow-a", "type": "subprocess", "command": sleeper},
            {"name": "slow-b", "type": "subprocess", "command": sleeper},
        ],
    )
    save(cfg, str(home / "config.json"))
    env = dict(os.environ)
    env["LOPE_HOME"] = str(home)
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(root)

    started = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "lope",
            "ask",
            "--json",
            "--timeout",
            "1",
            "--run-timeout",
            "7",
            "--sequential",
            "deadline fixture",
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    elapsed = time.monotonic() - started

    assert proc.returncode == 124, proc.stderr
    assert elapsed < 9
    payload = json.loads(proc.stdout)
    assert payload["partial"] is True
    assert str(payload["reason"]).startswith("run_budget_exhausted")
    assert not list((home / "runs" / "active").glob("*.json"))
