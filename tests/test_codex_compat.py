"""Codex CLI compatibility regression tests.

codex 0.125.0 (released 2026-04-29) added a trusted-directory gate that
exits 1 with ``Not inside a trusted directory and --skip-git-repo-check
was not specified`` when run from a CWD that isn't in its trust list.

Lope is intentionally invoked from arbitrary project directories (the
user's CWD, ``LOPE_WORKDIR``, test fixtures). We pass
``--skip-git-repo-check`` so the trust gate doesn't block legitimate
invocations. Codex is also launched with ``--ignore-user-config`` and a
read-only, low-reasoning profile so user MCP startup failures and interactive
defaults do not poison validator calls. Prompts travel on stdin so they do not
appear in process listings or hit argv limits.

These tests pin the argv shape so a future refactor or a regression in
the validators module is caught before it lands as a silent INFRA_ERROR
on every codex round.
"""

from __future__ import annotations

from types import SimpleNamespace
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
    with patch("lope.validators._run_with_group_kill", return_value=(_stubbed_proc(), 0.1)) as m:
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
    assert "--ignore-user-config" in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="low"'
    assert "hello" not in argv
    assert m.call_args.kwargs["input_text"] == "hello"


def test_codex_validate_passes_skip_git_repo_check():
    validator = CodexValidator(binary="codex")
    with patch(
        "lope.validators._run_with_group_kill",
        return_value=(_stubbed_proc(stdout="---VERDICT---\nstatus: PASS\nconfidence: 0.95\nrationale: ok\n---END---"), 0.1),
    ) as m:
        with patch.object(validator, "available", return_value=True):
            validator.validate("hello", timeout=60)
    assert m.called
    argv = m.call_args.args[0]
    assert "--skip-git-repo-check" in argv, (
        f"codex argv (validate) missing --skip-git-repo-check; got {argv!r}"
    )
    assert "--ignore-user-config" in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="low"'
    assert "hello" not in argv
    assert "hello" in m.call_args.kwargs["input_text"]


def test_codex_implementation_context_uses_workspace_write_only_for_generate():
    validator = CodexValidator(binary="codex")
    context = SimpleNamespace(metadata={"implementation": True})
    with patch("lope.validators._run_with_group_kill", return_value=(_stubbed_proc(), 0.1)) as m:
        with patch.object(validator, "available", return_value=True):
            validator.generate("implement", timeout=60, context=context)
    argv = m.call_args.args[0]
    assert argv[argv.index("-s") + 1] == "workspace-write"

    with patch(
        "lope.validators._run_with_group_kill",
        return_value=(
            _stubbed_proc(
                stdout="---VERDICT---\nstatus: PASS\nconfidence: 1\nrationale: ok\n---END---"
            ),
            0.1,
        ),
    ) as validate_call:
        with patch.object(validator, "available", return_value=True):
            validator.validate("review", timeout=60, context=context)
    validate_argv = validate_call.call_args.args[0]
    assert validate_argv[validate_argv.index("-s") + 1] == "read-only"
