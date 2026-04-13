"""Persistent config for Lope — read/write ~/.lope/config.json.

v0.4.0 adds:
  - Layered precedence (CLI flags > env vars > per-project > user global > built-in)
  - Advisory file locking on writes (serializes concurrent savers)
  - Atomic read that tolerates rename races
  - learned_adapters schema extension for self-heal persistence
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = 1


@dataclass
class LearnedAdapter:
    """A self-healed validator invocation, persisted across sessions.

    Populated by SelfHealer when a CLI vendor changes its flags and lope
    auto-discovers the new invocation via --help + reviewer consensus.
    """
    argv_template: List[str]            # e.g. ["claude", "--print", "{prompt}"]
    stdin_mode: str = "none"            # "none" or "pipe"
    stdout_parser: str = "plaintext"    # "plaintext" or "json:path.to.field"
    timestamp: float = 0.0              # unix seconds when learned
    source_cli: str = ""                # which reviewer proposed it
    confidence: float = 0.0             # 0.0-1.0, reviewer-reported


@dataclass
class LopeCfg:
    validators: List[str]
    primary: str
    timeout: int
    parallel: bool
    providers: List[Dict[str, Any]] = field(default_factory=list)
    learned_adapters: Dict[str, LearnedAdapter] = field(default_factory=dict)


def default_path() -> str:
    """Return path to ~/.lope/config.json, expanding LOPE_HOME env var."""
    home = os.environ.get("LOPE_HOME", os.path.expanduser("~/.lope"))
    return os.path.join(home, "config.json")


def project_path(cwd: Optional[str] = None) -> str:
    """Return path to ./.lope/config.json in the given cwd (default: os.getcwd())."""
    base = cwd if cwd is not None else os.getcwd()
    return os.path.join(base, ".lope", "config.json")


def _parse_dict(data: Any) -> Optional[Dict[str, Any]]:
    """Validate raw JSON is a config-shaped dict. Returns None if unusable."""
    if not isinstance(data, dict):
        return None
    if data.get("version", 0) != VERSION:
        return None
    return data


def _hydrate_cfg(data: Dict[str, Any]) -> Optional[LopeCfg]:
    """Build a LopeCfg from a validated raw dict. Returns None on bad shape."""
    validators = data.get("validators")
    if not isinstance(validators, list):
        return None
    primary = data.get("primary")
    if not isinstance(primary, str):
        return None
    timeout = data.get("timeout", 480)
    if not isinstance(timeout, int):
        return None
    parallel = data.get("parallel", True)
    if not isinstance(parallel, bool):
        return None
    providers = data.get("providers", [])
    if not isinstance(providers, list):
        providers = []

    learned_raw = data.get("learned_adapters", {})
    learned: Dict[str, LearnedAdapter] = {}
    if isinstance(learned_raw, dict):
        for cli_name, entry in learned_raw.items():
            if not isinstance(entry, dict):
                continue
            argv = entry.get("argv_template", [])
            if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
                continue
            learned[cli_name] = LearnedAdapter(
                argv_template=argv,
                stdin_mode=entry.get("stdin_mode", "none"),
                stdout_parser=entry.get("stdout_parser", "plaintext"),
                timestamp=float(entry.get("timestamp", 0.0)),
                source_cli=entry.get("source_cli", ""),
                confidence=float(entry.get("confidence", 0.0)),
            )

    return LopeCfg(
        validators=validators,
        primary=primary,
        timeout=timeout,
        parallel=parallel,
        providers=providers,
        learned_adapters=learned,
    )


def _safe_read(path: str, retries: int = 1) -> Optional[Dict[str, Any]]:
    """Read config JSON, tolerating the atomic-rename race on concurrent writers.

    If open() races with a rename mid-flight and hits ENOENT/stale FD, retry
    once with a 50ms backoff. After `retries` attempts, return None.
    """
    attempt = 0
    while True:
        try:
            if not os.path.exists(path):
                return None
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            if attempt >= retries:
                return None
            attempt += 1
            time.sleep(0.05)
        except Exception:
            return None


def load(path: str) -> Optional[LopeCfg]:
    """Load config from JSON file. Returns None if missing or malformed."""
    raw = _safe_read(path)
    if raw is None:
        return None
    validated = _parse_dict(raw)
    if validated is None:
        return None
    return _hydrate_cfg(validated)


def _env_list(name: str) -> Optional[List[str]]:
    """Parse a comma-separated env var into a list, or None if unset/empty."""
    val = os.environ.get(name)
    if not val:
        return None
    return [s.strip() for s in val.split(",") if s.strip()]


def _env_int(name: str) -> Optional[int]:
    val = os.environ.get(name)
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def _env_bool(name: str) -> Optional[bool]:
    val = os.environ.get(name)
    if val is None:
        return None
    return val.lower() in ("1", "true", "yes", "on")


def load_layered(
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
) -> LopeCfg:
    """Load config with 5-layer precedence, highest-wins per field.

    Args:
      cwd: Working directory for per-project config lookup. Defaults to os.getcwd().
      env: Environment mapping for the env-var layer. Defaults to os.environ.
           Pass a custom dict in tests without monkeypatching os.environ.
      cli_overrides: Argparse-derived dict with keys validators/primary/timeout/parallel.

    Layers, from lowest to highest precedence:
      1. Built-in defaults (empty validators, 480s timeout, parallel=True)
      2. User global config  (~/.lope/config.json)
      3. Per-project config  (./.lope/config.json in cwd, if present)
      4. Environment variables (LOPE_VALIDATORS, LOPE_PRIMARY, etc.)
      5. CLI overrides (passed in by the caller from argparse)

    Each layer overrides the previous one field-by-field — a user can set
    LOPE_VALIDATORS in their shell while still inheriting timeout and
    parallel from the global file.

    Returns a LopeCfg with the merged result. Never writes anything; callers
    that want to persist must call save() explicitly.
    """
    # Use os.environ by default. Tests may pass a custom dict.
    if env is None:
        env = os.environ
    # Layer 1: built-in defaults
    merged: Dict[str, Any] = {
        "validators": [],
        "primary": "",
        "timeout": 480,
        "parallel": True,
        "providers": [],
        "learned_adapters": {},
    }

    # Layer 2: user global
    global_cfg = load(default_path())
    if global_cfg is not None:
        merged["validators"] = list(global_cfg.validators)
        merged["primary"] = global_cfg.primary
        merged["timeout"] = global_cfg.timeout
        merged["parallel"] = global_cfg.parallel
        merged["providers"] = list(global_cfg.providers)
        merged["learned_adapters"] = dict(global_cfg.learned_adapters)

    # Layer 3: per-project (./.lope/config.json in cwd)
    # Read raw JSON once — avoids a double _safe_read and lets us apply a
    # relaxed version policy: absent `version` key = partial override (accept);
    # only reject on explicit version mismatch with the current schema.
    proj_raw = _safe_read(project_path(cwd)) or {}
    if proj_raw:
        proj_version = proj_raw.get("version")
        if proj_version is None or proj_version == VERSION:
            if isinstance(proj_raw.get("validators"), list):
                merged["validators"] = [
                    s for s in proj_raw["validators"] if isinstance(s, str)
                ]
            if isinstance(proj_raw.get("primary"), str):
                merged["primary"] = proj_raw["primary"]
            if isinstance(proj_raw.get("timeout"), int):
                merged["timeout"] = proj_raw["timeout"]
            if isinstance(proj_raw.get("parallel"), bool):
                merged["parallel"] = proj_raw["parallel"]
            if isinstance(proj_raw.get("providers"), list) and proj_raw["providers"]:
                merged["providers"] = list(proj_raw["providers"])
            # learned_adapters intentionally NOT inherited from project config —
            # those are always user-global.

    # Layer 4: environment variables (read from the `env` mapping arg)
    raw_validators = env.get("LOPE_VALIDATORS")
    if raw_validators:
        merged["validators"] = [s.strip() for s in raw_validators.split(",") if s.strip()]
    raw_primary = env.get("LOPE_PRIMARY")
    if raw_primary:
        merged["primary"] = raw_primary
    raw_timeout = env.get("LOPE_TIMEOUT")
    if raw_timeout:
        try:
            merged["timeout"] = int(raw_timeout)
        except ValueError:
            pass
    raw_parallel = env.get("LOPE_PARALLEL")
    if raw_parallel is not None:
        merged["parallel"] = raw_parallel.lower() in ("1", "true", "yes", "on")
    raw_sequential = env.get("LOPE_SEQUENTIAL", "")
    if raw_sequential.lower() in ("1", "true", "yes", "on"):
        merged["parallel"] = False

    # Layer 5: CLI overrides (from argparse in cli.py)
    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is None:
                continue
            if key in ("validators", "primary", "timeout", "parallel"):
                merged[key] = value

    return LopeCfg(
        validators=merged["validators"],
        primary=merged["primary"],
        timeout=merged["timeout"],
        parallel=merged["parallel"],
        providers=merged["providers"],
        learned_adapters=merged["learned_adapters"],
    )


def _adapter_to_dict(adapter: LearnedAdapter) -> Dict[str, Any]:
    return {
        "argv_template": adapter.argv_template,
        "stdin_mode": adapter.stdin_mode,
        "stdout_parser": adapter.stdout_parser,
        "timestamp": adapter.timestamp,
        "source_cli": adapter.source_cli,
        "confidence": adapter.confidence,
    }


def save(cfg: LopeCfg, path: str) -> None:
    """Atomic, locked write to the given path.

    Acquires an advisory exclusive lock on a sidecar `.lock` file,
    writes payload to a tmp file in the same directory, fsyncs, renames
    over the target (atomic on the same filesystem), then releases the lock.

    Concurrent save() calls from different processes serialize via the lock;
    readers never see partial state because rename is atomic.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": VERSION,
        "validators": cfg.validators,
        "primary": cfg.primary,
        "timeout": cfg.timeout,
        "parallel": cfg.parallel,
        "providers": cfg.providers,
    }
    if cfg.learned_adapters:
        data["learned_adapters"] = {
            name: _adapter_to_dict(adapter)
            for name, adapter in cfg.learned_adapters.items()
        }

    lock_path = path.with_suffix(path.suffix + ".lock")
    # The lock file is small, persistent, and safe to keep around —
    # multiple processes open it to coordinate writes.
    with open(lock_path, "a+") as lock_fp:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        except OSError:
            # Platform without flock (e.g. Windows via some emulation).
            # Fall through — best-effort without the lock.
            pass

        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            Path(tmp_path).replace(path)
        except Exception:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
            raise
        finally:
            try:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
