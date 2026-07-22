from __future__ import annotations

import subprocess
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from lope.validators import (
    ClaudeCodeValidator,
    CodexValidator,
    _bounded_stream,
    _claude_extra_args,
    _is_infrastructure_exit,
    _run_with_group_kill,
)
from lope.provider_errors import ProviderInfrastructureError
from lope.runtime import InvocationContext, RunBudget


def test_claude_extra_args_are_shell_split(monkeypatch):
    monkeypatch.setenv(
        "LOPE_CLAUDE_ARGS",
        "--settings '{\"env\":{\"ANTHROPIC_BASE_URL\":\"https://api.anthropic.com\"}}'",
    )

    assert _claude_extra_args() == [
        "--settings",
        '{"env":{"ANTHROPIC_BASE_URL":"https://api.anthropic.com"}}',
    ]


def test_claude_generate_passes_extra_args(monkeypatch):
    monkeypatch.setenv("LOPE_CLAUDE_ARGS", "--model sonnet")
    proc = MagicMock(returncode=0, stdout="done", stderr="")
    validator = ClaudeCodeValidator(binary="claude")
    with patch.object(validator, "available", return_value=True):
        with patch("lope.validators._run_with_group_kill", return_value=(proc, 0.1)) as run:
            assert validator.generate("implement", timeout=60) == "done"

    assert run.call_args.args[0] == ["claude", "--print", "--model", "sonnet"]


def test_implementation_context_sets_per_call_nested_lope_guard():
    context = InvocationContext(
        budget=RunBudget(mode="implement", run_timeout=30),
        mode="implement",
        metadata={"implementation": True},
    )

    proc, _elapsed = _run_with_group_kill(
        [sys.executable, "-m", "lope.cli", "--help"],
        input_text=None,
        timeout=12,
        cwd=os.getcwd(),
        context=context,
    )

    assert proc.returncode == 2
    assert "nested Lope orchestration is disabled" in proc.stderr
    assert "LOPE_IMPLEMENTATION_DEPTH" not in os.environ


def test_implementation_guard_is_per_call_not_process_global(monkeypatch):
    context = MagicMock(metadata={"implementation": True})
    proc = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("lope.validators.run_subprocess_group", return_value=proc) as run:
        _run_with_group_kill(["writer"], "prompt", 60, ".", context=context)

    assert run.call_args.kwargs.get("env") is None
    assert "LOPE_IMPLEMENTATION_DEPTH" not in os.environ


def test_claude_nonzero_exit_preserves_stdout_error():
    proc = MagicMock(
        returncode=1,
        stdout="API Error: 400 Tool reference 'WaitForMcpServers' not found in available tools",
        stderr="",
    )
    validator = ClaudeCodeValidator(binary="claude")
    with patch.object(validator, "available", return_value=True):
        with patch("lope.validators._run_with_group_kill", return_value=(proc, 0.1)):
            with pytest.raises(ProviderInfrastructureError, match="WaitForMcpServers"):
                validator.generate("implement", timeout=60)


def test_timeout_preserves_bounded_partial_output():
    error = subprocess.TimeoutExpired(
        ["codex", "exec"],
        60,
        output="partial implementation summary",
        stderr="sandbox denied nested process",
    )
    with patch("lope.validators.run_subprocess_group", side_effect=error):
        with pytest.raises(ProviderInfrastructureError) as raised:
            _run_with_group_kill(["codex", "exec"], "prompt", 60, ".")

    message = str(raised.value)
    assert "process group killed" in message
    assert "stdout: partial implementation summary" in message
    assert "stderr: sandbox denied nested process" in message


def test_diagnostic_tail_decodes_bytes_and_is_bounded():
    value = b"prefix-" + (b"x" * 600) + b"-exact-error"
    result = _bounded_stream(value, limit=40)

    assert result.startswith("...[truncated]...")
    assert result.endswith("-exact-error")
    assert len(result) == len("...[truncated]...") + 40


def test_infrastructure_classification_uses_complete_stream():
    stdout = ("context\n" * 1000) + (
        "API Error: 400 Tool reference 'WaitForMcpServers' not found in available tools"
    )

    assert _is_infrastructure_exit("claude", stdout, "") is True


@pytest.mark.parametrize(
    ("provider", "stdout", "stderr"),
    [
        ("claude", "API Error: 429 rate limit", ""),
        ("claude", "API Error: 503 unavailable", ""),
        (
            "claude",
            "API Error: 400 Tool reference 'WaitForMcpServers' not found in available tools",
            "",
        ),
        (
            "codex",
            "",
            "Error: failed to initialize in-process app-server client: denied",
        ),
        ("codex", "", "Error: connection reset by peer"),
        ("codex", "", "Error: service unavailable upstream"),
    ],
)
def test_every_supported_infrastructure_signature(provider, stdout, stderr):
    assert _is_infrastructure_exit(provider, stdout, stderr) is True


@pytest.mark.parametrize(
    ("provider", "stdout", "stderr"),
    [
        ("unknown", "API Error: 503 unavailable", "Error: service unavailable"),
        ("codex", "", "Tests mention Error: connection reset by peer"),
        ("codex", "", "prefix Error: service unavailable"),
        (
            "codex",
            "",
            "prefix Error: failed to initialize in-process app-server client: denied",
        ),
        ("codex", "Error: service unavailable", ""),
        ("claude", "prefix API Error: 503 unavailable", ""),
    ],
)
def test_infrastructure_signatures_are_provider_specific_and_anchored(
    provider, stdout, stderr
):
    assert _is_infrastructure_exit(provider, stdout, stderr) is False


def test_codex_nonzero_exit_preserves_both_streams():
    proc = MagicMock(
        returncode=1,
        stdout="partial work",
        stderr="Error: failed to initialize in-process app-server client: Operation not permitted",
    )
    validator = CodexValidator(binary="codex")
    with patch.object(validator, "available", return_value=True):
        with patch("lope.validators._run_with_group_kill", return_value=(proc, 0.1)):
            with pytest.raises(ProviderInfrastructureError) as raised:
                validator.generate("implement", timeout=60)

    assert "stdout: partial work" in str(raised.value)
    assert "stderr: Error: failed to initialize in-process app-server client" in str(raised.value)


def test_codex_semantic_nonzero_exit_is_not_infrastructure():
    proc = MagicMock(returncode=1, stdout="tests failed", stderr="assertion mismatch")
    validator = CodexValidator(binary="codex")
    with patch.object(validator, "available", return_value=True):
        with patch("lope.validators._run_with_group_kill", return_value=(proc, 0.1)):
            with pytest.raises(RuntimeError) as raised:
                validator.generate("implement", timeout=60)

    assert not isinstance(raised.value, ProviderInfrastructureError)
    assert "tests failed" in str(raised.value)


@pytest.mark.parametrize(
    "stdout",
    [
        "API Error: 401 Unauthorized",
        "Tests failed: expected the text API Error: 400 Tool reference 'x' not found in available tools",
    ],
)
def test_claude_auth_and_infrastructure_looking_semantic_exits_are_not_typed(stdout):
    proc = MagicMock(returncode=1, stdout=stdout, stderr="")
    validator = ClaudeCodeValidator(binary="claude")
    with patch.object(validator, "available", return_value=True):
        with patch("lope.validators._run_with_group_kill", return_value=(proc, 0.1)):
            with pytest.raises(RuntimeError) as raised:
                validator.generate("implement", timeout=60)

    assert not isinstance(raised.value, ProviderInfrastructureError)
