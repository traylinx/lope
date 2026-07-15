"""Durable, privacy-safe ownership registry for Lope runs and calls.

The registry is deliberately not a scheduler.  It records enough identity to
clean Lope-owned work after crashes without ever falling back to ``pkill`` or
command-name matching.  Provider supervision and signalling live in
``lope.supervisor``; this module owns state, liveness classification, and safe
scratch-path cleanup.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:  # pragma: no cover - Windows fallback exercised by platform CI
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore


REGISTRY_SCHEMA_VERSION = 1
COMPLETED_RETENTION_SECONDS = 7 * 24 * 60 * 60
COMPLETED_RETENTION_COUNT = 1000

_FORBIDDEN_MANIFEST_KEYS = {
    "prompt",
    "prompt_text",
    "response",
    "response_body",
    "raw_response",
    "secret",
    "credentials",
    "argv",
    "environment",
    "env",
}


class RegistryError(RuntimeError):
    pass


def lope_home() -> Path:
    return Path(os.environ.get("LOPE_HOME") or Path.home() / ".lope").expanduser()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def process_start_fingerprint(pid: int) -> Optional[str]:
    """Return a stable-per-process start fingerprint, or ``None`` if dead.

    Linux exposes the kernel start tick in ``/proc/<pid>/stat``.  POSIX hosts
    without procfs (notably macOS) use the full ``ps lstart`` value.  PID alone
    is never treated as identity.
    """

    if pid <= 0:
        return None
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        raw = stat_path.read_text(encoding="utf-8")
        # The comm field may contain spaces/parentheses. Everything after the
        # last ')' begins at field 3; starttime is field 22 => offset 19.
        tail = raw.rsplit(")", 1)[1].strip().split()
        return "proc:" + tail[19]
    except (OSError, IndexError):
        pass
    ps_bin = shutil.which("ps")
    if ps_bin is None:
        for candidate in ("/bin/ps", "/usr/bin/ps"):
            if Path(candidate).is_file():
                ps_bin = candidate
                break
    if ps_bin is None:
        return None
    try:
        proc = subprocess.run(
            [ps_bin, "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = proc.stdout.strip()
    return "ps:" + " ".join(value.split()) if proc.returncode == 0 and value else None


def boot_fingerprint() -> str:
    """Hash host + boot identity so manifests cannot cross reboot silently."""

    boot = ""
    for candidate in (Path("/proc/sys/kernel/random/boot_id"),):
        try:
            boot = candidate.read_text(encoding="utf-8").strip()
            if boot:
                break
        except OSError:
            continue
    if not boot:
        boot = process_start_fingerprint(1) or platform.platform()
    return _sha256_text("|".join((socket.gethostname(), boot)))


def process_identity(pid: int) -> Dict[str, Any]:
    return {
        "pid": int(pid),
        "start_fingerprint": process_start_fingerprint(int(pid)),
    }


def identity_matches(identity: Optional[Dict[str, Any]]) -> bool:
    if not identity or not isinstance(identity, dict):
        return False
    try:
        pid = int(identity.get("pid", 0))
    except (TypeError, ValueError):
        return False
    expected = identity.get("start_fingerprint")
    if pid <= 0 or not expected:
        return False
    return process_start_fingerprint(pid) == expected


def _validate_manifest_value(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key).lower()
            if key_text in _FORBIDDEN_MANIFEST_KEYS:
                raise RegistryError(f"manifest field {path + key_text!r} is forbidden")
            _validate_manifest_value(nested, path + str(key) + ".")
    elif isinstance(value, list):
        for item in value:
            _validate_manifest_value(item, path)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise RegistryError(f"manifest value at {path or '<root>'} is not JSON-safe")


class RunRegistry:
    """Atomic run/call manifest store under ``$LOPE_HOME/runs``."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else lope_home() / "runs"
        self.active_dir = self.root / "active"
        self.completed_dir = self.root / "completed"
        self.work_dir = self.root / "work"
        self.lock_path = self.root / "registry.lock"
        for directory in (self.root, self.active_dir, self.completed_dir, self.work_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                directory.chmod(0o700)
            except OSError:
                pass

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)

    def _active_path(self, run_id: str) -> Path:
        if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id):
            raise RegistryError("invalid run_id")
        return self.active_dir / (run_id + ".json")

    def run_work_dir(self, run_id: str) -> Path:
        self._active_path(run_id)  # validate
        path = self.work_dir / run_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass
        return path

    def _atomic_write(self, path: Path, payload: Dict[str, Any]) -> None:
        _validate_manifest_value(payload)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp_name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=str(path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _read(path: Path) -> Dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"invalid run manifest {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RegistryError(f"invalid run manifest {path}: expected object")
        return value

    def start_run(
        self,
        mode: str,
        *,
        run_id: Optional[str] = None,
        run_timeout: Optional[float] = None,
        owner_pid: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        rid = run_id or uuid.uuid4().hex
        owner = process_identity(owner_pid or os.getpid())
        if not owner.get("start_fingerprint"):
            raise RegistryError("cannot fingerprint run owner")
        work = self.run_work_dir(rid)
        manifest: Dict[str, Any] = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "run_id": rid,
            "mode": mode,
            "state": "active",
            "host_boot_fingerprint": boot_fingerprint(),
            "owner": owner,
            "started_at": now,
            "heartbeat_at": now,
            "deadline_at": None if run_timeout is None else now + float(run_timeout),
            "calls": {},
            "call_counters": {"planned": 0, "started": 0, "finished": 0},
            "work_dir": str(work),
            "cleanup_history": [],
        }
        with self.locked():
            path = self._active_path(rid)
            if path.exists():
                raise RegistryError(f"run already exists: {rid}")
            self._atomic_write(path, manifest)
        return manifest

    def load_active(self, run_id: str) -> Dict[str, Any]:
        return self._read(self._active_path(run_id))

    def update(self, run_id: str, mutator) -> Dict[str, Any]:
        with self.locked():
            path = self._active_path(run_id)
            manifest = self._read(path)
            updated = mutator(manifest) or manifest
            if updated.get("run_id") != run_id:
                raise RegistryError("mutator cannot change run_id")
            self._atomic_write(path, updated)
            return updated

    def heartbeat(self, run_id: str, *, source: str = "owner") -> Dict[str, Any]:
        def mutate(manifest: Dict[str, Any]) -> Dict[str, Any]:
            manifest["heartbeat_at"] = time.time()
            manifest["heartbeat_source"] = source
            return manifest

        return self.update(run_id, mutate)

    def register_call(self, run_id: str, call: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "call_id", "validator", "stage", "state", "supervisor", "child",
            "pgid", "started_at", "heartbeat_at", "deadline_at", "transport",
            "ownership_marker_hash", "executable_hash", "command_hash",
            "owned_paths", "cleanup_result", "outcome", "ended_at", "reason",
        }
        forbidden = {str(key).lower() for key in call} & _FORBIDDEN_MANIFEST_KEYS
        if forbidden:
            raise RegistryError("forbidden call manifest fields: " + ", ".join(sorted(forbidden)))
        unknown = set(call) - allowed
        if unknown:
            raise RegistryError("unsupported call manifest fields: " + ", ".join(sorted(unknown)))
        call_id = str(call.get("call_id") or "")
        if not call_id:
            raise RegistryError("call_id is required")

        def mutate(manifest: Dict[str, Any]) -> Dict[str, Any]:
            calls = manifest.setdefault("calls", {})
            calls[call_id] = dict(call)
            counters = manifest.setdefault("call_counters", {})
            counters["started"] = int(counters.get("started", 0)) + 1
            manifest["heartbeat_at"] = time.time()
            return manifest

        return self.update(run_id, mutate)

    def update_call(self, run_id: str, call_id: str, **changes: Any) -> Dict[str, Any]:
        def mutate(manifest: Dict[str, Any]) -> Dict[str, Any]:
            calls = manifest.setdefault("calls", {})
            if call_id not in calls:
                raise RegistryError(f"unknown call_id: {call_id}")
            calls[call_id].update(changes)
            calls[call_id]["heartbeat_at"] = time.time()
            manifest["heartbeat_at"] = time.time()
            return manifest

        return self.update(run_id, mutate)

    def finish_run(
        self,
        run_id: str,
        *,
        state: str = "completed",
        reason: str = "",
        cleanup_result: str = "clean",
    ) -> Path:
        with self.locked():
            active = self._active_path(run_id)
            manifest = self._read(active)
            manifest.update({
                "state": state,
                "ended_at": time.time(),
                "reason": reason,
                "cleanup_result": cleanup_result,
            })
            destination = self.completed_dir / f"{int(time.time())}-{run_id}.json"
            self._atomic_write(destination, manifest)
            active.unlink()
        self.prune_completed()
        return destination

    def list_active(self, *, include_invalid: bool = True) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for path in sorted(self.active_dir.glob("*.json")):
            try:
                manifest = self._read(path)
                manifest["classification"] = self.classify(manifest)
                manifest["manifest_path"] = str(path)
                out.append(manifest)
            except RegistryError as exc:
                if include_invalid:
                    out.append({
                        "state": "invalid",
                        "classification": "ownership_unverified",
                        "manifest_path": str(path),
                        "reason": str(exc),
                    })
        return out

    def classify(self, manifest: Dict[str, Any], *, stale_after: float = 45.0) -> str:
        if manifest.get("host_boot_fingerprint") != boot_fingerprint():
            return "abandoned"
        owner_live = identity_matches(manifest.get("owner"))
        if not owner_live:
            return "abandoned"
        heartbeat = float(manifest.get("heartbeat_at") or 0.0)
        if heartbeat and time.time() - heartbeat > stale_after:
            return "unresponsive"
        if manifest.get("cleanup_result") not in (None, "", "pending", "clean"):
            return "cleanup_failed"
        return "active"

    def is_owned_path(self, run_id: str, candidate: str) -> bool:
        root = (self.work_dir / run_id).resolve(strict=False)
        path = Path(candidate)
        if not path.is_absolute():
            return False
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, ValueError):
            return False
        # Existing symlink at any point makes crash cleanup ambiguous.
        current = path
        while current != root and current != current.parent:
            if current.exists() and current.is_symlink():
                return False
            current = current.parent
        return True

    def cleanup_owned_paths(self, run_id: str, paths: List[str]) -> List[Dict[str, str]]:
        results: List[Dict[str, str]] = []
        for raw in paths:
            if not self.is_owned_path(run_id, raw):
                results.append({"path": raw, "result": "cleanup_path_rejected"})
                continue
            path = Path(raw)
            try:
                if path.is_dir():
                    shutil.rmtree(str(path))
                else:
                    path.unlink(missing_ok=True)  # Python 3.8+
                results.append({"path": raw, "result": "removed"})
            except OSError as exc:
                results.append({"path": raw, "result": "cleanup_failed", "reason": str(exc)[:200]})
        return results

    def prune_completed(
        self,
        *,
        max_age_seconds: float = COMPLETED_RETENTION_SECONDS,
        max_count: int = COMPLETED_RETENTION_COUNT,
    ) -> int:
        files = sorted(
            self.completed_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        now = time.time()
        removed = 0
        for index, path in enumerate(files):
            try:
                expired = now - path.stat().st_mtime > max_age_seconds
                excess = index >= max_count
                if expired or excess:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


__all__ = [
    "COMPLETED_RETENTION_COUNT",
    "COMPLETED_RETENTION_SECONDS",
    "REGISTRY_SCHEMA_VERSION",
    "RegistryError",
    "RunRegistry",
    "boot_fingerprint",
    "identity_matches",
    "lope_home",
    "process_identity",
    "process_start_fingerprint",
]
