from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from lope.jobs import RunRegistry


def _run_main(monkeypatch, *argv):
    from lope import cli

    monkeypatch.setattr(sys, "argv", ["lope", *argv])
    try:
        cli.main()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)


def test_jobs_list_json_has_stable_resource_fields(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("LOPE_HOME", str(home))
    registry = RunRegistry(home / "runs")
    registry.start_run("ask", run_id="active")

    code = _run_main(monkeypatch, "jobs", "list", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema_version"] == 1
    assert payload["jobs"][0]["classification"] == "active"
    resources = payload["jobs"][0]["resources"]
    assert set(("process_count", "cpu_percent", "rss_bytes", "pids", "available")) <= set(resources)
    assert os.getpid() in resources["pids"]


def test_jobs_reap_dry_run_mutates_nothing_then_real_reaps(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    monkeypatch.setenv("LOPE_HOME", str(home))
    registry = RunRegistry(home / "runs")
    registry.start_run("ask", run_id="abandoned")
    registry.update(
        "abandoned",
        lambda value: {
            **value,
            "owner": {"pid": 99999999, "start_fingerprint": "gone"},
        },
    )
    path = registry.active_dir / "abandoned.json"
    before = path.read_bytes()

    assert _run_main(monkeypatch, "jobs", "reap", "--dry-run", "--json") == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["actions"][0]["action"] == "dry_run"
    assert path.read_bytes() == before

    assert _run_main(monkeypatch, "jobs", "reap", "--json") == 0
    real = json.loads(capsys.readouterr().out)
    assert real["actions"][0]["action"] == "reaped"
    assert not path.exists()


def test_jobs_kill_refuses_reused_owner_fingerprint(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    monkeypatch.setenv("LOPE_HOME", str(home))
    registry = RunRegistry(home / "runs")
    registry.start_run("review", run_id="reuse")
    registry.update(
        "reuse",
        lambda value: {
            **value,
            "owner": {"pid": os.getpid(), "start_fingerprint": "reused"},
        },
    )

    code = _run_main(monkeypatch, "jobs", "kill", "reuse", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["result"]["action"] == "refused"
    assert "fingerprint" in payload["result"]["reason"]
    assert (registry.active_dir / "reuse.json").exists()


def test_jobs_kill_missing_run_is_typed_refusal(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path / "home"))
    code = _run_main(monkeypatch, "jobs", "kill", "missing", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["result"]["classification"] == "ownership_unverified"
    assert payload["result"]["action"] == "refused"


def test_jobs_commands_never_recommend_process_name_matching():
    source = Path(__file__).resolve().parents[1] / "lope" / "cli.py"
    jobs_section = source.read_text(encoding="utf-8").split("def _cmd_jobs", 1)[1]
    jobs_section = jobs_section.split("def _cmd_configure", 1)[0]
    assert "pkill" not in jobs_section
    assert "killall" not in jobs_section
