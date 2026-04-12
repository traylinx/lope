"""Persistent config for Lope — read/write data/lope/config.json."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = 1


@dataclass
class LopeCfg:
    validators: List[str]
    primary: str
    timeout: int
    parallel: bool
    providers: List[Dict[str, Any]] = field(default_factory=list)


def default_path() -> str:
    """Return path to ~/.lope/config.json, expanding LOPE_HOME env var."""
    home = os.environ.get("LOPE_HOME", os.path.expanduser("~/.lope"))
    return os.path.join(home, "config.json")


def load(path: str) -> Optional[LopeCfg]:
    """Load config from JSON file. Returns None if missing or malformed."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version", 0) != VERSION:
        return None
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
    return LopeCfg(
        validators=validators,
        primary=primary,
        timeout=timeout,
        parallel=parallel,
        providers=providers,
    )


def save(cfg: LopeCfg, path: str) -> None:
    """Atomic write via tempfile + rename. Creates parent dirs if missing."""
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
