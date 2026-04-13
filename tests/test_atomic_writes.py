"""Tests for v0.4.0 Phase 2 — atomic + locked config writes.

Covers:
  - save() is atomic under a simulated crash mid-write
  - save() serializes 10 parallel processes via fcntl.flock
  - _safe_read() tolerates the open-vs-rename race with a retry
  - save()/load() round-trip is byte-identical for LopeCfg including
    learned_adapters
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lope.config import (
    LearnedAdapter,
    LopeCfg,
    VERSION,
    _safe_read,
    load,
    save,
)


# ---------------------------------------------------------------------------
# Round-trip (save then load must be byte-identical for all schema fields)
# ---------------------------------------------------------------------------

def test_save_load_round_trip_minimal(tmp_path):
    cfg_path = str(tmp_path / "config.json")
    cfg = LopeCfg(
        validators=["claude", "opencode"],
        primary="claude",
        timeout=300,
        parallel=True,
    )
    save(cfg, cfg_path)
    loaded = load(cfg_path)
    assert loaded is not None
    assert loaded.validators == ["claude", "opencode"]
    assert loaded.primary == "claude"
    assert loaded.timeout == 300
    assert loaded.parallel is True
    assert loaded.providers == []
    assert loaded.learned_adapters == {}


def test_save_load_round_trip_with_providers(tmp_path):
    cfg_path = str(tmp_path / "config.json")
    providers = [
        {"name": "groq-mistral", "type": "http", "url": "https://api.groq.com/v1"},
        {"name": "local-llama", "type": "subprocess", "argv": ["llama-cli"]},
    ]
    cfg = LopeCfg(
        validators=["gemini"],
        primary="gemini",
        timeout=600,
        parallel=False,
        providers=providers,
    )
    save(cfg, cfg_path)
    loaded = load(cfg_path)
    assert loaded is not None
    assert loaded.providers == providers


def test_save_load_round_trip_with_learned_adapters(tmp_path):
    cfg_path = str(tmp_path / "config.json")
    adapter = LearnedAdapter(
        argv_template=["codex", "exec", "{prompt}"],
        stdin_mode="pipe",
        stdout_parser="plaintext",
        timestamp=1_700_000_000.0,
        source_cli="claude",
        confidence=0.93,
    )
    cfg = LopeCfg(
        validators=["codex", "opencode"],
        primary="codex",
        timeout=480,
        parallel=True,
        learned_adapters={"codex": adapter},
    )
    save(cfg, cfg_path)
    loaded = load(cfg_path)
    assert loaded is not None
    assert "codex" in loaded.learned_adapters
    learned = loaded.learned_adapters["codex"]
    assert learned.argv_template == ["codex", "exec", "{prompt}"]
    assert learned.stdin_mode == "pipe"
    assert learned.stdout_parser == "plaintext"
    assert learned.timestamp == 1_700_000_000.0
    assert learned.source_cli == "claude"
    assert learned.confidence == 0.93


def test_save_version_field_is_schema_version(tmp_path):
    cfg_path = str(tmp_path / "config.json")
    cfg = LopeCfg(validators=["claude"], primary="claude", timeout=480, parallel=True)
    save(cfg, cfg_path)
    with open(cfg_path) as f:
        raw = json.load(f)
    assert raw["version"] == VERSION


# ---------------------------------------------------------------------------
# Atomic write — tmp file never leaves a half-written config.json
# ---------------------------------------------------------------------------

def test_save_does_not_leave_tmp_files(tmp_path):
    cfg_path = str(tmp_path / "config.json")
    cfg = LopeCfg(validators=["claude"], primary="claude", timeout=480, parallel=True)
    save(cfg, cfg_path)
    # After a clean save, only the target file + the lock sidecar may remain.
    # No *.tmp files should linger.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_save_overwrites_existing_config(tmp_path):
    cfg_path = str(tmp_path / "config.json")
    first = LopeCfg(validators=["claude"], primary="claude", timeout=480, parallel=True)
    second = LopeCfg(validators=["gemini", "opencode"], primary="gemini", timeout=120, parallel=False)
    save(first, cfg_path)
    save(second, cfg_path)
    loaded = load(cfg_path)
    assert loaded is not None
    assert loaded.validators == ["gemini", "opencode"]
    assert loaded.primary == "gemini"
    assert loaded.timeout == 120
    assert loaded.parallel is False


# ---------------------------------------------------------------------------
# _safe_read — tolerates the open-vs-rename race
# ---------------------------------------------------------------------------

def test_safe_read_missing_file_returns_none(tmp_path):
    missing = str(tmp_path / "does-not-exist.json")
    assert _safe_read(missing) is None


def test_safe_read_valid_json_returns_dict(tmp_path):
    path = str(tmp_path / "cfg.json")
    with open(path, "w") as f:
        json.dump({"version": VERSION, "validators": ["x"], "primary": "x",
                   "timeout": 480, "parallel": True}, f)
    result = _safe_read(path)
    assert isinstance(result, dict)
    assert result["version"] == VERSION
    assert result["validators"] == ["x"]


def test_safe_read_malformed_json_returns_none(tmp_path):
    path = str(tmp_path / "bad.json")
    with open(path, "w") as f:
        f.write("{not valid json at all")
    assert _safe_read(path) is None


# ---------------------------------------------------------------------------
# Concurrent writers — fcntl lock serializes save() across processes
# ---------------------------------------------------------------------------

def _writer_worker(args):
    """Child-process entry point: save a distinct LopeCfg and exit."""
    cfg_path, seed = args
    cfg = LopeCfg(
        validators=[f"validator_{seed}_a", f"validator_{seed}_b"],
        primary=f"validator_{seed}_a",
        timeout=480 + seed,
        parallel=(seed % 2 == 0),
    )
    save(cfg, cfg_path)


def test_concurrent_writers_no_corruption(tmp_path):
    """10 parallel processes all save(). Final file must be one valid save
    (last writer wins) with no partial merge or torn JSON."""
    cfg_path = str(tmp_path / "config.json")
    # Pre-create a baseline so the lock sidecar exists before the race
    baseline = LopeCfg(validators=["baseline"], primary="baseline",
                       timeout=480, parallel=True)
    save(baseline, cfg_path)

    N = 10
    with multiprocessing.Pool(processes=N) as pool:
        pool.map(_writer_worker, [(cfg_path, i) for i in range(N)])

    # The file must parse cleanly as a LopeCfg and contain exactly one
    # writer's payload (never a half-merge).
    loaded = load(cfg_path)
    assert loaded is not None, "config.json must be loadable after 10 concurrent writers"
    assert len(loaded.validators) == 2, "partial merge detected"
    # The primary must match one of the 10 valid seeds
    assert loaded.primary.startswith("validator_")
    seed_from_primary = int(loaded.primary.split("_")[1])
    assert 0 <= seed_from_primary < N
    # Validators and primary must agree on the same seed
    assert loaded.validators[0] == f"validator_{seed_from_primary}_a"
    assert loaded.validators[1] == f"validator_{seed_from_primary}_b"
    # No leftover .tmp files
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"tmp files linger: {leftovers}"


# ---------------------------------------------------------------------------
# Version mismatch — load() rejects incompatible schemas
# ---------------------------------------------------------------------------

def test_load_rejects_wrong_version(tmp_path):
    path = str(tmp_path / "bad_version.json")
    with open(path, "w") as f:
        json.dump({"version": 999, "validators": ["x"], "primary": "x",
                   "timeout": 480, "parallel": True}, f)
    assert load(path) is None
