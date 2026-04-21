"""Tests for MakakooAdapterValidator — lope ↔ Makakoo OS universal bridge.

Stubs `makakoo` on PATH with a tiny script that echoes a canned JSON
ValidatorResult. Proves:

  - MakakooAdapterValidator shells out correctly and hydrates the JSON
  - Missing binary → INFRA_ERROR, never a crash
  - Nonzero exit from the CLI → INFRA_ERROR with stderr surfaced
  - Malformed JSON → INFRA_ERROR
  - enumerate_registered_adapters() reads ~/.makakoo/adapters/registered/
  - build_validator_pool resolves a registered adapter name automatically
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lope.models import VerdictStatus
from lope.makakoo_adapter import (
    MakakooAdapterValidator,
    enumerate_registered_adapters,
)


def _install_fake_makakoo(tmp_path: Path, stdout_body: str, exit_code: int = 0) -> Path:
    """Drop a fake `makakoo` binary that prints a canned payload."""
    binpath = tmp_path / "makakoo"
    script = (
        "#!/usr/bin/env sh\n"
        f'cat <<__EOF__\n{stdout_body}\n__EOF__\n'
        f"exit {exit_code}\n"
    )
    binpath.write_text(script)
    binpath.chmod(binpath.stat().st_mode | stat.S_IEXEC)
    return binpath


def _canned_result(status: str = "PASS", confidence: float = 0.9):
    return json.dumps(
        {
            "validator_name": "openclaw",
            "verdict": {
                "status": status,
                "confidence": confidence,
                "rationale": "looks good",
                "required_fixes": [],
                "nice_to_have": [],
                "duration_seconds": 1.23,
                "validator_name": "openclaw",
                "stage": None,
                "evidence_gate_triggered": False,
            },
            "raw_response": "---VERDICT---\nstatus: PASS\n---END---",
            "error": "",
            "flag_error_hint": "",
        }
    )


def test_happy_path_hydrates_validator_result(tmp_path, monkeypatch):
    binpath = _install_fake_makakoo(tmp_path, _canned_result("PASS", 0.9))
    monkeypatch.setenv("MAKAKOO_BIN", str(binpath))
    v = MakakooAdapterValidator(adapter_name="openclaw")
    r = v.validate("test prompt", timeout=5)
    assert r.verdict.status == VerdictStatus.PASS
    assert abs(r.verdict.confidence - 0.9) < 1e-9
    assert r.verdict.rationale == "looks good"
    assert r.validator_name == "openclaw"


def test_needs_fix_propagates(tmp_path, monkeypatch):
    binpath = _install_fake_makakoo(tmp_path, _canned_result("NEEDS_FIX", 0.7))
    monkeypatch.setenv("MAKAKOO_BIN", str(binpath))
    v = MakakooAdapterValidator(adapter_name="openclaw")
    r = v.validate("p", timeout=5)
    assert r.verdict.status == VerdictStatus.NEEDS_FIX


def test_missing_binary_is_infra_error(monkeypatch):
    monkeypatch.setenv("MAKAKOO_BIN", "/totally/not/a/path/makakoo")
    monkeypatch.setenv("PATH", "")  # nothing on PATH
    v = MakakooAdapterValidator(adapter_name="openclaw")
    r = v.validate("p", timeout=5)
    assert r.verdict.status == VerdictStatus.INFRA_ERROR
    assert "makakoo" in r.error


def test_cli_exit_nonzero_is_infra_error(tmp_path, monkeypatch):
    binpath = tmp_path / "makakoo"
    binpath.write_text(
        "#!/usr/bin/env sh\necho 'no adapter named foo' 1>&2\nexit 1\n"
    )
    binpath.chmod(binpath.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("MAKAKOO_BIN", str(binpath))
    v = MakakooAdapterValidator(adapter_name="foo")
    r = v.validate("p", timeout=5)
    assert r.verdict.status == VerdictStatus.INFRA_ERROR
    assert "exit 1" in r.error or "no adapter" in r.error


def test_malformed_json_is_infra_error(tmp_path, monkeypatch):
    binpath = _install_fake_makakoo(tmp_path, "not valid json")
    monkeypatch.setenv("MAKAKOO_BIN", str(binpath))
    v = MakakooAdapterValidator(adapter_name="openclaw")
    r = v.validate("p", timeout=5)
    assert r.verdict.status == VerdictStatus.INFRA_ERROR
    assert "JSON" in r.error or "json" in r.error.lower()


def test_enumerate_registered_adapters_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MAKAKOO_ADAPTERS_HOME", str(tmp_path))
    assert enumerate_registered_adapters() == []


def test_enumerate_registered_adapters_finds_registered(tmp_path, monkeypatch):
    reg_dir = tmp_path / "registered"
    reg_dir.mkdir(parents=True)
    (reg_dir / "openclaw.toml").write_text("# stub\n")
    (reg_dir / "hermes.toml").write_text("# stub\n")
    (reg_dir / "README.md").write_text("# ignored\n")
    monkeypatch.setenv("MAKAKOO_ADAPTERS_HOME", str(tmp_path))
    assert enumerate_registered_adapters() == ["hermes", "openclaw"]


def test_disable_flag_hides_adapters(tmp_path, monkeypatch):
    reg_dir = tmp_path / "registered"
    reg_dir.mkdir(parents=True)
    (reg_dir / "openclaw.toml").write_text("# stub\n")
    monkeypatch.setenv("MAKAKOO_ADAPTERS_HOME", str(tmp_path))
    monkeypatch.setenv("LOPE_MAKAKOO_ADAPTERS", "0")
    v = MakakooAdapterValidator(adapter_name="openclaw")
    assert not v.available()


def test_build_validator_pool_picks_up_registered_adapter(tmp_path, monkeypatch):
    """End-to-end: config names 'openclaw'; validator pool resolves it
    to MakakooAdapterValidator via the 4th tier — no cfg.providers entry."""
    reg_dir = tmp_path / "registered"
    reg_dir.mkdir(parents=True)
    (reg_dir / "openclaw.toml").write_text("# stub\n")

    binpath = _install_fake_makakoo(tmp_path, _canned_result("PASS", 0.9))
    monkeypatch.setenv("MAKAKOO_BIN", str(binpath))
    monkeypatch.setenv("MAKAKOO_ADAPTERS_HOME", str(tmp_path))
    monkeypatch.setenv("LOPE_MAKAKOO_ADAPTERS", "1")

    from lope.config import LopeCfg
    from lope.validators import build_validator_pool

    cfg = LopeCfg(
        validators=["openclaw"],
        primary="openclaw",
        timeout=30,
        parallel=False,
        providers=[],
    )
    pool = build_validator_pool(cfg)
    # Primary validator resolves via the 4th tier (Makakoo adapter).
    assert pool.primary_validator().name == "openclaw"
    assert pool.primary_validator().__class__.__name__ == "MakakooAdapterValidator"
