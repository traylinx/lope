"""Codex CLI compatibility regression tests.

codex 0.125.0 (released 2026-04-29) added a trusted-directory gate that
exits 1 with ``Not inside a trusted directory and --skip-git-repo-check
was not specified`` when run from a CWD that isn't in its trust list.

Lope is intentionally invoked from arbitrary project directories (the
user's CWD, ``LOPE_WORKDIR``, test fixtures). We pass
``--skip-git-repo-check`` so the trust gate doesn't block legitimate
invocations.

These tests pin the argv shape so a future refactor or a regression in
the validators module is caught before it lands as a silent INFRA_ERROR
on every codex round.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lope.validators import CodexValidator


def _stubbed_proc(returncode: int = 0, stdout: str = "ok", stderr: str = ""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_codex_generate_passes_skip_git_repo_check():
    validator = CodexValidator(binary="codex")
    with patch("lope.validators.subprocess.run", return_value=_stubbed_proc()) as m:
        with patch.object(validator, "available", return_value=True):
            validator.generate("hello", timeout=60)
    assert m.called
    argv = m.call_args.args[0]
    assert argv[0] == "codex"
    assert argv[1] == "exec"
    assert "--skip-git-repo-check" in argv, (
        f"codex argv missing --skip-git-repo-check; got {argv!r}. "
        f"codex 0.125.0+ refuses to run without this flag from "
        f"untrusted directories."
    )
    # The prompt itself must still be the final positional arg.
    assert argv[-1] == "hello"


def test_codex_validate_passes_skip_git_repo_check():
    validator = CodexValidator(binary="codex")
    with patch(
        "lope.validators.subprocess.run",
        return_value=_stubbed_proc(stdout="---VERDICT---\nstatus: PASS\nconfidence: 0.95\nrationale: ok\n---END---"),
    ) as m:
        with patch.object(validator, "available", return_value=True):
            validator.validate("hello", timeout=60)
    assert m.called
    argv = m.call_args.args[0]
    assert "--skip-git-repo-check" in argv, (
        f"codex argv (validate) missing --skip-git-repo-check; got {argv!r}"
    )
