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
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lope.processes import run_subprocess_group, _have_process_groups

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
        l for l in lines
        if "spawn_tree.py" in l
        and "grep" not in l
        and "ps " not in l
        and "pytest" not in l
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
