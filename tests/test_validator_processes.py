"""Tests for lope.processes — safe subprocess runner with process-group kills.

Verifies that on timeout / explicit kill, the entire process tree
(child + grandchildren) is cleaned up. This is the regression test
for the orphan OpenCode child process leak (SPRINT BUG 2).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lope.processes import run_subprocess_group, _have_process_groups
from lope.runtime import InvocationContext, RunBudget

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ─── Unit: _have_process_groups ────────────────────────────────────


def test_have_process_groups_on_mac_linux():
    """On macOS/Linux this should return True."""
    if sys.platform == "win32":
        pytest.skip("process groups not expected on Windows")
    assert _have_process_groups() is True


# ─── Integration: run_subprocess_group with echo ────────────────────


def test_basic_subprocess_returns_stdout():
    proc = run_subprocess_group(["echo", "hello from lope"], timeout=10)
    assert proc.returncode == 0
    assert "hello from lope" in proc.stdout


def test_stdin_piping():
    proc = run_subprocess_group(
        ["python3", "-c", "import sys; print(sys.stdin.read().upper())"],
        input_text="hello stdin",
        timeout=10,
    )
    assert "HELLO STDIN" in proc.stdout


def _implementation_context():
    return InvocationContext(
        budget=RunBudget(mode="implement", run_timeout=30),
        mode="implement",
        metadata={"implementation": True},
    )


def test_implementation_guard_applies_at_central_subprocess_boundary():
    proc = run_subprocess_group(
        [sys.executable, "-m", "lope.cli", "--help"],
        timeout=12,
        context=_implementation_context(),
        env={"LOPE_IMPLEMENTATION_DEPTH": "0"},
    )

    assert proc.returncode == 2
    assert "nested Lope orchestration is disabled" in proc.stderr
    assert "LOPE_IMPLEMENTATION_DEPTH" not in os.environ


def test_concurrent_implementation_guards_do_not_race_or_leak():
    command = [
        sys.executable,
        "-c",
        "import os; print(os.environ.get('LOPE_IMPLEMENTATION_DEPTH', 'missing'))",
    ]

    def run_one(_index):
        return run_subprocess_group(
            command,
            timeout=12,
            context=_implementation_context(),
        ).stdout.strip()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(run_one, range(8))) == ["1"] * 8
    assert "LOPE_IMPLEMENTATION_DEPTH" not in os.environ


def test_timeout_cleans_descendants_ps():
    """End-to-end: spawn a tree via run_subprocess_group, timeout after 1s,
    then check via `ps` that no descendant processes remain."""
    if sys.platform == "win32":
        pytest.skip("process-group semantics are Unix-only")

    spawn_script = str(FIXTURES_DIR / "spawn_tree.py")

    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess_group(
            [sys.executable, spawn_script, "30"],
            timeout=1,
        )

    # Allow kernel to reap
    time.sleep(0.5)

    # Check for stale spawn_tree.py processes
    result = subprocess.run(
        ["ps", "-eo", "command"],
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    # Filter out lines that contain spawn_tree.py but are from ps itself
    # or the pytest runner
    orphans = [
        ln for ln in lines
        if "spawn_tree.py" in ln
        and "grep" not in ln
        and "ps " not in ln
        and "pytest" not in ln
    ]
    assert len(orphans) == 0, (
        f"Found {len(orphans)} orphan spawn_tree processes: {orphans}"
    )


# ─── run_subprocess_group error paths ──────────────────────────────


def test_binary_not_found():
    with pytest.raises(FileNotFoundError):
        run_subprocess_group(
            ["definitely-not-a-binary-xyz-wxyz-999"],
            timeout=10,
        )


def test_nonzero_exit_captured():
    proc = run_subprocess_group(
        ["python3", "-c", "import sys; sys.exit(42)"],
        timeout=10,
    )
    assert proc.returncode == 42
