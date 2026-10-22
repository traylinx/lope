"""Resource selection and maintenance error paths; real artifacts use check-package.py."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lope import cli, resources


def make_assets(root):
    for name in ("install", "skills/lope/SKILL.md", "docs/reference.md"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture reference", encoding="utf-8")
    (root / "commands").mkdir()
    return root


def test_bundled_resources_win_over_source(tmp_path, monkeypatch):
    make_assets(tmp_path)
    bundled = make_assets(tmp_path / "lope" / "_assets")
    monkeypatch.setattr(resources, "__file__", str(tmp_path / "lope" / "resources.py"))
    assert resources.resource_root() == bundled


def test_source_resources_resolved_without_cwd(tmp_path, monkeypatch):
    make_assets(tmp_path)
    monkeypatch.setattr(resources, "__file__", str(tmp_path / "lope" / "resources.py"))
    monkeypatch.chdir(tmp_path.parent)
    assert resources.resource_root() == tmp_path


def test_missing_resources_never_use_unrelated_home(tmp_path, monkeypatch):
    make_assets(tmp_path / ".lope")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(resources, "__file__", str(tmp_path / "site-packages/lope/resources.py"))
    with pytest.raises(FileNotFoundError, match="missing bundled host resources"):
        resources.resource_root()


def test_docs_from_matching_version(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(resources, "resource_root", lambda: make_assets(tmp_path))
    cli._cmd_docs()
    assert capsys.readouterr().out.strip() == "fixture reference"


@pytest.mark.parametrize("command", ["docs", "install"])
def test_missing_resources_actionable_error(monkeypatch, capsys, command):
    def missing():
        raise FileNotFoundError("missing bundled host resources")
    monkeypatch.setattr(resources, "resource_root", missing)
    with pytest.raises(SystemExit) as exc:
        cli._cmd_docs() if command == "docs" else cli._cmd_install("all")
    assert exc.value.code == 1
    assert "missing bundled host resources" in capsys.readouterr().err


def test_installer_uses_bash_bound_python_and_bundled_engine(tmp_path, monkeypatch):
    root = make_assets(tmp_path)
    monkeypatch.setattr(resources, "resource_root", lambda: root)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    with pytest.raises(SystemExit) as exc:
        cli._cmd_install("codex")
    assert exc.value.code == 0
    args, kwargs = calls[0]
    assert args[0] == ["bash", str(root / "install"), "--host", "codex"]
    assert kwargs["env"]["LOPE_PACKAGED_INSTALL"] == "1"
    assert kwargs["env"]["LOPE_PYTHON"] == cli.sys.executable
    assert kwargs["env"]["LOPE_ENGINE_ROOT"] == str(Path(cli.__file__).resolve().parent.parent)
    assert kwargs["timeout"] == 600


@pytest.mark.parametrize("error,code", [(OSError("no bash"), 1),
                                         (subprocess.TimeoutExpired("bash", 600), 124)])
def test_installer_failures_do_not_mask_original_error(tmp_path, monkeypatch, capsys, error, code):
    monkeypatch.setattr(resources, "resource_root", lambda: make_assets(tmp_path))
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(SystemExit) as exc:
        cli._cmd_install("all")
    assert exc.value.code == code
    assert "Install failed" in capsys.readouterr().err
