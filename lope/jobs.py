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
import signal
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


def process_state(pid: int) -> str:
    if pid <= 0:
        return ""
    ps_bin = shutil.which("ps") or ("/bin/ps" if Path("/bin/ps").is_file() else "/usr/bin/ps")
    try:
        proc = subprocess.run(
            [ps_bin, "-p", str(pid), "-o", "stat="], capture_output=True,
            text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip()


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
    state = process_state(pid)
    if not state or state.startswith("Z"):
        return False
    return process_start_fingerprint(pid) == expected


def process_resources(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort CPU/RSS/process count for positively identified run PIDs."""

    identities = [manifest.get("owner")]
    pgids = set()
    for call in (manifest.get("calls") or {}).values():
        identities.extend((call.get("supervisor"), call.get("child")))
        if identity_matches(call.get("child")):
            try:
                pgid = int(call.get("pgid") or 0)
            except (TypeError, ValueError):
                pgid = 0
            if pgid > 0:
                pgids.add(pgid)
    owned_pids = {
        int(identity.get("pid"))
        for identity in identities
        if identity_matches(identity)
    }
    ps_bin = shutil.which("ps") or (
        "/bin/ps" if Path("/bin/ps").is_file() else "/usr/bin/ps"
    )
    rows: Dict[int, Dict[str, float]] = {}
    try:
        proc = subprocess.run(
            [ps_bin, "-axo", "pid=,pgid=,%cpu=,rss="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) != 4:
                continue
            try:
                pid, pgid = int(parts[0]), int(parts[1])
                cpu, rss_kib = float(parts[2]), float(parts[3])
            except ValueError:
                continue
            if pid in owned_pids or pgid in pgids:
                rows[pid] = {"cpu": cpu, "rss_bytes": rss_kib * 1024.0}
    except (OSError, subprocess.TimeoutExpired):
        rows = {pid: {"cpu": 0.0, "rss_bytes": 0.0} for pid in owned_pids}
    return {
        "process_count": len(rows),
        "cpu_percent": round(sum(item["cpu"] for item in rows.values()), 3),
        "rss_bytes": int(sum(item["rss_bytes"] for item in rows.values())),
        "pids": sorted(rows),
        "available": bool(rows) or not owned_pids,
    }


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
            "heartbeat_source": "owner",
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
            now = time.time()
            manifest["heartbeat_at"] = now
            manifest["heartbeat_source"] = source
            if source == "owner":
                manifest["owner_heartbeat_at"] = now
            elif source == "supervisor":
                manifest["supervisor_heartbeat_at"] = now
            return manifest

        return self.update(run_id, mutate)

    def register_call(self, run_id: str, call: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "call_id", "validator", "stage", "state", "supervisor", "child",
            "pgid", "started_at", "heartbeat_at", "deadline_at", "transport",
            "ownership_marker_hash", "executable_hash", "command_hash",
            "owned_paths", "cleanup_result", "outcome", "ended_at", "reason",
            "processes_targeted", "platform_cleanup",
            "heartbeat_source",
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

    def set_planned_calls(self, run_id: str, planned: int) -> Dict[str, Any]:
        def mutate(manifest: Dict[str, Any]) -> Dict[str, Any]:
            counters = manifest.setdefault("call_counters", {})
            counters["planned"] = max(
                int(counters.get("planned", 0)),
                max(0, int(planned)),
            )
            return manifest

        return self.update(run_id, mutate)

    def update_call(self, run_id: str, call_id: str, **changes: Any) -> Dict[str, Any]:
        def mutate(manifest: Dict[str, Any]) -> Dict[str, Any]:
            calls = manifest.setdefault("calls", {})
            if call_id not in calls:
                raise RegistryError(f"unknown call_id: {call_id}")
            previous_state = calls[call_id].get("state")
            calls[call_id].update(changes)
            now = time.time()
            calls[call_id]["heartbeat_at"] = now
            manifest["heartbeat_at"] = now
            source = changes.get("heartbeat_source")
            if source == "supervisor":
                manifest["supervisor_heartbeat_at"] = now
                manifest["heartbeat_source"] = "supervisor"
            if (
                changes.get("state") in {"finished", "completed"}
                and previous_state not in {"finished", "completed"}
            ):
                counters = manifest.setdefault("call_counters", {})
                counters["finished"] = int(counters.get("finished", 0)) + 1
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
                now = time.time()
                manifest["age_seconds"] = max(
                    0.0, now - float(manifest.get("started_at") or now)
                )
                deadline = manifest.get("deadline_at")
                manifest["deadline_remaining_seconds"] = (
                    None if deadline is None else float(deadline) - now
                )
                manifest["owner_live"] = identity_matches(manifest.get("owner"))
                manifest["resources"] = process_resources(manifest)
                out.append(manifest)
            except RegistryError as exc:
                if include_invalid:
                    out.append({
                        "state": "invalid",
                        "classification": "ownership_unverified",
                        "manifest_path": str(path),
                        "reason": str(exc),
                        "resources": {
                            "process_count": 0,
                            "cpu_percent": 0.0,
                            "rss_bytes": 0,
                            "pids": [],
                            "available": False,
                        },
                    })
        return out

    def classify(self, manifest: Dict[str, Any], *, stale_after: float = 45.0) -> str:
        if manifest.get("host_boot_fingerprint") != boot_fingerprint():
            return "abandoned"
        if manifest.get("cleanup_result") not in (None, "", "pending", "clean"):
            return "cleanup_failed"
        owner_live = identity_matches(manifest.get("owner"))
        if not owner_live:
            return "abandoned"
        owner_heartbeat = manifest.get("owner_heartbeat_at")
        if owner_heartbeat is None:
            owner_heartbeat = (
                manifest.get("started_at")
                if manifest.get("supervisor_heartbeat_at") is not None
                else manifest.get("heartbeat_at")
            )
        heartbeat = float(owner_heartbeat or 0.0)
        if heartbeat and time.time() - heartbeat > stale_after:
            return "unresponsive"
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

    @staticmethod
    def _wait_identity_gone(identity: Dict[str, Any], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not identity_matches(identity):
                return True
            time.sleep(0.05)
        return not identity_matches(identity)

    def _remove_empty_work_dirs(self, run_id: str) -> List[Dict[str, str]]:
        """Remove only empty, canonical directories beneath one run root.

        Reconciliation must not use ``rmtree`` as a shortcut: an unregistered
        file or a rejected symlink is evidence that cleanup is incomplete, not
        permission to delete it recursively.
        """

        root = self.work_dir / run_id
        if not root.exists():
            return []
        results: List[Dict[str, str]] = []
        for current, directories, _files in os.walk(root, topdown=False, followlinks=False):
            for name in directories:
                path = Path(current) / name
                if path.is_symlink():
                    continue
                try:
                    path.rmdir()
                    results.append({"path": str(path), "result": "removed"})
                except OSError:
                    pass
        try:
            root.rmdir()
            results.append({"path": str(root), "result": "removed"})
        except OSError as exc:
            results.append({
                "path": str(root),
                "result": "cleanup_failed",
                "reason": f"work directory not empty: {exc}"[:200],
            })
        return results

    def _reap_call(self, run_id: str, call_id: str, call: Dict[str, Any], *, dry_run: bool) -> Dict[str, Any]:
        child = call.get("child") or {}
        if not identity_matches(child):
            try:
                pid = int(child.get("pid", 0))
            except (TypeError, ValueError):
                pid = 0
            expected = child.get("start_fingerprint")
            current = process_start_fingerprint(pid) if pid > 0 else None
            if current and expected and current != expected and not process_state(pid).startswith("Z"):
                return {"call_id": call_id, "action": "refused", "result": "identity_mismatch"}
            return {"call_id": call_id, "action": "none", "result": "not_live_or_identity_mismatch"}
        pid = int(child.get("pid", 0))
        pgid = int(call.get("pgid") or 0)
        marker_hash = str(call.get("ownership_marker_hash") or "")
        if pid <= 0 or pgid <= 0 or len(marker_hash) != 64:
            return {"call_id": call_id, "action": "refused", "result": "ownership_unverified"}
        if os.name == "posix":
            try:
                actual_pgid = os.getpgid(pid)
            except OSError:
                return {"call_id": call_id, "action": "none", "result": "already_gone"}
            if actual_pgid != pgid:
                return {"call_id": call_id, "action": "refused", "result": "pgid_mismatch"}
        if dry_run:
            return {"call_id": call_id, "action": "would_reap", "result": "confirmed_owned"}
        try:
            if os.name == "posix":
                os.killpg(pgid, signal.SIGTERM)
            else:  # pragma: no cover
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return {"call_id": call_id, "action": "none", "result": "already_gone"}
        if not self._wait_identity_gone(child, 1.0):
            try:
                if os.name == "posix" and hasattr(signal, "SIGKILL"):
                    os.killpg(pgid, signal.SIGKILL)
                else:  # pragma: no cover
                    os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        gone = self._wait_identity_gone(child, 3.0)
        return {
            "call_id": call_id,
            "action": "reaped" if gone else "kill_sent",
            "result": "clean" if gone else "cleanup_unconfirmed",
        }

    def reap_run(self, run_id: str, *, dry_run: bool = False) -> Dict[str, Any]:
        """Reap one confirmed abandoned run; never signal a live owner."""

        manifest = self.load_active(run_id)
        classification = self.classify(manifest)
        if classification != "abandoned":
            return {
                "run_id": run_id,
                "classification": classification,
                "action": "refused",
                "reason": "owner is live or ownership is not abandoned",
                "calls": [],
            }
        actions = [
            self._reap_call(run_id, call_id, call, dry_run=dry_run)
            for call_id, call in (manifest.get("calls") or {}).items()
        ]
        refused = any(item["action"] == "refused" for item in actions)
        still_live = any(
            identity_matches(call.get("child"))
            for call in (manifest.get("calls") or {}).values()
        )
        cleanup = []
        if not dry_run and not refused and not still_live:
            paths: List[str] = []
            for call in (manifest.get("calls") or {}).values():
                paths.extend(str(p) for p in (call.get("owned_paths") or []))
            cleanup = self.cleanup_owned_paths(run_id, paths)
            cleanup.extend(self._remove_empty_work_dirs(run_id))
            cleanup_failed = any(
                item.get("result") not in ("removed", "already_gone")
                for item in cleanup
            )

            def record(manifest_to_update: Dict[str, Any]) -> Dict[str, Any]:
                manifest_to_update.setdefault("cleanup_history", []).append({
                    "at": time.time(),
                    "source": "reconcile",
                    "calls": actions,
                    "paths": cleanup,
                })
                if cleanup_failed:
                    manifest_to_update["state"] = "cleanup_failed"
                    manifest_to_update["cleanup_result"] = "cleanup_failed"
                    manifest_to_update["reason"] = "owned resource cleanup incomplete"
                return manifest_to_update

            self.update(run_id, record)
            if not cleanup_failed:
                self.finish_run(
                    run_id,
                    state="reaped",
                    reason="confirmed owner dead",
                    cleanup_result="clean",
                )
        return {
            "run_id": run_id,
            "classification": classification,
            "action": (
                "dry_run" if dry_run else
                "refused" if refused or still_live else
                "cleanup_failed" if any(
                    item.get("result") not in ("removed", "already_gone")
                    for item in cleanup
                ) else
                "reaped"
            ),
            "calls": actions,
            "cleanup": cleanup,
        }

    def reap_all(self, *, dry_run: bool = False) -> List[Dict[str, Any]]:
        """Return one typed action/refusal for every active registry row."""

        results: List[Dict[str, Any]] = []
        for manifest in self.list_active(include_invalid=True):
            run_id = str(manifest.get("run_id") or "")
            if not run_id:
                results.append({
                    "run_id": None,
                    "classification": "ownership_unverified",
                    "action": "refused",
                    "reason": manifest.get("reason") or "invalid manifest",
                    "calls": [],
                })
                continue
            results.append(self.reap_run(run_id, dry_run=dry_run))
        return results

    def kill_run(self, run_id: str) -> Dict[str, Any]:
        """Cancel one live, fingerprint-verified owner and its owned calls."""

        manifest = self.load_active(run_id)
        if manifest.get("host_boot_fingerprint") != boot_fingerprint():
            return {
                "run_id": run_id,
                "classification": "ownership_unverified",
                "action": "refused",
                "reason": "host boot fingerprint mismatch",
                "calls": [],
            }
        owner = manifest.get("owner") or {}
        if not identity_matches(owner):
            return {
                "run_id": run_id,
                "classification": self.classify(manifest),
                "action": "refused",
                "reason": "missing, dead, or reused owner fingerprint; run jobs reap instead",
                "calls": [],
            }
        owner_pid = int(owner.get("pid") or 0)
        if owner_pid == os.getpid():
            return {
                "run_id": run_id,
                "classification": "active",
                "action": "refused",
                "reason": "jobs kill cannot terminate its own process",
                "calls": [],
            }

        # Prove every currently-live child before signalling the owner. One
        # ambiguous child makes the entire operation fail closed.
        preflight = [
            self._reap_call(run_id, call_id, call, dry_run=True)
            for call_id, call in (manifest.get("calls") or {}).items()
        ]
        if any(item.get("action") == "refused" for item in preflight):
            return {
                "run_id": run_id,
                "classification": "ownership_unverified",
                "action": "refused",
                "reason": "one or more live provider identities could not be verified",
                "calls": preflight,
            }

        try:
            os.kill(owner_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            return {
                "run_id": run_id,
                "classification": "active",
                "action": "refused",
                "reason": f"cannot signal verified owner: {exc}"[:240],
                "calls": preflight,
            }
        if not self._wait_identity_gone(owner, 2.0) and identity_matches(owner):
            try:
                os.kill(owner_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                return {
                    "run_id": run_id,
                    "classification": "active",
                    "action": "refused",
                    "reason": f"verified owner ignored TERM and KILL failed: {exc}"[:240],
                    "calls": preflight,
                }
            self._wait_identity_gone(owner, 3.0)
        if identity_matches(owner):
            return {
                "run_id": run_id,
                "classification": "active",
                "action": "cleanup_failed",
                "reason": "verified owner remains live after TERM/KILL",
                "calls": preflight,
            }

        reaped = self.reap_run(run_id, dry_run=False)
        return {
            "run_id": run_id,
            "classification": reaped.get("classification", "abandoned"),
            "action": "killed" if reaped.get("action") == "reaped" else reaped.get("action"),
            "reason": "verified owner terminated",
            "calls": reaped.get("calls", []),
            "cleanup": reaped.get("cleanup", []),
        }

    def close_run(
        self,
        run_id: str,
        *,
        state: str = "completed",
        reason: str = "",
    ) -> Dict[str, Any]:
        """Verify zero live owned work and scratch before completing a run."""

        manifest = self.load_active(run_id)
        calls = manifest.get("calls") or {}
        unresolved = []
        for call_id, call in calls.items():
            live = []
            for role in ("supervisor", "child"):
                if identity_matches(call.get(role)):
                    live.append(role)
            if live or call.get("state") not in {"finished", "completed"}:
                unresolved.append({
                    "call_id": call_id,
                    "live": live,
                    "state": call.get("state"),
                })

        paths: List[str] = []
        for call in calls.values():
            paths.extend(str(path) for path in (call.get("owned_paths") or []))
        cleanup = self.cleanup_owned_paths(run_id, paths)
        cleanup.extend(self._remove_empty_work_dirs(run_id))
        cleanup_failed = any(
            item.get("result") not in {"removed", "already_gone"}
            for item in cleanup
        )
        if unresolved or cleanup_failed:
            close_reason = (
                "run closeout found live/unresolved calls"
                if unresolved else "run closeout found owned scratch cleanup failure"
            )

            def mark_failed(value: Dict[str, Any]) -> Dict[str, Any]:
                value["state"] = "cleanup_failed"
                value["cleanup_result"] = "cleanup_failed"
                value["reason"] = close_reason
                value.setdefault("cleanup_history", []).append({
                    "at": time.time(),
                    "source": "closeout",
                    "unresolved": unresolved,
                    "paths": cleanup,
                })
                return value

            self.update(run_id, mark_failed)
            return {
                "run_id": run_id,
                "action": "cleanup_failed",
                "reason": close_reason,
                "unresolved": unresolved,
                "cleanup": cleanup,
            }

        destination = self.finish_run(
            run_id,
            state=state,
            reason=reason,
            cleanup_result="clean",
        )
        return {
            "run_id": run_id,
            "action": "closed",
            "completed_manifest": str(destination),
            "unresolved": [],
            "cleanup": cleanup,
        }

    def reconcile(self, *, dry_run: bool = False) -> List[Dict[str, Any]]:
        results = []
        for manifest in self.list_active(include_invalid=False):
            if manifest.get("classification") == "abandoned" and manifest.get("run_id"):
                results.append(self.reap_run(str(manifest["run_id"]), dry_run=dry_run))
        return results


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
    "process_resources",
    "process_state",
    "process_start_fingerprint",
]
