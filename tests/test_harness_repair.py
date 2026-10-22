"""Keyless regressions for the review harness; no real model/CLI calls."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lope.diagnostics import subprocess_diagnostics
from lope.generic_validators import GenericSubprocessValidator
from lope.jobs import process_state
from lope.processes import OutputLimitExceeded, ProcessCompletionError, run_subprocess_group
from lope.validators import _run_with_group_kill
from lope.provider_errors import ProviderInfrastructureError


ROOT = Path(__file__).resolve().parent.parent


def _provider(code="", timeout=5):
    return GenericSubprocessValidator({
        "name": "fixture", "type": "subprocess", "stdin": True,
        "command": [sys.executable, "-u", "-c", code], "timeout": timeout,
    })


@pytest.mark.parametrize("mode", ["plain", "json"])
def test_budget_advice_is_visible_in_both_output_modes(tmp_path, mode):
    home = tmp_path / mode
    home.mkdir()
    (home / "config.json").write_text(json.dumps({
        "version": 1, "validators": ["fixture"], "primary": "fixture",
        "providers": [{"name": "fixture", "type": "subprocess", "stdin": True,
                       "command": [sys.executable, "-c", "print('KEYLESS_OK')"],
                       "timeout": 1800}],
    }))
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(ROOT), "LOPE_HOME": str(home),
                "LOPE_WORKDIR": str(tmp_path), "LOPE_LATENCY": "on",
                "LOPE_RESPECT_PROVIDER_TIMEOUT": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    cmd = [sys.executable, "-m", "lope", "pipe", "--validators", "fixture",
           "--primary", "fixture", "--timeout", "8", "--run-timeout", "20",
           "--max-calls", "1", "--require-all"]
    if mode == "json":
        cmd.append("--json")
    run = subprocess.run(cmd, input="probe", capture_output=True, text=True,
                         cwd=tmp_path, env=env, timeout=25)
    assert run.returncode == 0, run.stderr
    assert "clamps its configured 1800s" in run.stderr
    if mode == "json":
        result = json.loads(run.stdout)
        assert "clamps its configured 1800s" in result["plan"]["requests"][0]["budget_advice"][0]


def test_generic_provider_retains_callers_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOPE_WORKDIR", raising=False)
    assert _provider("import os; print(os.getcwd())").generate("probe", timeout=8).strip() == str(tmp_path)


def test_generic_provider_honours_explicit_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_WORKDIR", str(tmp_path))
    assert _provider("import os; print(os.getcwd())").generate("probe", timeout=8).strip() == str(tmp_path)


@pytest.mark.parametrize("method", ["generate", "validate"])
def test_generic_timeout_keeps_bounded_redacted_diagnostics(monkeypatch, method):
    validator = _provider()
    error = subprocess.TimeoutExpired(
        ["fixture"], 5, output=b"x" * 5000 + b" PARTIAL_MARKER",
        stderr=b"Permission denied; api_key=sk-testsecret123456789",
    )
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(validator, "_run", fail)
    if method == "generate":
        with pytest.raises(RuntimeError) as raised:
            validator.generate("probe", timeout=5)
        message = str(raised.value)
    else:
        message = validator.validate("probe", timeout=5).error
    assert "PARTIAL_MARKER" in message
    assert "Permission denied" in message
    assert "[REDACTED]" in message
    assert "testsecret" not in message
    assert len(message) < 1200


@pytest.mark.parametrize("method", ["generate", "validate"])
def test_empty_output_retains_stderr_without_secrets(monkeypatch, method):
    validator = _provider()
    monkeypatch.setattr(validator, "_run", lambda *a, **k: (
        0, "", "Native print deadline exceeded; Authorization: Bearer secret-value", 0.1,
    ))
    if method == "generate":
        with pytest.raises(RuntimeError) as raised:
            validator.generate("probe")
        message = str(raised.value)
    else:
        result = validator.validate("probe")
        message = result.error
        assert result.raw_response == ""
    assert "empty output" in message
    assert "Native print deadline exceeded" in message
    assert "secret-value" not in message


def test_nonzero_exit_keeps_both_diagnostic_streams(monkeypatch):
    validator = _provider()
    monkeypatch.setattr(validator, "_run", lambda *a, **k: (1, "OUT_MARKER", "ERR_MARKER", 0.1))
    with pytest.raises(RuntimeError) as raised:
        validator.generate("probe")
    assert "OUT_MARKER" in str(raised.value) and "ERR_MARKER" in str(raised.value)


@pytest.mark.parametrize("secret", [
    'password="value with spaces"', '"api_key":"value-secret"',
    "postgresql://user:password@host/db", "ghp_abcdefgh123456789",
    "-----BEGIN RSA PRIVATE KEY-----\nprivate material\n-----END RSA PRIVATE KEY-----",
    '<think>private reasoning</think>', '{"type":"thinking_delta","thinking":"private"}',
])
def test_diagnostics_redact_before_clipping(secret):
    text = subprocess_diagnostics("", secret)
    assert "REDACTED" in text
    assert "value-secret" not in text and "value with spaces" not in text
    assert "private material" not in text and "private reasoning" not in text


def _alive(pid):
    state = process_state(pid)
    return bool(state) and not state.startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="POSIX group ownership")
@pytest.mark.parametrize("inherit_pipes", [True, False])
def test_exited_parent_reaps_owned_children_without_false_timeout(inherit_pipes):
    redirection = "" if inherit_pipes else ", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL"
    program = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']"
        + redirection + "); print(p.pid,flush=True)"
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        started = time.monotonic()
        result = run_subprocess_group([sys.executable, "-u", "-c", program], timeout=8)
        assert result.returncode == 0
        pid = int(result.stdout.strip())
        assert not _alive(pid), "owned child survived successful completion"
        assert _alive(unrelated.pid), "unrelated group was signalled"
        assert time.monotonic() - started < 5
    finally:
        os.killpg(unrelated.pid, signal.SIGKILL)
        unrelated.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX group ownership")
def test_escaped_pipe_fails_closed_with_completion_reason():
    program = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
        "print(p.pid,flush=True)"
    )
    child = None
    try:
        with pytest.raises(ProcessCompletionError, match="pipes did not close") as raised:
            run_subprocess_group([sys.executable, "-u", "-c", program], timeout=8)
        child = int(raised.value.stdout.strip())
        assert _alive(child), "an escaped group must not be blindly signalled"
    finally:
        if child is not None:
            os.killpg(child, signal.SIGKILL)


def test_post_exit_drain_keeps_output_limits():
    with pytest.raises(OutputLimitExceeded):
        run_subprocess_group([sys.executable, "-c", "print('x'*8192)"], timeout=8, stdout_limit=128)


def test_completion_failure_is_not_success_in_builtin_adapter(monkeypatch):
    def fail(*a, **k):
        raise ProcessCompletionError("owned cleanup failed", stdout="PARTIAL", stderr="api_key=secret-value")
    monkeypatch.setattr("lope.validators.run_subprocess_group", fail)
    with pytest.raises(ProviderInfrastructureError) as raised:
        _run_with_group_kill(["fixture"], None, 8, ".")
    assert "cleanup failed" in str(raised.value) and "PARTIAL" in str(raised.value)
    assert "secret-value" not in str(raised.value)
