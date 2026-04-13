"""Tests for lope.runlock — process-level run lock for negotiate/execute.

Covers:
  - acquire() is a context manager that holds a lock for its lifetime
  - Holder info (pid + command) is written into the lockfile
  - A second attempt fails fast (exit 75) when the first holds the lock
  - LOPE_RUN_LOCK=off disables locking entirely
  - LOPE_RUN_LOCK_WAIT=<secs> makes the second caller block then time out
  - Lock is released cleanly after the context exits
  - Reentrant sequential acquires work (first releases, second succeeds)
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lope import runlock


@pytest.fixture(autouse=True)
def isolated_lock(tmp_path, monkeypatch):
    """Point the lockfile at a tmp_path so tests don't touch ~/.lope/run.lock."""
    lock_path = tmp_path / "run.lock"
    monkeypatch.setenv("LOPE_RUN_LOCK_PATH", str(lock_path))
    monkeypatch.delenv("LOPE_RUN_LOCK", raising=False)
    monkeypatch.delenv("LOPE_RUN_LOCK_WAIT", raising=False)
    yield lock_path


def test_acquire_writes_holder_info(isolated_lock):
    with runlock.acquire("negotiate"):
        contents = isolated_lock.read_text()
    assert str(os.getpid()) in contents
    assert "negotiate" in contents


def test_lockfile_is_truncated_after_release(isolated_lock):
    with runlock.acquire("negotiate"):
        pass
    # Lockfile still exists but holder line is gone
    assert isolated_lock.exists()
    assert isolated_lock.read_text().strip() == ""


def test_sequential_acquires_succeed(isolated_lock):
    with runlock.acquire("negotiate"):
        pass
    with runlock.acquire("execute"):
        contents = isolated_lock.read_text()
    assert "execute" in contents


def test_lock_disabled_via_env(isolated_lock, monkeypatch):
    monkeypatch.setenv("LOPE_RUN_LOCK", "off")
    # Both contexts should enter without any file being written
    with runlock.acquire("negotiate"):
        with runlock.acquire("execute"):
            pass
    # Nothing was written because the lock is disabled
    assert not isolated_lock.exists() or isolated_lock.read_text() == ""


def _hold_lock_in_subprocess(lock_path_str: str, hold_seconds: float):
    """Child process: acquire the lock and hold it for `hold_seconds`."""
    os.environ["LOPE_RUN_LOCK_PATH"] = lock_path_str
    os.environ.pop("LOPE_RUN_LOCK", None)
    os.environ.pop("LOPE_RUN_LOCK_WAIT", None)
    with runlock.acquire("negotiate"):
        time.sleep(hold_seconds)


def test_second_caller_fails_fast_when_lock_held(isolated_lock):
    """When LOPE_RUN_LOCK_WAIT is unset, a second caller must exit 75 immediately."""
    ctx = multiprocessing.get_context("fork")
    child = ctx.Process(
        target=_hold_lock_in_subprocess,
        args=(str(isolated_lock), 5.0),
    )
    child.start()
    try:
        # Wait for child to actually acquire the lock
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if isolated_lock.exists() and isolated_lock.read_text().strip():
                break
            time.sleep(0.05)
        else:
            pytest.fail("child never acquired the lock")

        # Our attempt should fail fast with SystemExit(75)
        with pytest.raises(SystemExit) as excinfo:
            with runlock.acquire("execute"):
                pass
        assert excinfo.value.code == 75
    finally:
        child.terminate()
        child.join(timeout=3)


def test_second_caller_waits_and_times_out(isolated_lock, monkeypatch):
    """LOPE_RUN_LOCK_WAIT=<secs> makes the second caller block then time out."""
    ctx = multiprocessing.get_context("fork")
    child = ctx.Process(
        target=_hold_lock_in_subprocess,
        args=(str(isolated_lock), 10.0),
    )
    child.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if isolated_lock.exists() and isolated_lock.read_text().strip():
                break
            time.sleep(0.05)
        else:
            pytest.fail("child never acquired the lock")

        monkeypatch.setenv("LOPE_RUN_LOCK_WAIT", "2")
        start = time.monotonic()
        with pytest.raises(SystemExit) as excinfo:
            with runlock.acquire("execute"):
                pass
        elapsed = time.monotonic() - start
        assert excinfo.value.code == 75
        assert elapsed >= 1.5, f"expected to block ~2s, blocked {elapsed}s"
    finally:
        child.terminate()
        child.join(timeout=3)


def test_second_caller_succeeds_after_first_releases(isolated_lock, monkeypatch):
    """If the first caller releases within the wait window, the second proceeds."""
    ctx = multiprocessing.get_context("fork")
    child = ctx.Process(
        target=_hold_lock_in_subprocess,
        args=(str(isolated_lock), 1.0),  # hold briefly
    )
    child.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if isolated_lock.exists() and isolated_lock.read_text().strip():
                break
            time.sleep(0.05)
        else:
            pytest.fail("child never acquired the lock")

        monkeypatch.setenv("LOPE_RUN_LOCK_WAIT", "10")
        # Should block briefly then succeed when child releases
        with runlock.acquire("execute"):
            contents = isolated_lock.read_text()
            assert "execute" in contents
    finally:
        child.terminate()
        child.join(timeout=3)
