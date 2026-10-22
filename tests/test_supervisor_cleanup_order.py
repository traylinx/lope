"""Cancellation cannot wait for process-table telemetry before killing work."""
import os
import signal

import pytest
import subprocess

from lope import supervisor

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")


class Process:
    pid = 123
    def __init__(self, exited=False):
        self.exited = exited
    def poll(self):
        return 0 if self.exited else None
    def wait(self, timeout):
        self.exited = True
        return 0
    def kill(self):
        self.exited = True


def test_live_group_signalled_before_any_process_table_inspection(monkeypatch):
    events = []
    def signal_tree(proc, sig):
        events.append(sig)
        return "process_group"
    def census(pgid, **kwargs):
        # A slow/unavailable ps used to consume the parent's entire grace
        # before TERM was sent, leaking both leader and descendants.
        assert signal.SIGTERM in events
        assert signal.SIGKILL in events
        return 0
    monkeypatch.setattr(supervisor, "_signal_tree", signal_tree)
    monkeypatch.setattr(supervisor, "_process_group_count", census)
    cleanup, targeted = supervisor._terminate_and_reap(Process())
    assert cleanup.startswith("clean:")
    assert targeted == 1  # lower bound, not a process-tree census


def test_exited_leader_still_kills_group_before_inspection(monkeypatch):
    events = []
    monkeypatch.setattr(supervisor, "_signal_tree", lambda proc, sig: events.append(sig))
    def census(pgid, **kwargs):
        assert events == [signal.SIGKILL]
        return 0
    monkeypatch.setattr(supervisor, "_process_group_count", census)
    assert supervisor._terminate_and_reap(Process(exited=True))[0] == "clean"


def test_census_failure_is_unknown_not_clean(monkeypatch):
    monkeypatch.setattr(supervisor.os, "killpg", lambda *args: None)
    def fail(*args, **kwargs):
        assert kwargs["timeout"] <= 0.3
        raise subprocess.TimeoutExpired("ps", 0.3)
    monkeypatch.setattr(supervisor.subprocess, "run", fail)
    assert supervisor._process_group_count(123) == -1
    monkeypatch.setattr(supervisor, "_signal_tree", lambda *args: "process_group")
    monkeypatch.setattr(supervisor, "CLEANUP_TIMEOUT_SECONDS", 0)
    assert supervisor._terminate_and_reap(Process(exited=True))[0].startswith("cleanup_failed")


def test_failed_cleanup_is_not_confused_with_clean_prefix(monkeypatch):
    import os
    import sys
    real_cleanup = supervisor._terminate_and_reap
    def failed(proc):
        real_cleanup(proc)
        return "cleanup_failed:inspection_unknown", 1
    monkeypatch.setattr(supervisor, "_terminate_and_reap", failed)
    reader, writer = os.pipe()
    try:
        result = supervisor._run_provider({
            "command": [sys.executable, "-c", "print('done')"], "timeout": 5,
        }, reader)
    finally:
        os.close(reader)
        os.close(writer)
    assert result["outcome"] == "cleanup_failed"
    assert result["stdout"].strip() == "done"


def test_supervisor_bounds_identity_probes_before_event_loop(monkeypatch):
    import os
    import sys
    import lope.jobs as jobs
    timeouts = []
    def identity(pid, *, timeout):
        timeouts.append(timeout)
        assert timeout <= 0.3
        return {"pid": pid, "start_fingerprint": None}
    monkeypatch.setattr(jobs, "process_identity", identity)
    reader, writer = os.pipe()
    try:
        result = supervisor._run_provider({
            "command": [sys.executable, "-c", "print('done')"], "timeout": 5,
        }, reader)
    finally:
        os.close(reader)
        os.close(writer)
    assert timeouts
    assert result["outcome"] == "ok"


def test_cleanup_waits_share_one_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(supervisor, "_signal_tree", lambda *args: "process_group")
    class SlowProcess(Process):
        waits = 0
        def wait(self, timeout):
            self.waits += 1
            now[0] += timeout
            if self.waits == 1:
                raise subprocess.TimeoutExpired("fixture", timeout)
            self.exited = True
            return 0
    proc = SlowProcess()
    status, _ = supervisor._terminate_and_reap(proc)
    assert now[0] - 100.0 <= supervisor.CLEANUP_TIMEOUT_SECONDS + 0.0001
    assert status == "cleanup_failed:inspection_unknown"


@pytest.mark.parametrize("count", [0, -1])
def test_group_inspection_does_not_repeat_empty_or_unknown_result(monkeypatch, count):
    calls = []
    def census(*args, **kwargs):
        calls.append(1)
        assert len(calls) == 1
        return count
    monkeypatch.setattr(supervisor, "_process_group_count", census)
    assert supervisor._wait_group_exit(123, supervisor.time.monotonic() + 1) == count
    assert calls == [1]


def test_absent_group_does_not_spawn_ps(monkeypatch):
    def absent(*args):
        raise ProcessLookupError()
    monkeypatch.setattr(supervisor.os, "killpg", absent)
    def forbidden(*args, **kwargs):
        raise AssertionError("ps unnecessary for an absent group")
    monkeypatch.setattr(supervisor.subprocess, "run", forbidden)
    assert supervisor._process_group_count(123) == 0


@pytest.mark.parametrize("before_spawn", [False, True])
def test_supervisor_term_kills_owned_group_and_restores_handler(monkeypatch, before_spawn):
    previous = signal.getsignal(signal.SIGTERM)
    proc = Process()
    signals = []
    monkeypatch.setattr(supervisor, "_signal_tree", lambda p, sig: signals.append((p, sig)))

    def run(spec, control_fd, *, on_spawn):
        handler = signal.getsignal(signal.SIGTERM)
        if before_spawn:
            handler(signal.SIGTERM, None)
        on_spawn(proc)
        if not before_spawn:
            handler(signal.SIGTERM, None)
        return {"outcome": "cancelled"}

    monkeypatch.setattr(supervisor, "_run_provider_impl", run)
    assert supervisor._run_provider({}, -1) == {"outcome": "cancelled"}
    assert signals == [(proc, signal.SIGKILL)]
    assert signal.getsignal(signal.SIGTERM) == previous


def test_forced_supervisor_stop_during_registry_io_kills_provider_tree(tmp_path, monkeypatch):
    """Force the parent fallback while startup I/O blocks the cancellation loop."""
    import sys
    import time
    from lope import processes
    from lope.runtime import InvocationContext, RunBudget

    pids = tmp_path / "pids"
    blocked = tmp_path / "blocked"
    provider = (
        "import os, signal, subprocess, sys, time; "
        "from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']); "
        f"Path({str(pids)!r}).write_text(str(os.getpid()) + ' ' + str(child.pid)); "
        "time.sleep(60)"
    )
    bootstrap = f"""
import time
from pathlib import Path
from lope import supervisor
original = supervisor._registry_update
def block(spec, **fields):
    if 'child' in fields:
        Path({str(blocked)!r}).write_text('startup registry blocked')
        time.sleep(30)
    return original(spec, **fields)
supervisor._registry_update = block
raise SystemExit(supervisor.main())
"""
    real_popen = subprocess.Popen
    context = InvocationContext(budget=RunBudget(mode="ask", run_timeout=30), mode="ask")

    def launch(command, **kwargs):
        assert command[1:3] == ["-m", "lope.supervisor"]
        return real_popen([command[0], "-c", bootstrap, command[-1]], **kwargs)

    def alive(pid):
        result = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
            timeout=2,
        )
        return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")

    # Restore Popen before ps probes (subprocess is a shared module object).
    try:
        with monkeypatch.context() as patch:
            patch.setattr(processes.subprocess, "Popen", launch)
            with pytest.raises(subprocess.TimeoutExpired):
                processes.run_subprocess_group(
                    [sys.executable, "-c", provider], timeout=2, context=context,
                )
        assert context.metadata["process_cleanup_result"] == "unknown"
        assert blocked.is_file(), "fixture must reach the blocked startup path"
        assert pids.is_file(), "fixture must start both provider and descendant"
        owned = [int(value) for value in pids.read_text().split()]
        deadline = time.monotonic() + 5
        while any(alive(pid) for pid in owned) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not any(alive(pid) for pid in owned)
    finally:
        # Failures must not themselves leak this test's isolated process group.
        if pids.is_file():
            pgid = int(pids.read_text().split()[0])
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("mode", ["generate", "validate"])
@pytest.mark.parametrize("cleanup", ["unknown", "cleanup_failed:inspection_unknown"])
def test_timeout_does_not_overwrite_process_cleanup_evidence(monkeypatch, mode, cleanup):
    from types import SimpleNamespace
    from lope import invocation
    from lope.runtime import InvocationContext, RunBudget

    monkeypatch.setattr(invocation, "decide_retry", lambda *a, **k: SimpleNamespace(
        retry=False, classification="provider_timeout", reason="fixture", delay_seconds=0,
    ))

    class Provider:
        name = "fixture"

        def generate_with_context(self, prompt, timeout, context):
            assert context.metadata["process_cleanup_result"] is None
            context.metadata["process_cleanup_result"] = cleanup
            raise subprocess.TimeoutExpired("fixture", timeout)

        validate_with_context = generate_with_context

    context = InvocationContext(
        budget=RunBudget(mode="ask", run_timeout=30), mode="ask",
        metadata={"process_cleanup_result": "clean"},
    )
    with pytest.raises(subprocess.TimeoutExpired):
        if mode == "generate":
            invocation.invoke_generate(Provider(), "prompt", 5, context=context)
        else:
            invocation.invoke_validate(Provider(), "prompt", 5, context=context)
    calls = context.budget.snapshot()["calls"]
    assert len(calls) == 1
    assert calls[0]["cleanup_result"] == cleanup
