"""Self-update helpers for the Lope CLI."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple


SUPPORTED_INSTALL_HOSTS = {
    "all",
    "claude",
    "codex",
    "gemini",
    "opencode",
    "cursor",
    "vibe",
    "qwen",
    "pi",
}
UPDATE_OPERATION_TIMEOUT_SECONDS = 600


class UpdateError(RuntimeError):
    """Raised for operator-actionable update failures."""


@dataclass
class UpdateResult:
    method: str
    root: Optional[Path]
    before_version: str
    after_version: str
    commands: List[List[str]]
    dry_run: bool = False


def package_root() -> Path:
    """Return the directory that contains the ``lope/`` package."""
    return Path(__file__).resolve().parents[1]


def _quote_command(cmd: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in cmd)


def _capture(cmd: Sequence[str], *, cwd: Optional[Path] = None) -> str:
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            check=False,
            timeout=UPDATE_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            f"`{_quote_command(cmd)}` exceeded the {UPDATE_OPERATION_TIMEOUT_SECONDS}s "
            "maintenance timeout"
        ) from exc
    except OSError as exc:
        raise UpdateError(f"cannot execute {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise UpdateError(f"`{_quote_command(cmd)}` failed: {detail}")
    return (proc.stdout or "").strip()


def _run(cmd: Sequence[str], *, cwd: Optional[Path] = None) -> None:
    try:
        proc = subprocess.run(
            list(cmd), cwd=str(cwd) if cwd else None, check=False,
            timeout=UPDATE_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            f"`{_quote_command(cmd)}` exceeded the {UPDATE_OPERATION_TIMEOUT_SECONDS}s "
            "maintenance timeout"
        ) from exc
    except OSError as exc:
        raise UpdateError(f"cannot execute {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise UpdateError(f"`{_quote_command(cmd)}` exited with {proc.returncode}")


def _git_root(start: Path) -> Optional[Path]:
    try:
        out = _capture(["git", "-C", str(start), "rev-parse", "--show-toplevel"])
    except UpdateError as exc:
        message = str(exc)
        if "cannot execute git" in message:
            if _is_lope_checkout(start):
                raise
            return None
        if "not a git repository" in message or "not a gitdir" in message:
            return None
        raise
    return Path(out).resolve()


def _is_lope_checkout(root: Path) -> bool:
    """Return true only for an actual Lope source checkout root."""
    init_path = root / "lope" / "__init__.py"
    pyproject = root / "pyproject.toml"
    if not (init_path.is_file() and pyproject.is_file()):
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return 'name = "lope-agent"' in text


def _package_matches_checkout(root: Path, git_root: Path) -> bool:
    """Return true when the running package path is the git checkout package."""
    try:
        return (root / "lope").resolve() == (git_root / "lope").resolve()
    except OSError:
        return False


def _git_branch(root: Path) -> str:
    try:
        return _capture(["git", "-C", str(root), "symbolic-ref", "-q", "--short", "HEAD"])
    except UpdateError as exc:
        raise UpdateError("git checkout is detached; cannot self-update safely") from exc


def _git_pull_target(root: Path, branch: str) -> Tuple[str, str]:
    """Return the remote + branch that `git pull` should use explicitly."""
    try:
        upstream = _capture(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ]
        )
    except UpdateError:
        return "origin", branch
    if "/" not in upstream:
        return "origin", branch
    remote, remote_branch = upstream.split("/", 1)
    return remote, remote_branch or branch


def _tracked_dirty(root: Path) -> str:
    return _capture(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"])


def _read_version(root: Optional[Path] = None) -> str:
    if root is None:
        root = package_root()
    init_path = root / "lope" / "__init__.py"
    try:
        text = init_path.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    return match.group(1) if match else "unknown"


def _python_version(root: Optional[Path] = None) -> str:
    if root is None:
        try:
            out = _capture([sys.executable, "-m", "lope", "version"])
            match = re.search(r"v(\d+\.\d+\.\d+)", out)
            return match.group(1) if match else "unknown"
        except UpdateError:
            return "unknown"
    return _read_version(root)


def _emit_commands(emit: Callable[[str], None], commands: Iterable[Sequence[str]]) -> None:
    for cmd in commands:
        emit(f"  $ {_quote_command(cmd)}")


def _validate_host(host: str) -> None:
    if host not in SUPPORTED_INSTALL_HOSTS:
        allowed = ", ".join(sorted(SUPPORTED_INSTALL_HOSTS))
        raise UpdateError(f"unknown install host `{host}`; expected one of: {allowed}")


def _git_commands(
    root: Path,
    *,
    reinstall_skills: bool,
    host: str,
    remote: str,
    remote_branch: str,
) -> List[List[str]]:
    commands: List[List[str]] = [
        ["git", "-C", str(root), "fetch", "--tags", remote],
        ["git", "-C", str(root), "pull", "--ff-only", remote, remote_branch],
    ]
    installer = root / "install"
    if reinstall_skills:
        _validate_host(host)
        if not installer.exists():
            raise UpdateError(f"install script not found at {installer}; cannot refresh host skills")
        commands.append(["bash", str(installer), "--host", host])
    return commands


def _pip_commands() -> List[List[str]]:
    return [[sys.executable, "-m", "pip", "install", "--upgrade", "lope-agent"]]


def detect_method(root: Optional[Path] = None, forced: str = "auto") -> str:
    """Return ``git`` or ``pip`` for the current Lope install."""
    if forced not in {"auto", "git", "pip"}:
        raise UpdateError(f"unknown update method: {forced}")
    if forced != "auto":
        return forced
    root = root or package_root()
    git_root = _git_root(root)
    if (
        git_root is not None
        and _is_lope_checkout(git_root)
        and _package_matches_checkout(root.resolve(), git_root)
    ):
        return "git"
    return "pip"


def run_update(
    *,
    method: str = "auto",
    dry_run: bool = False,
    reinstall_skills: bool = True,
    host: str = "all",
    allow_dirty: bool = False,
    root: Optional[Path] = None,
    emit: Callable[[str], None] = print,
) -> UpdateResult:
    """Update Lope and optionally refresh installed host skills."""
    root = (root or package_root()).resolve()
    resolved_method = detect_method(root, method)

    if resolved_method == "git":
        git_root = _git_root(root)
        if (
            git_root is None
            or not _is_lope_checkout(git_root)
            or not _package_matches_checkout(root, git_root)
        ):
            raise UpdateError("not a Lope git checkout; use `lope update --method pip`")
        before = _read_version(git_root)
        branch = _git_branch(git_root)
        remote, remote_branch = _git_pull_target(git_root, branch)
        dirty = _tracked_dirty(git_root)
        commands = _git_commands(
            git_root,
            reinstall_skills=reinstall_skills,
            host=host,
            remote=remote,
            remote_branch=remote_branch,
        )
        emit(f"# install method: git ({git_root})")
        emit(f"# branch: {branch}")
        emit(f"# update source: {remote}/{remote_branch}")
        if dirty:
            emit("# tracked changes detected:")
            for line in dirty.splitlines():
                emit(f"#   {line}")
            if not dry_run and not allow_dirty:
                raise UpdateError(
                    "tracked changes would make `git pull --ff-only` unsafe; "
                    "commit/stash them or pass --allow-dirty"
                )
        if dry_run:
            emit("# DRY RUN — would execute:")
            _emit_commands(emit, commands)
            emit("# dry-run complete — 0 actions executed")
            return UpdateResult(resolved_method, git_root, before, before, commands, True)
        for cmd in commands:
            emit(f"$ {_quote_command(cmd)}")
            _run(cmd, cwd=git_root)
        after = _read_version(git_root)
        emit("# version delta:")
        emit(f"  before: lope {before}")
        emit(f"  after:  lope {after}")
        if reinstall_skills:
            emit("# host skills refreshed")
        return UpdateResult(resolved_method, git_root, before, after, commands, False)

    if host != "all":
        raise UpdateError("`--host` only applies to git checkout updates")
    if not reinstall_skills:
        emit("# note: --skip-install is already implicit for pip updates")
    if allow_dirty:
        raise UpdateError("`--allow-dirty` only applies to git checkout updates")
    before = _python_version(None)
    commands = _pip_commands()
    emit("# install method: pip (lope-agent)")
    emit("# WARNING: PyPI publishing is not live yet (Trusted Publisher pending); git checkout is the supported path")
    if dry_run:
        emit("# DRY RUN — would execute:")
        _emit_commands(emit, commands)
        emit("# dry-run complete — 0 actions executed")
        return UpdateResult(resolved_method, None, before, before, commands, True)
    for cmd in commands:
        emit(f"$ {_quote_command(cmd)}")
        _run(cmd)
    after = _python_version(None)
    emit("# version delta:")
    emit(f"  before: lope {before}")
    emit(f"  after:  lope {after}")
    emit("# pip update complete; host-skill refresh is available from the git checkout installer")
    return UpdateResult(resolved_method, None, before, after, commands, False)
