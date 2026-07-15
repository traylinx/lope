from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lope.processes import InputLimitExceeded, OutputLimitExceeded, run_subprocess_group
from lope.jobs import RunRegistry
from lope.runtime import InvocationContext, RunBudget


FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="], capture_output=True,
            text=True, timeout=2, check=False,
        ).stdout.strip()
    except Exception:
        state = ""
    return bool(state) and not state.startswith("Z")


def test_stdout_limit_cancels_provider_without_loading_full_payload():
    with pytest.raises(OutputLimitExceeded) as raised:
        run_subprocess_group(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
            timeout=10,
            stdout_limit=32 * 1024,
        )
    assert raised.value.stream == "stdout"
    assert len(raised.value.stdout.encode()) <= 32 * 1024
    assert "output truncated; head and tail preserved" in raised.value.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group escalation")
def test_term_exiting_leader_does_not_leave_term_ignoring_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    script = tmp_path / "leader.py"
    script.write_text(
        "import subprocess,sys,time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(120)'])\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess_group(
            [sys.executable, str(script), str(pid_file)],
            timeout=0.5,
        )
    assert pid_file.exists()
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _alive(grandchild):
        time.sleep(0.05)
    assert not _alive(grandchild)


def test_argv_over_policy_limit_rejects_before_exec():
    with pytest.raises(InputLimitExceeded, match="128|131072|policy limit"):
        run_subprocess_group(["echo", "x" * (128 * 1024)], timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-death process groups")
def test_parent_sigkill_triggers_supervisor_tree_cleanup(tmp_path):
    pid_file = tmp_path / "tree.json"
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        "from lope.processes import run_subprocess_group\n"
        "import sys\n"
        f"run_subprocess_group([sys.executable, {str(FIXTURES / 'ignore_term_tree.py')!r}, {str(pid_file)!r}], timeout=60)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    parent = subprocess.Popen(
        [sys.executable, str(parent_script)], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pids = []
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not pid_file.exists():
            time.sleep(0.05)
        assert pid_file.exists(), "provider tree never started"
        tree = json.loads(pid_file.read_text())
        pids = [int(tree["child"]), int(tree["grandchild"])]
        assert all(_alive(pid) for pid in pids)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        deadline = time.time() + 7
        while time.time() < deadline and any(_alive(pid) for pid in pids):
            time.sleep(0.1)
        assert not any(_alive(pid) for pid in pids)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        for pid in pids:
            if _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_timeout_reaps_direct_child_not_zombie():
    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess_group(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=0.2,
        )
    # The supervisor result is returned only after wait(). A broad zombie scan
    # is noisy on shared hosts, so assert no supervisor/provider from this test.
    out = subprocess.run(
        ["ps", "-axo", "stat=,command="], capture_output=True, text=True, check=False,
    ).stdout
    assert not any(
        line.lstrip().startswith("Z") and "lope.supervisor" in line
        for line in out.splitlines()
    )


def test_supervisor_publishes_child_identity_and_cleanup(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    budget = RunBudget(mode="ask", run_timeout=30, run_id="run1")
    registry.start_run("ask", run_id="run1", run_timeout=30)
    context = InvocationContext(
        budget=budget, mode="ask", stage="fanout", metadata={"registry": registry},
    )
    lease = budget.reserve_call(
        stage="fanout", validator="stub", prompt="hello", requested_timeout=5,
    )
    context.register_lease(lease)
    call_context = context.child(
        validator="stub", metadata={"call_id": lease.record.call_id},
    )
    proc = run_subprocess_group(
        [sys.executable, "-c", "print('ok')"], timeout=5, context=call_context,
    )
    assert proc.stdout.strip() == "ok"
    lease.finish(output_bytes=len(proc.stdout.encode()))
    context.finish_lease(lease)
    manifest = registry.load_active("run1")
    call = manifest["calls"][lease.record.call_id]
    assert call["supervisor"]["pid"] > 0
    assert call["child"]["pid"] > 0
    assert call["pgid"] > 0
    assert call["ownership_marker_hash"]
    assert call["cleanup_result"].startswith("clean")
    assert not list((registry.work_dir / "run1").rglob("supervisor-result.json"))
