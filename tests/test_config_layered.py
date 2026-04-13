"""Tests for load_layered() config precedence chain (Phase 1 of v0.4.0).

Coverage:
  - All four override layers resolve in correct precedence order
  - load_layered() with only global config matches load() output
  - LOPE_VALIDATORS comma-separated env var parses correctly
  - Integration: --validators CLI flag does not mutate global config file
  - Integration: per-project .lope/config.json overrides global primary
  - Regression: lope configure writes to ~/.lope/config.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the package is importable from the repo root when run without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lope.config import (
    LopeCfg,
    LearnedAdapter,
    VERSION,
    default_path,
    load,
    load_layered,
    project_path,
    save,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(path: str, *, validators, primary, timeout=480, parallel=True,
                  providers=None, version=VERSION):
    """Write a minimal valid config JSON to *path*, creating parents as needed."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": version,
        "validators": validators,
        "primary": primary,
        "timeout": timeout,
        "parallel": parallel,
    }
    if providers:
        data["providers"] = providers
    with open(path, "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# Unit test: all four override layers with correct precedence
# ---------------------------------------------------------------------------

def test_load_layered_full_precedence(tmp_path, monkeypatch):
    """CLI flags beat env vars beat project config beat global config.

    Setup:
      - global config:  validators=["ollama"], primary="ollama", parallel=False, providers=[p]
      - project config: validators=["aider"],  primary="aider",  parallel=True
      - env vars:       LOPE_VALIDATORS=gemini, LOPE_TIMEOUT=120
      - CLI overrides:  validators=["opencode"], primary="opencode"

    Expected result:
      - validators = ["opencode"]     (CLI layer wins)
      - primary    = "opencode"       (CLI layer wins)
      - timeout    = 120              (env var wins; CLI did not set it)
      - parallel   = True             (project layer wins; env/CLI did not set it)
      - providers  = [p]              (global layer; nothing higher set it)
    """
    # Use a dedicated LOPE_HOME so we don't touch the real ~/.lope
    lope_home = tmp_path / "home_lope"
    lope_home.mkdir()
    global_cfg_path = str(lope_home / "config.json")
    cwd = str(tmp_path / "project")

    providers = [{"name": "custom", "type": "subprocess", "argv": ["echo"]}]
    _write_config(global_cfg_path,
                  validators=["ollama"], primary="ollama",
                  timeout=999, parallel=False, providers=providers)
    _write_config(project_path(cwd),
                  validators=["aider"], primary="aider", parallel=True)

    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_VALIDATORS", "gemini")
    monkeypatch.setenv("LOPE_TIMEOUT", "120")
    monkeypatch.delenv("LOPE_PRIMARY", raising=False)
    monkeypatch.delenv("LOPE_PARALLEL", raising=False)
    monkeypatch.delenv("LOPE_SEQUENTIAL", raising=False)

    cli_overrides = {"validators": ["opencode"], "primary": "opencode"}
    cfg = load_layered(cwd=cwd, cli_overrides=cli_overrides)

    assert cfg.validators == ["opencode"], "CLI layer must win for validators"
    assert cfg.primary == "opencode",      "CLI layer must win for primary"
    assert cfg.timeout == 120,             "Env layer must win for timeout"
    assert cfg.parallel is True,           "Project layer must win for parallel"
    assert cfg.providers == providers,     "Global layer must supply providers"


# ---------------------------------------------------------------------------
# Unit test: load_layered with only global config == load()
# ---------------------------------------------------------------------------

def test_load_layered_global_only_matches_load(tmp_path, monkeypatch):
    """When only global config exists, load_layered() returns the same values as load()."""
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    global_cfg_path = str(lope_home / "config.json")
    _write_config(global_cfg_path,
                  validators=["claude", "opencode"], primary="claude",
                  timeout=300, parallel=False)

    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    # Ensure no env overrides bleed in
    for var in ("LOPE_VALIDATORS", "LOPE_PRIMARY", "LOPE_TIMEOUT",
                "LOPE_PARALLEL", "LOPE_SEQUENTIAL"):
        monkeypatch.delenv(var, raising=False)

    # Use an empty cwd (no project config)
    cwd = str(tmp_path / "empty_project")
    os.makedirs(cwd, exist_ok=True)

    from lope.config import default_path as _default_path
    # Monkey-patch default_path to return our temp global path
    monkeypatch.setattr("lope.config.default_path", lambda: global_cfg_path)

    direct = load(global_cfg_path)
    layered = load_layered(cwd=cwd)

    assert direct is not None
    assert layered.validators == direct.validators
    assert layered.primary    == direct.primary
    assert layered.timeout    == direct.timeout
    assert layered.parallel   == direct.parallel
    assert layered.providers  == direct.providers


# ---------------------------------------------------------------------------
# Unit test: LOPE_VALIDATORS comma-separated parsing
# ---------------------------------------------------------------------------

def test_lope_validators_env_parsed(tmp_path, monkeypatch):
    """LOPE_VALIDATORS=claude,gemini must produce ["claude", "gemini"]."""
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_VALIDATORS", "claude,gemini")
    monkeypatch.delenv("LOPE_PRIMARY", raising=False)
    monkeypatch.delenv("LOPE_TIMEOUT", raising=False)
    monkeypatch.delenv("LOPE_PARALLEL", raising=False)
    monkeypatch.delenv("LOPE_SEQUENTIAL", raising=False)
    monkeypatch.setattr("lope.config.default_path", lambda: str(lope_home / "config.json"))

    cfg = load_layered(cwd=str(tmp_path))
    assert cfg.validators == ["claude", "gemini"]


def test_lope_validators_env_strips_whitespace(tmp_path, monkeypatch):
    """LOPE_VALIDATORS with spaces around commas must strip whitespace."""
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_VALIDATORS", " claude , gemini , opencode ")
    monkeypatch.delenv("LOPE_PRIMARY", raising=False)
    monkeypatch.delenv("LOPE_TIMEOUT", raising=False)
    monkeypatch.delenv("LOPE_PARALLEL", raising=False)
    monkeypatch.delenv("LOPE_SEQUENTIAL", raising=False)
    monkeypatch.setattr("lope.config.default_path", lambda: str(lope_home / "config.json"))

    cfg = load_layered(cwd=str(tmp_path))
    assert cfg.validators == ["claude", "gemini", "opencode"]


# ---------------------------------------------------------------------------
# Integration test: --validators flag does not mutate global config
# ---------------------------------------------------------------------------

def test_cli_validators_flag_does_not_mutate_global(tmp_path, monkeypatch):
    """lope negotiate --validators opencode,gemini must not change global mtime.

    We can't actually run negotiate end-to-end (needs live CLIs), so we
    exercise the _ensure_config() path directly by importing cli and calling
    _ensure_config with a fake argparse namespace. This validates the
    load_layered path taken by the real CLI without spawning subprocesses.
    """
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    global_cfg = lope_home / "config.json"
    _write_config(str(global_cfg),
                  validators=["claude"], primary="claude")

    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    for var in ("LOPE_VALIDATORS", "LOPE_PRIMARY", "LOPE_TIMEOUT",
                "LOPE_PARALLEL", "LOPE_SEQUENTIAL"):
        monkeypatch.delenv(var, raising=False)

    original_mtime = global_cfg.stat().st_mtime

    # Simulate argparse namespace from "lope negotiate --validators opencode,gemini"
    import types
    args = types.SimpleNamespace(
        validators="opencode,gemini",
        primary=None,
        timeout=None,
        parallel=None,
    )

    # Patch default_path to return our temp global config
    import lope.config as _cfg_mod
    import lope.cli as _cli_mod
    monkeypatch.setattr(_cfg_mod, "default_path", lambda: str(global_cfg))
    monkeypatch.setattr(_cli_mod, "default_path", lambda: str(global_cfg))

    # _ensure_config calls load_layered which must NOT write anything
    cfg, pool = _cli_mod._ensure_config(args)

    assert cfg.validators == ["opencode", "gemini"], \
        "CLI flag validators must be respected"
    assert global_cfg.stat().st_mtime == original_mtime, \
        "Global config file must NOT be mutated by --validators flag"


# ---------------------------------------------------------------------------
# Integration test: per-project config overrides global primary
# ---------------------------------------------------------------------------

def test_project_config_overrides_global_primary(tmp_path, monkeypatch):
    """./.lope/config.json with primary=gemini must win over global primary=claude."""
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    global_cfg_path = str(lope_home / "config.json")
    cwd = str(tmp_path / "project")

    _write_config(global_cfg_path,
                  validators=["claude", "gemini"], primary="claude", timeout=480)
    _write_config(project_path(cwd),
                  validators=["claude", "gemini"], primary="gemini")

    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    for var in ("LOPE_VALIDATORS", "LOPE_PRIMARY", "LOPE_TIMEOUT",
                "LOPE_PARALLEL", "LOPE_SEQUENTIAL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("lope.config.default_path", lambda: global_cfg_path)

    cfg = load_layered(cwd=cwd)

    assert cfg.primary == "gemini", \
        "Project config primary must override global primary"

    # Verify the global config was not mutated
    with open(global_cfg_path) as f:
        raw = json.load(f)
    assert raw["primary"] == "claude", \
        "Global config file must not be mutated by project config resolution"


# ---------------------------------------------------------------------------
# Regression test: lope configure writes to global config path
# ---------------------------------------------------------------------------

def test_configure_writes_to_global_path(tmp_path, monkeypatch):
    """save() called with default_path() must write to ~/.lope/config.json (or LOPE_HOME)."""
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    global_cfg_path = lope_home / "config.json"

    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setattr("lope.config.default_path", lambda: str(global_cfg_path))
    import lope.cli as _cli_mod
    monkeypatch.setattr(_cli_mod, "default_path", lambda: str(global_cfg_path))

    # _cmd_configure calls save_config(cfg, path) with path = default_path().
    # We simulate this directly.
    from lope.config import save as _save, default_path as _default_path
    import lope.config as _cfg_mod
    monkeypatch.setattr(_cfg_mod, "default_path", lambda: str(global_cfg_path))

    cfg = LopeCfg(
        validators=["claude", "opencode"],
        primary="claude",
        timeout=480,
        parallel=True,
    )
    _save(cfg, str(global_cfg_path))

    assert global_cfg_path.exists(), \
        "lope configure must write the global config to LOPE_HOME/config.json"
    with open(global_cfg_path) as f:
        data = json.load(f)
    assert data["validators"] == ["claude", "opencode"]
    assert data["primary"] == "claude"
    # Confirm the per-project path was NOT created (configure never touches it)
    project_cfg = tmp_path / "project" / ".lope" / "config.json"
    assert not project_cfg.exists(), \
        "lope configure must not create a per-project config"


# ---------------------------------------------------------------------------
# Unit test: project config without a version key is accepted as partial override
# ---------------------------------------------------------------------------

def test_project_config_no_version_accepted(tmp_path, monkeypatch):
    """A project config that omits the `version` key is a valid partial override.

    Prior to this fix, _parse_dict rejected any dict where version != VERSION
    (including missing version, which defaulted to 0). Project configs that
    only set e.g. {"primary": "gemini"} were silently dropped, meaning the
    global config's primary was never overridden.
    """
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    global_cfg_path = str(lope_home / "config.json")
    cwd = str(tmp_path / "project")

    _write_config(global_cfg_path,
                  validators=["claude", "gemini"], primary="claude", timeout=480)

    # Write a project config with NO version key — this is the partial-override format
    project_cfg_path = project_path(cwd)
    Path(project_cfg_path).parent.mkdir(parents=True, exist_ok=True)
    with open(project_cfg_path, "w") as f:
        json.dump({"primary": "gemini"}, f)

    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    for var in ("LOPE_VALIDATORS", "LOPE_PRIMARY", "LOPE_TIMEOUT",
                "LOPE_PARALLEL", "LOPE_SEQUENTIAL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("lope.config.default_path", lambda: global_cfg_path)

    cfg = load_layered(cwd=cwd)

    assert cfg.primary == "gemini", \
        "Project config without version key must still override global primary"
    assert cfg.validators == ["claude", "gemini"], \
        "Global validators must be inherited when project config omits them"
    assert cfg.timeout == 480, \
        "Global timeout must be inherited when project config omits it"


# ---------------------------------------------------------------------------
# Unit test: LOPE_SEQUENTIAL forces parallel=False
# ---------------------------------------------------------------------------

def test_lope_sequential_forces_sequential(tmp_path, monkeypatch):
    """LOPE_SEQUENTIAL=1 must set parallel=False regardless of global config."""
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    global_cfg_path = str(lope_home / "config.json")
    _write_config(global_cfg_path,
                  validators=["claude"], primary="claude", parallel=True)

    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_SEQUENTIAL", "1")
    monkeypatch.delenv("LOPE_PARALLEL", raising=False)
    monkeypatch.setattr("lope.config.default_path", lambda: global_cfg_path)

    cfg = load_layered(cwd=str(tmp_path))
    assert cfg.parallel is False, "LOPE_SEQUENTIAL=1 must set parallel=False"


# ---------------------------------------------------------------------------
# Unit test: learned_adapters not inherited from project config
# ---------------------------------------------------------------------------

def test_learned_adapters_not_inherited_from_project(tmp_path, monkeypatch):
    """learned_adapters must only come from the global config, never the project config."""
    lope_home = tmp_path / "lope_home"
    lope_home.mkdir()
    global_cfg_path = str(lope_home / "config.json")
    cwd = str(tmp_path / "project")

    adapter_entry = {
        "argv_template": ["claude", "--print", "{prompt}"],
        "stdin_mode": "none",
        "stdout_parser": "plaintext",
        "timestamp": 1700000000.0,
        "source_cli": "opencode",
        "confidence": 0.95,
    }
    # Write global config with a learned adapter
    global_data = {
        "version": VERSION,
        "validators": ["claude"],
        "primary": "claude",
        "timeout": 480,
        "parallel": True,
        "learned_adapters": {"claude": adapter_entry},
    }
    Path(global_cfg_path).parent.mkdir(parents=True, exist_ok=True)
    with open(global_cfg_path, "w") as f:
        json.dump(global_data, f)

    # Write project config also containing a learned_adapters key (should be ignored)
    project_cfg_path = project_path(cwd)
    project_data = {
        "version": VERSION,
        "validators": ["claude"],
        "primary": "claude",
        "timeout": 120,
        "parallel": False,
        "learned_adapters": {
            "opencode": {
                "argv_template": ["opencode", "run", "{prompt}"],
                "stdin_mode": "none",
                "stdout_parser": "plaintext",
                "timestamp": 1700000000.0,
                "source_cli": "claude",
                "confidence": 0.8,
            }
        },
    }
    Path(project_cfg_path).parent.mkdir(parents=True, exist_ok=True)
    with open(project_cfg_path, "w") as f:
        json.dump(project_data, f)

    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    for var in ("LOPE_VALIDATORS", "LOPE_PRIMARY", "LOPE_TIMEOUT",
                "LOPE_PARALLEL", "LOPE_SEQUENTIAL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("lope.config.default_path", lambda: global_cfg_path)

    cfg = load_layered(cwd=cwd)

    # Only the global learned adapter must be present; project's must be ignored
    assert "claude" in cfg.learned_adapters, \
        "Global learned adapter must be present"
    assert "opencode" not in cfg.learned_adapters, \
        "Project config learned_adapters must NOT be inherited"
