"""Tests for `lope update` self-update planning."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import lope.update as update
from lope.update import UpdateError, detect_method, run_update


def _git(cmd, cwd: Path) -> None:
    subprocess.run(["git", *cmd], cwd=str(cwd), check=True, capture_output=True)


def test_detect_method_prefers_git_checkout(tmp_path):
    _git(["init"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')
    assert detect_method(tmp_path) == "git"


def test_detect_method_falls_back_to_pip_outside_git(tmp_path):
    assert detect_method(tmp_path) == "pip"


def test_detect_method_ignores_unrelated_parent_git_repo(tmp_path):
    _git(["init"], tmp_path)
    site_packages = tmp_path / ".venv" / "lib" / "python3.11" / "site-packages"
    package_root = site_packages / "lope"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text('__version__ = "1.2.3"\n')

    assert detect_method(site_packages) == "pip"


def test_forced_git_rejects_unrelated_parent_git_repo(tmp_path):
    _git(["init"], tmp_path)
    site_packages = tmp_path / ".venv" / "lib" / "python3.11" / "site-packages"
    package_root = site_packages / "lope"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text('__version__ = "1.2.3"\n')

    try:
        run_update(method="git", dry_run=True, root=site_packages, emit=lambda _: None)
    except UpdateError as exc:
        assert "not a Lope git checkout" in str(exc)
    else:
        raise AssertionError("expected unrelated parent repo to be rejected")


def test_detect_method_ignores_venv_inside_lope_checkout(tmp_path):
    _git(["init"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')
    site_packages = tmp_path / ".venv" / "lib" / "python3.11" / "site-packages"
    package_root = site_packages / "lope"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text('__version__ = "1.2.3"\n')

    assert detect_method(site_packages) == "pip"


def test_git_detection_reports_missing_git_for_lope_checkout(tmp_path, monkeypatch):
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')

    def fake_capture(cmd, cwd=None):
        if "rev-parse" in cmd:
            raise UpdateError("cannot execute git: missing")
        return ""

    monkeypatch.setattr(update, "_capture", fake_capture)

    try:
        detect_method(tmp_path)
    except UpdateError as exc:
        assert "cannot execute git" in str(exc)
    else:
        raise AssertionError("expected missing git to be reported for source checkout")


def test_git_update_dry_run_prints_pull_and_install(tmp_path):
    _git(["init"], tmp_path)
    _git(["checkout", "-b", "main"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')

    lines = []
    result = run_update(
        method="git",
        dry_run=True,
        host="codex",
        root=tmp_path,
        emit=lines.append,
    )

    assert result.dry_run is True
    assert result.method == "git"
    assert result.before_version == "1.2.3"
    rendered = "\n".join(lines)
    assert "git -C" in rendered
    assert "pull --ff-only origin main" in rendered
    assert "bash" in rendered
    assert "--host codex" in rendered


def test_git_update_dry_run_can_skip_skill_install(tmp_path):
    _git(["init"], tmp_path)
    _git(["checkout", "-b", "main"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')

    lines = []
    result = run_update(
        method="git",
        dry_run=True,
        reinstall_skills=False,
        root=tmp_path,
        emit=lines.append,
    )

    rendered = "\n".join(lines)
    assert "pull --ff-only" in rendered
    assert all("install" not in cmd for cmd in result.commands)


def test_git_update_rejects_unknown_host_before_mutation(tmp_path):
    _git(["init"], tmp_path)
    _git(["checkout", "-b", "main"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')

    try:
        run_update(method="git", dry_run=True, host="codeex", root=tmp_path, emit=lambda _: None)
    except UpdateError as exc:
        assert "unknown install host" in str(exc)
    else:
        raise AssertionError("expected invalid host to raise")


def test_git_update_rejects_missing_installer_when_reinstall_requested(tmp_path):
    _git(["init"], tmp_path)
    _git(["checkout", "-b", "main"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')

    try:
        run_update(method="git", dry_run=True, root=tmp_path, emit=lambda _: None)
    except UpdateError as exc:
        assert "install script not found" in str(exc)
    else:
        raise AssertionError("expected missing installer to raise")


def test_git_update_rejects_tracked_dirty_checkout(tmp_path, monkeypatch):
    _git(["init"], tmp_path)
    _git(["checkout", "-b", "main"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')

    executed = []
    monkeypatch.setattr(update, "_tracked_dirty", lambda root: " M lope/__init__.py")
    monkeypatch.setattr(update, "_run", lambda cmd, cwd=None: executed.append(cmd))

    try:
        run_update(method="git", root=tmp_path, emit=lambda _: None)
    except UpdateError as exc:
        assert "tracked changes" in str(exc)
    else:
        raise AssertionError("expected tracked dirty checkout to be rejected")
    assert executed == []


def test_git_update_allow_dirty_executes_commands(tmp_path, monkeypatch):
    _git(["init"], tmp_path)
    _git(["checkout", "-b", "main"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')

    executed = []
    monkeypatch.setattr(update, "_tracked_dirty", lambda root: " M lope/__init__.py")
    monkeypatch.setattr(update, "_run", lambda cmd, cwd=None: executed.append(cmd))

    run_update(method="git", root=tmp_path, allow_dirty=True, emit=lambda _: None)

    assert len(executed) == 3


def test_git_update_executes_explicit_remote_branch_sequence(tmp_path, monkeypatch):
    _git(["init"], tmp_path)
    _git(["checkout", "-b", "main"], tmp_path)
    (tmp_path / "lope").mkdir()
    (tmp_path / "lope" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "install").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "pyproject.toml").write_text('name = "lope-agent"\n')

    executed = []

    def fake_run(cmd, cwd=None):
        executed.append((cmd, cwd))

    monkeypatch.setattr(update, "_run", fake_run)

    run_update(method="git", root=tmp_path, host="codex", emit=lambda _: None)

    rendered = [" ".join(cmd) for cmd, _ in executed]
    assert "git -C " + str(tmp_path) + " fetch --tags origin" in rendered
    assert "git -C " + str(tmp_path) + " pull --ff-only origin main" in rendered
    assert "bash " + str(tmp_path / "install") + " --host codex" in rendered


def test_pip_update_dry_run_plans_pip_upgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "_python_version", lambda root=None: "1.2.3")
    lines = []

    result = run_update(method="pip", dry_run=True, root=tmp_path, emit=lines.append)

    assert result.method == "pip"
    assert result.commands == [[sys.executable, "-m", "pip", "install", "--upgrade", "lope-agent"]]
    assert "pip install --upgrade lope-agent" in "\n".join(lines)


def test_pip_update_rejects_git_only_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "_python_version", lambda root=None: "1.2.3")

    cases = [
        {"host": "codex"},
        {"allow_dirty": True},
    ]
    for kwargs in cases:
        try:
            run_update(method="pip", dry_run=True, root=tmp_path, emit=lambda _: None, **kwargs)
        except UpdateError as exc:
            assert "only applies to git checkout updates" in str(exc)
        else:
            raise AssertionError(f"expected pip mode to reject {kwargs}")


def test_pip_update_skip_install_is_noop_note(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "_python_version", lambda root=None: "1.2.3")
    lines = []

    run_update(
        method="pip",
        dry_run=True,
        root=tmp_path,
        reinstall_skills=False,
        emit=lines.append,
    )

    assert "--skip-install is already implicit" in "\n".join(lines)


def test_update_and_upgrade_help_are_registered():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for verb in ("update", "upgrade"):
        proc = subprocess.run(
            [sys.executable, "-m", "lope", verb, "--help"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert proc.returncode == 0, proc.stderr
        assert "--dry-run" in proc.stdout
        assert "--skip-install" in proc.stdout


def test_python_install_command_propagates_installer_failure():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    proc = subprocess.run(
        [sys.executable, "-m", "lope", "install", "--host", "badhost"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert proc.returncode != 0
    assert "Unknown host: badhost" in proc.stdout


def test_python_install_command_propagates_host_filesystem_failure(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    fake_home = tmp_path / "not-a-dir"
    fake_home.write_text("file, not directory\n")
    env["HOME"] = str(fake_home)

    proc = subprocess.run(
        [sys.executable, "-m", "lope", "install", "--host", "codex"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )

    assert proc.returncode != 0
    assert "Not a directory" in proc.stderr or "failed" in proc.stdout
