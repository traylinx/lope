"""Killable provider supervisor used by every external Lope invocation.

The supervisor is a separate Python interpreter, not a thread or a fork from a
worker thread.  Its stdin is a parent-liveness/control pipe: the parent writes
one JSON specification line and keeps the pipe open.  EOF means the Lope owner
died, so the supervisor terminates the provider process group, reaps its direct
child, removes transient output where possible, and exits.

Provider stdout/stderr are streamed with hard byte ceilings.  A secure result
file avoids deadlocking on a multi-megabyte supervisor stdout pipe; the parent
deletes it immediately, and the run registry owns it for crash reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


READ_SIZE = 64 * 1024
SUPERVISOR_HEARTBEAT_SECONDS = 5.0
# Keep cancellation bounded tightly: provider groups are already isolated and
# receive SIGKILL after a short TERM grace. A long grace would violate the
# caller's hard timeout and leave the parent waiting on supervisor cleanup.
TERM_GRACE_SECONDS = 0.2
KILL_GRACE_SECONDS = 0.5
TRUNCATION_MARKER = b"\n...[output truncated; head and tail preserved]...\n"


class _BoundedStream:
    """Keep complete output until the cap, then retain bounded head + tail."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.total = 0
        self._data = bytearray()

    def add(self, chunk: bytes) -> bool:
        self.total += len(chunk)
        if self.total <= self.limit:
            self._data.extend(chunk)
            return False
        combined = bytes(self._data) + chunk
        marker = TRUNCATION_MARKER[: self.limit]
        remaining = max(0, self.limit - len(marker))
        head_size = remaining // 2
        tail_size = remaining - head_size
        self._data[:] = (
            combined[:head_size]
            + marker
            + (combined[-tail_size:] if tail_size else b"")
        )
        return True

    def value(self) -> bytes:
        return bytes(self._data[: self.limit])


def _drain_provider_streams(
    selector: selectors.BaseSelector,
    streams_open: set,
    stdout: _BoundedStream,
    stderr: _BoundedStream,
) -> None:
    """Drain already-emitted bytes after cancellation for head/tail diagnostics."""

    deadline = time.monotonic() + 0.5
    while streams_open and time.monotonic() < deadline:
        events = selector.select(timeout=0.05)
        if not events:
            continue
        for key, _mask in events:
            label = key.data
            if label not in ("stdout", "stderr"):
                continue
            fd = int(key.fd)
            try:
                chunk = os.read(fd, READ_SIZE)
            except (BlockingIOError, OSError):
                continue
            if not chunk:
                try:
                    selector.unregister(fd)
                except Exception:
                    pass
                streams_open.discard(label)
                continue
            (stdout if label == "stdout" else stderr).add(chunk)


def _process_group_count(pgid: int) -> int:
    """Best-effort member count used only for cleanup telemetry."""

    if os.name != "posix" or pgid <= 0:
        return 0
    try:
        proc = subprocess.run(
            ["/bin/ps", "-axo", "pgid=,pid=,stat="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    count = 0
    for line in proc.stdout.splitlines():
        fields = line.split()
        if (
            len(fields) >= 3
            and fields[0].isdigit()
            and int(fields[0]) == pgid
            and not fields[2].startswith("Z")
        ):
            count += 1
    return count


def _attach_windows_job(proc: subprocess.Popen) -> str:
    """Attach provider to a kill-on-close Job Object when Windows supports it."""

    if os.name != "nt":
        return "not_applicable"
    try:  # pragma: no cover - exercised by Windows CI
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMITS),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        info = EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
        if not kernel32.AssignProcessToJobObject(handle, int(proc._handle)):  # noqa: SLF001
            kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        proc._lope_job_handle = handle  # type: ignore[attr-defined]  # noqa: SLF001
        return "windows_job"
    except Exception as exc:  # pragma: no cover - explicit degraded contract
        return "cleanup_degraded:" + str(exc)[:160]


def _close_windows_job(proc: subprocess.Popen) -> None:
    handle = getattr(proc, "_lope_job_handle", None)
    if not handle:
        return
    try:  # pragma: no cover - Windows CI
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
    except Exception:
        pass
    proc._lope_job_handle = None  # type: ignore[attr-defined]  # noqa: SLF001


def _write_result(path: str, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = target.with_name("." + target.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(target))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _signal_tree(proc: subprocess.Popen, sig: int) -> str:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, sig)
            return "process_group"
        except ProcessLookupError:
            return "already_exited"
        except OSError as exc:
            return "signal_failed:" + str(exc)[:120]
    if proc.poll() is not None:
        return "already_exited"
    # Windows fallback: taskkill is explicitly tree-aware. If unavailable,
    # report degraded cleanup rather than claiming descendants were killed.
    try:  # pragma: no cover - Windows CI
        taskkill = subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return "windows_tree" if taskkill.returncode == 0 else "cleanup_degraded"
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
        try:
            proc.kill()
        except OSError:
            pass
        return "cleanup_degraded"


def _terminate_and_reap(proc: subprocess.Popen) -> Tuple[str, int]:
    targeted = _process_group_count(proc.pid)
    if proc.poll() is not None:
        try:
            proc.wait(timeout=0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if os.name == "posix" and _process_group_count(proc.pid):
            _signal_tree(proc, signal.SIGKILL)
            deadline = time.monotonic() + KILL_GRACE_SECONDS
            while time.monotonic() < deadline and _process_group_count(proc.pid):
                time.sleep(0.05)
        remaining = _process_group_count(proc.pid)
        return ("clean" if not remaining else "cleanup_failed:descendants_live"), targeted
    term = _signal_tree(proc, signal.SIGTERM if os.name == "posix" else signal.SIGTERM)
    direct_reaped = False
    try:
        proc.wait(timeout=TERM_GRACE_SECONDS)
        direct_reaped = True
    except subprocess.TimeoutExpired:
        pass
    remaining = _process_group_count(proc.pid) if os.name == "posix" else int(not direct_reaped)
    killed = "not_needed"
    if remaining or not direct_reaped:
        kill_sig = signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM
        killed = _signal_tree(proc, kill_sig)
    # A process-group signal should include the leader, but an explicit direct
    # kill closes the last race on platforms/shims with delayed group setup.
    if not direct_reaped:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
        direct_reaped = True
    except subprocess.TimeoutExpired:
        direct_reaped = False
    if os.name == "posix":
        deadline = time.monotonic() + KILL_GRACE_SECONDS
        while time.monotonic() < deadline and _process_group_count(proc.pid):
            time.sleep(0.05)
        remaining = _process_group_count(proc.pid)
    else:
        remaining = int(not direct_reaped)
    if not direct_reaped:
        return "cleanup_failed:child_not_reaped", targeted
    if remaining:
        return "cleanup_failed:descendants_live", targeted
    return "clean:" + (killed if killed != "not_needed" else term), targeted


def _registry_update(spec: Dict[str, Any], **changes: Any) -> None:
    root = spec.get("registry_root")
    run_id = spec.get("run_id")
    call_id = spec.get("call_id")
    if not root or not run_id or not call_id:
        return
    try:
        from .jobs import RunRegistry, process_identity

        registry = RunRegistry(Path(root))
        if "supervisor" not in changes:
            changes["supervisor"] = process_identity(os.getpid())
        registry.update_call(str(run_id), str(call_id), **changes)
    except Exception:
        # Registry failure must not prevent provider cleanup. The parent still
        # receives cleanup_result and can surface the persistence failure.
        return


def _provider_env(spec: Dict[str, Any]) -> Dict[str, str]:
    env = dict(os.environ)
    for key, value in (spec.get("env") or {}).items():
        if value is None:
            env.pop(str(key), None)
        else:
            env[str(key)] = str(value)
    marker = str(spec.get("ownership_marker") or "")
    if marker:
        env["LOPE_PROCESS_MARKER"] = marker
    if spec.get("run_id"):
        env["LOPE_RUN_ID"] = str(spec["run_id"])
    if spec.get("call_id"):
        env["LOPE_CALL_ID"] = str(spec["call_id"])
    return env


def _spawn_provider(spec: Dict[str, Any]) -> subprocess.Popen:
    command = spec.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError("supervisor command must be a non-empty argv list")
    kwargs: Dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": spec.get("cwd") or None,
        "env": _provider_env(spec),
        "shell": False,
        "bufsize": 0,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):  # pragma: no cover
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(command, **kwargs)


def _run_provider(spec: Dict[str, Any], control_fd: int) -> Dict[str, Any]:
    started = time.monotonic()
    timeout = float(spec.get("timeout") or 0.0)
    if timeout <= 0:
        raise ValueError("supervisor timeout must be positive")
    stdout_limit = int(spec.get("stdout_limit") or 2 * 1024 * 1024)
    stderr_limit = int(spec.get("stderr_limit") or 512 * 1024)
    input_bytes = str(spec.get("input_text") or "").encode("utf-8")
    proc = _spawn_provider(spec)
    windows_job = _attach_windows_job(proc)
    marker = str(spec.get("ownership_marker") or "")
    try:
        pgid = os.getpgid(proc.pid) if os.name == "posix" else proc.pid
    except OSError:
        pgid = proc.pid
    child_identity: Dict[str, Any] = {"pid": proc.pid, "start_fingerprint": None}
    try:
        from .jobs import process_identity

        child_identity = process_identity(proc.pid)
    except Exception:
        pass
    _registry_update(
        spec,
        child=child_identity,
        pgid=pgid,
        state="active",
        heartbeat_at=time.time(),
        ownership_marker_hash=hashlib.sha256(marker.encode()).hexdigest() if marker else "",
        owned_paths=list(spec.get("owned_paths") or [str(spec.get("result_path"))]),
    )

    selector = selectors.DefaultSelector()
    stdout = _BoundedStream(stdout_limit)
    stderr = _BoundedStream(stderr_limit)
    input_offset = 0
    outcome = "ok"
    reason = ""
    cleanup = "pending"
    parent_dead = False
    cleanup_attempted = False
    processes_targeted = 0

    os.set_blocking(control_fd, False)
    selector.register(control_fd, selectors.EVENT_READ, "control")
    assert proc.stdout is not None and proc.stderr is not None and proc.stdin is not None
    for stream, label in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream.fileno(), selectors.EVENT_READ, label)
    os.set_blocking(proc.stdin.fileno(), False)
    if input_bytes:
        selector.register(proc.stdin.fileno(), selectors.EVENT_WRITE, "stdin")
    else:
        proc.stdin.close()

    deadline = started + timeout
    next_heartbeat = started + SUPERVISOR_HEARTBEAT_SECONDS
    streams_open = {"stdout", "stderr"}
    try:
        while True:
            now = time.monotonic()
            if now >= next_heartbeat:
                _registry_update(
                    spec,
                    state="active",
                    heartbeat_at=time.time(),
                    heartbeat_source="supervisor",
                )
                next_heartbeat = now + SUPERVISOR_HEARTBEAT_SECONDS
            if now >= deadline:
                outcome = "provider_timeout"
                reason = f"provider timed out after {timeout:g}s"
                cleanup, processes_targeted = _terminate_and_reap(proc)
                cleanup_attempted = True
                break
            if proc.poll() is not None and not streams_open:
                cleanup = "clean"
                break
            events = selector.select(timeout=min(0.1, max(0.0, deadline - now)))
            for key, _mask in events:
                label = key.data
                fd = int(key.fd)
                if label == "control":
                    try:
                        chunk = os.read(fd, READ_SIZE)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        parent_dead = True
                        outcome = "cancelled"
                        reason = "parent control pipe closed"
                        cleanup, processes_targeted = _terminate_and_reap(proc)
                        cleanup_attempted = True
                        break
                    if b"cancel" in chunk.lower():
                        outcome = "cancelled"
                        reason = "parent requested cancellation"
                        cleanup, processes_targeted = _terminate_and_reap(proc)
                        cleanup_attempted = True
                        break
                elif label == "stdin":
                    try:
                        written = os.write(fd, input_bytes[input_offset:input_offset + READ_SIZE])
                    except BlockingIOError:
                        continue
                    except (BrokenPipeError, OSError):
                        written = 0
                        input_offset = len(input_bytes)
                    input_offset += written
                    if input_offset >= len(input_bytes):
                        try:
                            selector.unregister(fd)
                        except Exception:
                            pass
                        try:
                            proc.stdin.close()
                        except OSError:
                            pass
                else:
                    try:
                        chunk = os.read(fd, READ_SIZE)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        try:
                            selector.unregister(fd)
                        except Exception:
                            pass
                        streams_open.discard(label)
                        continue
                    target = stdout if label == "stdout" else stderr
                    limit = stdout_limit if label == "stdout" else stderr_limit
                    if target.add(chunk):
                        outcome = "output_limit"
                        reason = f"{label} exceeded {limit} bytes"
                        cleanup, processes_targeted = _terminate_and_reap(proc)
                        cleanup_attempted = True
                        break
            if outcome != "ok":
                break

        if proc.poll() is None and not cleanup_attempted:
            cleanup, processes_targeted = _terminate_and_reap(proc)
        else:
            try:
                proc.wait(timeout=0)
            except (OSError, subprocess.TimeoutExpired):
                cleanup = "cleanup_failed:wait"
        if outcome in ("provider_timeout", "cancelled", "output_limit"):
            _drain_provider_streams(selector, streams_open, stdout, stderr)
        returncode = proc.returncode if proc.returncode is not None else -9
        if outcome == "ok" and returncode != 0:
            outcome = "nonzero_exit"
            reason = f"provider exited {returncode}"
        if windows_job.startswith("cleanup_degraded"):
            cleanup = windows_job
        result = {
            "returncode": returncode,
            "stdout": stdout.value().decode("utf-8", errors="replace"),
            "stderr": stderr.value().decode("utf-8", errors="replace"),
            "outcome": outcome,
            "reason": reason,
            "cleanup_result": cleanup,
            "elapsed_seconds": time.monotonic() - started,
            "child_pid": proc.pid,
            "pgid": pgid,
            "parent_dead": parent_dead,
            "processes_targeted": processes_targeted,
            "platform_cleanup": windows_job,
        }
        _registry_update(
            spec,
            state="finished",
            outcome=outcome,
            reason=reason,
            cleanup_result=cleanup,
            ended_at=time.time(),
            processes_targeted=processes_targeted,
            platform_cleanup=windows_job,
        )
        return result
    finally:
        selector.close()
        _close_windows_job(proc)


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m lope.supervisor RESULT_PATH", file=sys.stderr)
        return 2
    result_path = args[0]
    try:
        spec_line = sys.stdin.buffer.readline()
        if not spec_line:
            return 3
        spec = json.loads(spec_line.decode("utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("spec must be an object")
        spec["result_path"] = result_path
        result = _run_provider(spec, sys.stdin.fileno())
        if not result.get("parent_dead"):
            _write_result(result_path, result)
        else:
            try:
                Path(result_path).unlink()
            except OSError:
                pass
            try:
                parent = Path(result_path).parent
                if parent.name.startswith("lope-call-"):
                    parent.rmdir()
            except OSError:
                pass
        return 0
    except BaseException as exc:
        try:
            _write_result(result_path, {
                "returncode": -1,
                "stdout": "",
                "stderr": "",
                "outcome": "launch_error",
                "reason": f"{type(exc).__name__}: {exc}"[:500],
                "cleanup_result": "unknown",
                "elapsed_seconds": 0.0,
            })
        except Exception:
            pass
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())
