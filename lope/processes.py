"""Universal cancellation-safe subprocess boundary.

Every model/provider/gate/helper subprocess routes through a separate
``lope.supervisor`` interpreter.  Parent death is observable as control-pipe
EOF, provider descendants live in their own process group, stdout/stderr are
bounded, and timeout includes supervisor startup through an absolute monotonic
deadline.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_STDOUT_LIMIT = 2 * 1024 * 1024
DEFAULT_STDERR_LIMIT = 512 * 1024
SUPERVISOR_CLEANUP_RESERVE = 8.0
ARGV_POLICY_LIMIT = 128 * 1024
ARG_MAX_RESERVE = 32 * 1024


class OutputLimitExceeded(RuntimeError):
    def __init__(self, stream: str, limit: int, stdout: str = "", stderr: str = ""):
        super().__init__(f"{stream} exceeded {limit} bytes")
        self.stream = stream
        self.limit = limit
        self.stdout = stdout
        self.stderr = stderr


class InputLimitExceeded(RuntimeError):
    pass


def _have_process_groups() -> bool:
    return os.name == "posix" and hasattr(os, "killpg")


def _check_argv_size(command: List[str], env_overrides: Optional[Dict[str, Optional[str]]]) -> None:
    argv_bytes = sum(len(item.encode("utf-8")) + 1 for item in command)
    if argv_bytes > ARGV_POLICY_LIMIT:
        raise InputLimitExceeded(
            f"argv payload {argv_bytes} bytes exceeds Lope policy limit {ARGV_POLICY_LIMIT}; "
            "use stdin, a prompt file, chunking, or rejection before launch"
        )
    environment = dict(os.environ)
    for key, value in (env_overrides or {}).items():
        if value is None:
            environment.pop(str(key), None)
        else:
            environment[str(key)] = str(value)
    env_bytes = sum(
        len(str(key).encode("utf-8")) + len(str(value).encode("utf-8")) + 2
        for key, value in environment.items()
    )
    try:
        arg_max = int(os.sysconf("SC_ARG_MAX"))
    except (AttributeError, OSError, ValueError):
        arg_max = 1024 * 1024
    if argv_bytes + env_bytes > max(1, arg_max - ARG_MAX_RESERVE):
        raise InputLimitExceeded(
            f"environment plus argv requires {argv_bytes + env_bytes} bytes; "
            f"safe exec limit is {max(1, arg_max - ARG_MAX_RESERVE)} bytes"
        )


def _call_workspace(context) -> tuple:
    registry = None
    run_id = ""
    call_id = ""
    if context is not None:
        registry = context.metadata.get("registry")
        run_id = context.run_id
        call_id = str(context.metadata.get("call_id") or "")
    if registry is not None and run_id and call_id:
        base = registry.run_work_dir(run_id) / call_id
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            base.chmod(0o700)
        except OSError:
            pass
        return base, registry, run_id, call_id, False
    base = Path(tempfile.mkdtemp(prefix="lope-call-"))
    try:
        base.chmod(0o700)
    except OSError:
        pass
    return base, None, "", uuid.uuid4().hex, True


def _kill_supervisor(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if _have_process_groups():
            os.killpg(proc.pid, signal.SIGTERM)
        else:  # pragma: no cover - Windows
            proc.terminate()
        proc.wait(timeout=2)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if _have_process_groups():
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover
            proc.kill()
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_subprocess_group(
    command: List[str],
    input_text: Optional[str] = None,
    timeout: Optional[float] = None,
    cwd: Optional[str] = None,
    *,
    env: Optional[Dict[str, Optional[str]]] = None,
    stdout_limit: int = DEFAULT_STDOUT_LIMIT,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
    context=None,
) -> subprocess.CompletedProcess:
    """Run an argv command behind the orphan-safe supervisor.

    ``timeout`` is a total wall deadline covering supervisor startup, provider
    launch, input write, response read, and cleanup.  It raises
    :class:`subprocess.TimeoutExpired` only after the provider group has been
    terminated/reaped by the supervisor, or the supervisor itself has been
    force-stopped as a final defensive fallback.
    """

    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError("command must be a non-empty argv list")
    if timeout is None or float(timeout) <= 0:
        raise ValueError("timeout must be positive")
    if stdout_limit <= 0 or stderr_limit <= 0:
        raise ValueError("output limits must be positive")
    _check_argv_size(command, env)

    started = time.monotonic()
    call_deadline = started + float(timeout)
    base, registry, run_id, call_id, temporary = _call_workspace(context)
    result_path = base / "supervisor-result.json"
    marker = uuid.uuid4().hex
    context_owned_paths = (
        list(context.metadata.get("owned_paths") or []) if context is not None else []
    )
    if registry is not None:
        try:
            registry.update_call(
                run_id,
                call_id,
                state="supervising",
                owned_paths=context_owned_paths + [str(result_path)],
                command_hash=__import__("hashlib").sha256(
                    "\0".join([command[0], "<redacted-args>"]).encode()
                ).hexdigest(),
                executable_hash=__import__("hashlib").sha256(command[0].encode()).hexdigest(),
            )
        except Exception:
            pass

    spec = {
        "command": list(command),
        "input_text": input_text or "",
        "timeout": float(timeout),
        "cwd": cwd,
        "env": env or {},
        "stdout_limit": int(stdout_limit),
        "stderr_limit": int(stderr_limit),
        "ownership_marker": marker,
        "registry_root": str(registry.root) if registry is not None else "",
        "run_id": run_id,
        "call_id": call_id,
        "owned_paths": context_owned_paths + [str(result_path)],
    }
    supervisor_cmd = [sys.executable, "-m", "lope.supervisor", str(result_path)]
    kwargs = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        # Import the same Lope copy even when the provider itself runs in an
        # arbitrary project working directory.
        "cwd": str(Path(__file__).resolve().parent.parent),
        "env": dict(os.environ),
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):  # pragma: no cover
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    supervisor = subprocess.Popen(supervisor_cmd, **kwargs)
    try:
        assert supervisor.stdin is not None
        wire = (json.dumps(spec, separators=(",", ":")) + "\n").encode("utf-8")
        wire_complete = True
        if os.name == "posix":
            fd = supervisor.stdin.fileno()
            os.set_blocking(fd, False)
            offset = 0
            while offset < len(wire) and time.monotonic() < call_deadline:
                _readable, writable, _errors = select.select(
                    [], [fd], [], min(0.1, max(0.0, call_deadline - time.monotonic()))
                )
                if not writable:
                    continue
                try:
                    offset += os.write(fd, wire[offset:offset + 64 * 1024])
                except (BlockingIOError, BrokenPipeError):
                    continue
            wire_complete = offset == len(wire)
        else:  # pragma: no cover - Windows pipe fallback
            supervisor.stdin.write(wire)
            supervisor.stdin.flush()
        while wire_complete and supervisor.poll() is None and time.monotonic() < call_deadline:
            time.sleep(0.02)
        parent_timed_out = not wire_complete or supervisor.poll() is None
        if supervisor.poll() is None:
            try:
                supervisor.stdin.write(b"cancel\n")
                supervisor.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                supervisor.wait(timeout=SUPERVISOR_CLEANUP_RESERVE)
            except subprocess.TimeoutExpired:
                _kill_supervisor(supervisor)
        try:
            supervisor.stdin.close()
        except OSError:
            pass

        if not result_path.is_file():
            raise subprocess.TimeoutExpired(command, timeout)
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid supervisor result: {exc}") from exc

        if os.environ.get("LOPE_DEBUG_SUPERVISOR") == "1":  # pragma: no cover - diagnostics
            print(f"lope supervisor result: {result}", file=sys.stderr)

        outcome = result.get("outcome")
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        reason = str(result.get("reason") or "")
        if parent_timed_out or outcome in ("provider_timeout", "cancelled"):
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
        if outcome == "output_limit":
            stream = "stderr" if "stderr" in reason else "stdout"
            limit = stderr_limit if stream == "stderr" else stdout_limit
            raise OutputLimitExceeded(stream, limit, stdout=stdout, stderr=stderr)
        if outcome == "launch_error":
            if "FileNotFoundError" in reason or "No such file" in reason:
                raise FileNotFoundError(reason)
            raise OSError(reason or "provider launch failed")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=int(result.get("returncode", -1)),
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        try:
            result_path.unlink()
        except OSError:
            pass
        if temporary:
            shutil.rmtree(str(base), ignore_errors=True)
        else:
            try:
                base.rmdir()
            except OSError:
                pass


__all__ = [
    "DEFAULT_STDERR_LIMIT",
    "DEFAULT_STDOUT_LIMIT",
    "InputLimitExceeded",
    "OutputLimitExceeded",
    "run_subprocess_group",
]
