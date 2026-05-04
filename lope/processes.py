"""
Safe subprocess runner for validator backends.

Runs every validator child in its own process group (Unix) so that on
timeout the entire process tree — not just the direct child — is killed.
Prevents the orphan process leak observed when OpenCode spawns nested
`.opencode` children that survive a parent timeout.

Works on macOS / Linux with process-group semantics; falls back to
plain `subprocess` kill on Windows (non-group-aware).

Usage:
    from lope.processes import run_subprocess_group

    try:
        proc = run_subprocess_group(
            ["opencode", "run", "--format", "json"],
            input_text=prompt,
            timeout=120,
            cwd="/some/dir",
        )
    except subprocess.TimeoutExpired:
        # Process group already killed — no orphans remain
        ...
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import List, Optional


def _have_process_groups() -> bool:
    """True on platforms that support os.setsid / os.killpg."""
    return hasattr(os, "setsid") and hasattr(signal, "SIGTERM")


def run_subprocess_group(
    command: List[str],
    input_text: Optional[str] = None,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    """Run *command* in its own process group; kill descendants on timeout.

    Args:
        command: Argv list (shell=False always).
        input_text: Text piped to stdin, or None.
        timeout: Seconds before SIGTERM→SIGKILL escalation.
        cwd: Working directory for the child.

    Returns:
        CompletedProcess with stdout / stderr as strings.

    Raises:
        subprocess.TimeoutExpired: after SIGTERM + SIGKILL on process group.
        FileNotFoundError: if the binary doesn't exist.
    """
    kwargs: dict = dict(
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )

    if _have_process_groups():
        kwargs["preexec_fn"] = os.setsid  # type: ignore[attr-defined]

    with subprocess.Popen(command, **kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            stdout, stderr = proc.communicate()
            raise subprocess.TimeoutExpired(command, timeout, stdout, stderr)

    return subprocess.CompletedProcess(
        args=command,
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGTERM the process group of *proc*, wait 2s, then SIGKILL.

    Only active on POSIX (macOS/Linux). On Windows, falls back to
    `proc.kill()` which kills only the direct child.
    """
    if not _have_process_groups():
        proc.kill()
        return

    pid = proc.pid
    if pid is None:
        return

    sigterm = signal.SIGTERM  # type: ignore[attr-defined]
    sigkill = signal.SIGKILL  # type: ignore[attr-defined]

    # Wave 1: graceful shutdown
    _safe_killpg(pid, sigterm)
    try:
        proc.wait(timeout=2)
        return  # child tree exited cleanly
    except subprocess.TimeoutExpired:
        pass

    # Wave 2: force kill
    _safe_killpg(pid, sigkill)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _safe_killpg(pgid: int, sig: int) -> None:
    """Send *sig* to process group *pgid*, ignoring missing groups."""
    try:
        os.killpg(pgid, sig)  # type: ignore[attr-defined]
    except (ProcessLookupError, OSError):
        pass
