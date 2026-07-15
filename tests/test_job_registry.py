from __future__ import annotations

import json
import os
import time
import pytest

from lope.jobs import RegistryError, RunRegistry, identity_matches, process_identity


def test_start_run_writes_private_atomic_manifest(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    manifest = registry.start_run("ask", run_id="run1", run_timeout=60)
    path = registry.active_dir / "run1.json"
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert manifest["schema_version"] == 1
    assert manifest["owner"]["pid"] == os.getpid()
    assert json.loads(path.read_text())["run_id"] == "run1"
    assert not list(registry.active_dir.glob("*.tmp"))


def test_manifest_rejects_raw_prompt_and_output(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    registry.start_run("ask", run_id="run1")
    with pytest.raises(RegistryError, match="forbidden"):
        registry.register_call("run1", {"call_id": "c1", "prompt": "secret"})
    with pytest.raises(RegistryError, match="forbidden|unsupported"):
        registry.register_call("run1", {"call_id": "c1", "raw_response": "secret"})


def test_pid_start_fingerprint_prevents_reuse(monkeypatch):
    identity = process_identity(os.getpid())
    assert identity_matches(identity)
    monkeypatch.setattr("lope.jobs.process_start_fingerprint", lambda _pid: "different")
    assert not identity_matches(identity)


def test_live_owner_stale_heartbeat_warns_not_abandoned(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    registry.start_run("ask", run_id="run1")

    def stale(manifest):
        manifest["heartbeat_at"] = time.time() - 100
        return manifest

    manifest = registry.update("run1", stale)
    assert registry.classify(manifest, stale_after=10) == "unresponsive"


def test_dead_or_reused_owner_is_abandoned(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    registry.start_run("ask", run_id="run1")

    def dead(manifest):
        manifest["owner"] = {"pid": 99999999, "start_fingerprint": "gone"}
        return manifest

    manifest = registry.update("run1", dead)
    assert registry.classify(manifest) == "abandoned"


def test_cleanup_is_confined_and_rejects_symlink(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    registry.start_run("ask", run_id="run1")
    work = registry.run_work_dir("run1")
    owned = work / "spool.txt"
    owned.write_text("x")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    link = work / "link"
    link.symlink_to(outside)

    result = registry.cleanup_owned_paths("run1", [str(owned), str(outside), str(link)])
    by_path = {item["path"]: item["result"] for item in result}
    assert by_path[str(owned)] == "removed"
    assert by_path[str(outside)] == "cleanup_path_rejected"
    assert by_path[str(link)] == "cleanup_path_rejected"
    assert outside.read_text() == "keep"


def test_finish_moves_manifest_and_prunes_work_separately(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    registry.start_run("ask", run_id="run1")
    completed = registry.finish_run("run1")
    assert completed.exists()
    assert not (registry.active_dir / "run1.json").exists()
    assert json.loads(completed.read_text())["state"] == "completed"


def test_corrupt_manifest_fails_closed_without_process_action(tmp_path):
    registry = RunRegistry(tmp_path / "runs")
    bad = registry.active_dir / "bad.json"
    bad.write_text("{")
    rows = registry.list_active()
    assert rows[0]["classification"] == "ownership_unverified"
    assert "invalid run manifest" in rows[0]["reason"]


def test_jobs_list_cli_is_registered_and_read_only(tmp_path, monkeypatch, capsys):
    from lope import cli

    monkeypatch.setenv("LOPE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("sys.argv", ["lope", "jobs", "list", "--json"])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["jobs"] == []
