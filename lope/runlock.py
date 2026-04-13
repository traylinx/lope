"""Process-level run lock for lope commands that spawn CLI subprocesses.

Prevents two `lope negotiate` or `lope execute` invocations from fighting
over the same CLI auth tokens at the same time. Without this, N parallel
runs spawn N*V validator subprocesses that block each other on the
claude/gemini/vibe/opencode auth servers, and the whole ensemble stalls.

Uses fcntl.flock on a single lockfile. The pattern is the same as
frozen_memory.py in HARVEY/harvey-os/core/memory — shared POSIX file lock,
released automatically on process exit.

Behavior:
- Held for the lifetime of a `lope negotiate` or `lope execute` command
- Default: fail fast with a clear error if another lope process holds it
- Opt-in: set LOPE_RUN_LOCK_WAIT=<seconds> to block instead (0 = wait forever)
- Disable entirely: set LOPE_RUN_LOCK=off (escape hatch for tests + CI)
"""

from __future__ import annotations

import errno
import fcntl
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


def _lockfile_path() -> Path:
    """Canonical lockfile path. Override via LOPE_RUN_LOCK_PATH for tests."""
    override = os.environ.get("LOPE_RUN_LOCK_PATH")
    if override:
        return Path(override)
    home = Path(os.environ.get("LOPE_HOME") or Path.home() / ".lope")
    return home / "run.lock"


def _read_holder(path: Path) -> str:
    try:
        return path.read_text().strip() or "unknown"
    except Exception:
        return "unknown"


@contextmanager
def acquire(command: str):
    """Hold the lope run lock for the lifetime of a command.

    `command` is a short label ("negotiate", "execute") written into the
    lockfile so the next caller can see who's holding it.
    """
    if os.environ.get("LOPE_RUN_LOCK", "").lower() == "off":
        yield
        return

    path = _lockfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)

    wait_env = os.environ.get("LOPE_RUN_LOCK_WAIT")
    deadline: Optional[float] = None
    if wait_env is not None:
        try:
            secs = float(wait_env)
        except ValueError:
            secs = 0.0
        deadline = None if secs == 0 else time.monotonic() + secs

    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if wait_env is None:
                    holder = _read_holder(path)
                    print(
                        f"\nlope {command}: another lope run is already active "
                        f"(holder: {holder}).\n"
                        f"  Either wait for it to finish, or kill it:\n"
                        f"    pkill -f 'python3 -m lope'\n"
                        f"  To queue instead of failing, set LOPE_RUN_LOCK_WAIT=0 "
                        f"(wait forever) or LOPE_RUN_LOCK_WAIT=300 (5 min).\n"
                        f"  To disable locking entirely: LOPE_RUN_LOCK=off\n",
                        file=sys.stderr,
                    )
                    os.close(fd)
                    sys.exit(75)  # EX_TEMPFAIL
                if deadline is not None and time.monotonic() >= deadline:
                    holder = _read_holder(path)
                    print(
                        f"\nlope {command}: timed out waiting for run lock "
                        f"after {wait_env}s (holder: {holder}).\n",
                        file=sys.stderr,
                    )
                    os.close(fd)
                    sys.exit(75)
                time.sleep(1.0)

        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} {command}\n".encode())
        os.fsync(fd)
        try:
            yield
        finally:
            try:
                os.ftruncate(fd, 0)
            except OSError:
                pass
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
