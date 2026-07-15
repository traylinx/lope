from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import pytest

from lope.jobs import RegistryError, RunRegistry, process_identity, process_state


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def _alive(pid: int) -> bool:
    state = process_state(pid)
    return bool(state) and not state.startswith("Z")


def _wait_dead(pids: List[int], timeout: float = 7.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return not any(_alive(pid) for pid in pids)


def _kill_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        proc.wait(timeout=2)
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows cleanup fallback
            proc.kill()
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)


def _register_abandoned_group(
    registry: RunRegistry,
    proc: subprocess.Popen,
    *,
    run_id: str = "abandoned",
    owned_paths: Optional[List[str]] = None,
) -> None:
    registry.start_run("ask", run_id=run_id, run_timeout=60)
    registry.register_call(run_id, {
        "call_id": "call1",
        "validator": "fixture",
        "stage": "fanout",
        "state": "active",
        "child": process_identity(proc.pid),
        "pgid": os.getpgid(proc.pid) if os.name == "posix" else proc.pid,
        "ownership_marker_hash": hashlib.sha256(b"owned-fixture").hexdigest(),
        "owned_paths": list(owned_paths or []),
        "cleanup_result": "pending",
    })

    def abandon(manifest):
        manifest["owner"] = {"pid": 99999999, "start_fingerprint": "gone"}
        return manifest

    registry.update(run_id, abandon)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group ownership")
def test_reconcile_reaps_only_confirmed_abandoned_group_and_owned_scratch(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    owned = registry.run_work_dir("abandoned") / "stale-spool.txt"
    owned.write_text("stale", encoding="utf-8")
    command = [sys.executable, "-c", "import time; time.sleep(120)"]
    provider = subprocess.Popen(command, start_new_session=True)
    unrelated = subprocess.Popen(command, start_new_session=True)
    try:
        _register_abandoned_group(registry, provider, owned_paths=[str(owned)])
        result = registry.reconcile()
        assert result[0]["action"] == "reaped"
        assert _wait_dead([provider.pid])
        provider.wait(timeout=5)
        assert _alive(unrelated.pid), "same-name unregistered process was touched"
        assert not owned.exists()
        assert not (registry.active_dir / "abandoned.json").exists()
        completed = list(registry.completed_dir.glob("*-abandoned.json"))
        assert len(completed) == 1
        assert json.loads(completed[0].read_text())["cleanup_result"] == "clean"
    finally:
        _kill_group(provider)
        _kill_group(unrelated)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group ownership")
def test_reap_dry_run_mutates_nothing_and_live_owner_is_refused(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    provider = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    try:
        _register_abandoned_group(registry, provider)
        path = registry.active_dir / "abandoned.json"
        before = path.read_bytes()
        result = registry.reap_run("abandoned", dry_run=True)
        assert result["action"] == "dry_run"
        assert result["calls"][0]["action"] == "would_reap"
        assert path.read_bytes() == before
        assert _alive(provider.pid)

        # Restoring the real live owner must make even a non-dry run refuse.
        registry.update("abandoned", lambda manifest: {
            **manifest,
            "owner": process_identity(os.getpid()),
        })
        refused = registry.reap_run("abandoned")
        assert refused["action"] == "refused"
        assert refused["classification"] == "active"
        assert _alive(provider.pid)
    finally:
        _kill_group(provider)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group ownership")
def test_reaper_refuses_pid_start_mismatch_without_signalling(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    provider = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    try:
        _register_abandoned_group(registry, provider)

        def reuse(manifest):
            manifest["calls"]["call1"]["child"]["start_fingerprint"] = "reused-pid"
            return manifest

        registry.update("abandoned", reuse)
        result = registry.reap_run("abandoned")
        assert result["action"] == "refused"
        assert result["calls"][0]["result"] == "identity_mismatch"
        assert _alive(provider.pid)
        assert (registry.active_dir / "abandoned.json").exists()
    finally:
        _kill_group(provider)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group ownership")
def test_reaper_preserves_rejected_symlink_and_outside_path(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    work = registry.run_work_dir("abandoned")
    owned = work / "owned.txt"
    owned.write_text("remove", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    link = work / "outside-link"
    link.symlink_to(outside)
    provider = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    try:
        _register_abandoned_group(
            registry,
            provider,
            owned_paths=[str(owned), str(link), str(outside)],
        )
        result = registry.reap_run("abandoned")
        assert result["action"] == "cleanup_failed"
        assert _wait_dead([provider.pid])
        provider.wait(timeout=5)
        assert not owned.exists()
        assert link.is_symlink()
        assert outside.read_text(encoding="utf-8") == "keep"
        manifest = registry.load_active("abandoned")
        assert manifest["cleanup_result"] == "cleanup_failed"
        assert registry.classify(manifest) == "cleanup_failed"
        path_results = {item["path"]: item["result"] for item in result["cleanup"]}
        assert path_results[str(link)] == "cleanup_path_rejected"
        assert path_results[str(outside)] == "cleanup_path_rejected"
    finally:
        _kill_group(provider)


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent/supervisor crash recovery")
def test_parent_and_supervisor_sigkill_is_repaired_by_reconcile(tmp_path):
    registry_root = tmp_path / "runs"
    tree_file = tmp_path / "tree.json"
    script = tmp_path / "owner.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from lope.jobs import RunRegistry\n"
        "from lope.processes import run_subprocess_group\n"
        "from lope.runtime import InvocationContext, RunBudget\n"
        "registry = RunRegistry(Path(sys.argv[1]))\n"
        "budget = RunBudget(mode='ask', run_timeout=60, run_id='crash-run')\n"
        "registry.start_run('ask', run_id='crash-run', run_timeout=60)\n"
        "context = InvocationContext(budget=budget, mode='ask', metadata={'registry': registry})\n"
        "lease = budget.reserve_call(stage='fanout', validator='fixture', prompt='x', requested_timeout=60)\n"
        "context.register_lease(lease)\n"
        "call_context = context.child(validator='fixture', metadata={'call_id': lease.record.call_id})\n"
        f"run_subprocess_group([sys.executable, {str(FIXTURES / 'ignore_term_tree.py')!r}, sys.argv[2]], timeout=60, context=call_context)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    owner = subprocess.Popen(
        [sys.executable, str(script), str(registry_root), str(tree_file)],
        cwd=str(ROOT), env=env, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    provider_pids: List[int] = []
    supervisor_pid = 0
    try:
        registry = RunRegistry(registry_root)
        deadline = time.monotonic() + 10
        manifest = None
        while time.monotonic() < deadline:
            try:
                manifest = registry.load_active("crash-run")
                call = next(iter((manifest.get("calls") or {}).values()))
                supervisor_pid = int((call.get("supervisor") or {}).get("pid") or 0)
                child_pid = int((call.get("child") or {}).get("pid") or 0)
                if supervisor_pid and child_pid and tree_file.exists():
                    break
            except (OSError, RegistryError, StopIteration):
                pass
            time.sleep(0.05)
        assert manifest is not None and supervisor_pid and tree_file.exists()
        tree = json.loads(tree_file.read_text(encoding="utf-8"))
        provider_pids = [int(tree["child"]), int(tree["grandchild"])]
        assert all(_alive(pid) for pid in provider_pids)

        # Kill supervisor first while owner keeps control pipe open, then owner.
        os.kill(supervisor_pid, signal.SIGKILL)
        os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _alive(supervisor_pid):
            time.sleep(0.05)

        results = registry.reconcile()
        assert results and results[0]["action"] == "reaped"
        assert _wait_dead(provider_pids)
        assert not (registry.active_dir / "crash-run.json").exists()
        assert not (registry.work_dir / "crash-run").exists()
    finally:
        if owner.poll() is None:
            _kill_group(owner)
        for pid in provider_pids + ([supervisor_pid] if supervisor_pid else []):
            if _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX verified owner/group kill")
def test_jobs_kill_cli_terminates_verified_owner_and_owned_provider_only(tmp_path):
    home = tmp_path / "home"
    pid_file = tmp_path / "owned-provider.pid"
    owner_script = tmp_path / "owned-run.py"
    owner_script.write_text(
        "import hashlib, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "from lope.jobs import RunRegistry, process_identity\n"
        "registry = RunRegistry()\n"
        "registry.start_run('ask', run_id='killable', run_timeout=120)\n"
        "provider = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)\n"
        "registry.register_call('killable', {\n"
        "  'call_id': 'call1', 'validator': 'fixture', 'stage': 'fanout',\n"
        "  'state': 'active', 'child': process_identity(provider.pid),\n"
        "  'pgid': os.getpgid(provider.pid),\n"
        "  'ownership_marker_hash': hashlib.sha256(b'owned-fixture').hexdigest(),\n"
        "  'owned_paths': [], 'cleanup_result': 'pending',\n"
        "})\n"
        "Path(sys.argv[1]).write_text(str(provider.pid))\n"
        "while True:\n"
        "  registry.heartbeat('killable', source='owner')\n"
        "  time.sleep(0.2)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["LOPE_HOME"] = str(home)
    owner = subprocess.Popen(
        [sys.executable, str(owner_script), str(pid_file)],
        cwd=str(ROOT),
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    provider_pid = 0
    try:
        deadline = time.monotonic() + 10
        registry = RunRegistry(home / "runs")
        while time.monotonic() < deadline:
            if pid_file.exists() and (registry.active_dir / "killable.json").exists():
                provider_pid = int(pid_file.read_text())
                break
            time.sleep(0.05)
        assert provider_pid > 0
        assert _alive(owner.pid) and _alive(provider_pid) and _alive(unrelated.pid)

        result = subprocess.run(
            [sys.executable, "-m", "lope", "jobs", "kill", "killable", "--json"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["result"]["action"] == "killed"
        assert _wait_dead([owner.pid, provider_pid])
        owner.wait(timeout=5)
        assert _alive(unrelated.pid), "unregistered same-name process was touched"
        assert not (registry.active_dir / "killable.json").exists()
    finally:
        _kill_group(owner)
        if provider_pid and _alive(provider_pid):
            try:
                os.killpg(provider_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _kill_group(unrelated)
