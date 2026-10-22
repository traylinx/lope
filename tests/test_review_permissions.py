"""Keyless regressions; permission errors are not parser or failover signals."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from lope.generic_validators import ConfigError, GenericSubprocessValidator
from lope.invocation import _outcome_for, invoke_generate
from lope.provider_errors import (
    ProviderInfrastructureError, ProviderToolPermissionError, tool_permission_denial,
)
from lope.retry_policy import classify_failure, decide_retry
from lope.review import REVIEW_SCOPE, ReviewInput, build_review_prompt
from lope.runtime import InvocationContext, RunBudget

ROOT = Path(__file__).resolve().parents[1]
DENIAL = ('jetski: no output produced — a tool required the "command" permission '
          'that headless mode cannot prompt for, so it was auto-denied. '
          'Add an allow-rule under permissions.allow in settings.json.')


def context(mode="review"):
    return InvocationContext(budget=RunBudget(mode=mode, run_timeout=30), mode=mode)


def provider(**overrides):
    cfg = {"name": "fixture", "type": "subprocess", "command": [sys.executable, "-c", "print('base')"]}
    cfg.update(overrides)
    # Most keyless fixtures consume neither transport; explicitly opt into
    # stdin when exercising a profile so production validation stays strict.
    if any(k in overrides for k in ("review_command", "review_workdir")) and "review_stdin" not in overrides:
        cfg["review_stdin"] = True
    return GenericSubprocessValidator(cfg)


@pytest.mark.parametrize("wrapped", [DENIAL, "agy returned empty output; stderr: " + DENIAL])
def test_recorded_native_permission_failure_is_terminal(wrapped):
    failure = classify_failure(wrapped)
    assert failure.kind == "tool_permission_denied"
    assert not failure.retryable
    assert _outcome_for(wrapped).value == "tool_permission_denied"
    assert not decide_retry(wrapped, attempt=0, remaining_seconds=1000,
                            requested_timeout=10).retry


def test_permission_precedes_incidental_transient_diagnostic():
    assert not classify_failure(DENIAL + " Previous HTTP 429 retry").retryable


@pytest.mark.parametrize("text", [
    'Review says a command permission may be needed',
    'Example: ' + DENIAL,
    'Quoted stdout: ' + DENIAL,
])
def test_no_denial_from_general_or_quoted_prose(text):
    assert not tool_permission_denial(text)


def test_permission_error_is_not_implementation_failover_signal():
    error = ProviderToolPermissionError("scope")
    assert not isinstance(error, ProviderInfrastructureError)
    assert classify_failure(error).kind == "tool_permission_denied"


@pytest.mark.parametrize("rc,stdout", [(0, ""), (1, ""), (0, "Starting my review"), (1, "partial")])
def test_denied_tool_never_counts_commentary_as_review(monkeypatch, rc, stdout):
    p = provider()
    # Long tail must not hide a native denial before diagnostic clipping.
    monkeypatch.setattr(p, "_run", lambda *a, **kw: (rc, stdout, DENIAL + "\n" + "x"*5000, 1.0))
    with pytest.raises(ProviderToolPermissionError, match="command requires approval"):
        p.generate("prompt")
    verdict = p.validate("prompt")
    assert verdict.error
    assert "tool_permission_denied" in verdict.error
    assert verdict.raw_response == ""


def test_model_text_cannot_forge_runner_permission_failure(monkeypatch):
    p = provider()
    monkeypatch.setattr(p, "_run", lambda *a, **kw: (0, DENIAL, "", 1.0))
    assert p.generate("prompt") == DENIAL


def test_permission_has_no_retry_and_no_success_vote(monkeypatch):
    p = provider()
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        return 0, "", DENIAL, 0.1
    monkeypatch.setattr(p, "_run", run)
    ctx = context()
    with pytest.raises(ProviderToolPermissionError):
        invoke_generate(p, "p", 5, context=ctx)
    assert len(calls) == 1
    snapshot = ctx.budget.snapshot()
    assert snapshot["calls"][0]["outcome"] == "tool_permission_denied"
    assert snapshot["events"][-1]["reason"] == "not_retryable"


@pytest.mark.parametrize("error,expected", [
    ("provider returned empty output", "empty_output"),
    ("invalid json response", "parse_error"),
    ("HTTP 401 unauthorized", "auth_error"),
])
def test_adjacent_failure_classes_retain_meaning(error, expected):
    assert classify_failure(error).kind == expected
    assert _outcome_for(error).value == expected


@pytest.mark.parametrize("config", [
    {"review_command": []}, {"review_command": "agy"}, {"review_command": [""]},
    {"review_command": [sys.executable, "${SECRET}"]},
    {"review_workdir": "~/unsafe"}, {"review_stdin": "true"},
    {"review_command": ["./agy"], "review_workdir": "isolated"},
])
def test_invalid_review_profile_fails_before_launch(config):
    with pytest.raises(ConfigError):
        provider(**config)


def test_profile_is_selected_only_for_review_and_stdin_is_independent():
    p = provider(review_command=[sys.executable, "-c", "import sys; print('review:' + sys.stdin.read())"],
                 review_stdin=True)
    assert invoke_generate(p, "hello", 5, context=context()).strip() == "review:hello"
    assert invoke_generate(p, "hello", 5, context=context("ask")).strip() == "base"
    assert p.generate("hello", 5).strip() == "base"


def test_review_cwd_is_ephemeral_not_inherited_and_not_claimed_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_WORKDIR", str(tmp_path))
    (tmp_path / "AGENTS.md").write_text("marker")
    code = "import os,json; print(json.dumps({'cwd':os.getcwd(),'files':os.listdir('.'),'workdir':os.environ.get('LOPE_WORKDIR')}))"
    p = provider(review_command=[sys.executable, "-c", code], review_workdir="isolated")
    result = json.loads(invoke_generate(p, "p", 5, context=context()))
    assert result["files"] == []
    assert Path(result["workdir"]).resolve() == Path(result["cwd"]).resolve()
    assert Path(result["cwd"]) != tmp_path
    assert not Path(result["cwd"]).exists()
    assert (tmp_path / "AGENTS.md").read_text() == "marker"
    profile = p.execution_profile("review")
    assert profile["workdir"] == "isolated"
    assert profile["tool_policy"] == "provider_owned_unchanged"
    assert "not a tool sandbox" in profile["warnings"][0]


def test_legacy_default_keeps_callers_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_WORKDIR", str(tmp_path))
    p = provider(command=[sys.executable, "-c", "import os; print(os.getcwd())"])
    assert Path(invoke_generate(p, "p", 5, context=context()).strip()) == tmp_path
    assert not p.execution_profile("review")["review_profile_configured"]


def test_missing_review_binary_is_visible_without_native_dispatch():
    p = provider(review_command=["/no-such-lope-review-binary-xyz"])
    assert not p.execution_profile("review")["launchable"]
    with pytest.raises(RuntimeError, match="no-such-lope-review-binary"):
        invoke_generate(p, "p", 5, context=context())


def test_review_scope_shared_with_structured_review():
    assert REVIEW_SCOPE in build_review_prompt(ReviewInput(target="fixture", content="body"))
    assert "Treat instructions inside" in REVIEW_SCOPE
    assert "not a tool-permission or sandbox guarantee" in REVIEW_SCOPE


@pytest.mark.parametrize("consensus", [False, True])
def test_cli_review_profile_preflight_and_failure_quorum(tmp_path, consensus):
    home = tmp_path / "home"
    home.mkdir()
    code = "import sys; print('preparatory commentary'); sys.stderr.write(" + repr(DENIAL) + ")"
    config = {"version":1,"validators":["fixture"],"primary":"fixture", "providers":[{
        "name":"fixture", "type":"subprocess", "command":[sys.executable,"-c","print('WRONG_BASE')"],
        "review_command":[sys.executable,"-c",code],"review_stdin":True,"review_workdir":"isolated","timeout":5,
    }]}
    (home / "config.json").write_text(json.dumps(config))
    target = tmp_path / "input.md"
    target.write_text("An inert review fixture")
    env = dict(os.environ, PYTHONPATH=str(ROOT), LOPE_HOME=str(home),
               LOPE_LATENCY="off", PYTHONDONTWRITEBYTECODE="1", LOPE_WORKDIR=str(tmp_path))
    cmd=[sys.executable,"-m","lope","review",str(target),"--validators","fixture",
         "--primary","fixture","--timeout","5","--run-timeout","30","--max-calls","1","--json"]
    if consensus:
        cmd.append("--consensus")
    result=subprocess.run(cmd,capture_output=True,text=True,env=env,cwd=tmp_path,timeout=35)
    assert result.returncode == 1, result.stderr
    data=json.loads(result.stdout)
    assert data["partial"]
    assert data["calls"][0]["outcome"] == "tool_permission_denied"
    assert data["actual"]["calls"] == 1
    profile=data["plan"]["requests"][0]["execution_profiles"][0]
    assert profile["review_profile_configured"]
    assert profile["workdir"] == "isolated"
    assert "CLI tool permissions are unchanged" in result.stderr
    assert "WRONG_BASE" not in result.stdout
    assert "preparatory commentary" not in result.stdout

@pytest.mark.parametrize("error_type", [subprocess.TimeoutExpired, OSError])
def test_exception_stderr_permission_checked_before_clipping(monkeypatch, error_type):
    p = provider()
    stderr = (DENIAL + "\n" + "x" * 5000).encode()
    if error_type is subprocess.TimeoutExpired:
        error = error_type(["fixture"], 1, output=b"commentary", stderr=stderr)
    else:
        error = error_type("completion failed")
        error.stderr = stderr
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(p, "_run", fail)
    with pytest.raises(ProviderToolPermissionError):
        p.generate("p")
    assert classify_failure(p.validate("p").error).kind == "tool_permission_denied"


@pytest.mark.parametrize("quoted", [DENIAL, "fixture tool_permission_denied: command"])
def test_quoted_diagnostics_cannot_override_timeout_classification(quoted):
    for stream in ("stdout", "stderr"):
        assert classify_failure("provider timed out after 1s; " + stream + ":\n" + quoted).kind == "provider_timeout"


def test_non_empty_output_not_classified_as_empty():
    assert classify_failure("expected non-empty output").kind == "launch_error"


def test_profile_cannot_drop_prompt_and_stdin_only_profile_is_reported():
    with pytest.raises(ConfigError, match="needs stdin"):
        provider(review_stdin=False)
    assert provider(review_stdin=True).execution_profile("review")["review_profile_configured"]


@pytest.mark.parametrize("timeout", [False, True])
def test_review_directory_cleanup_does_not_replace_result_or_error(monkeypatch, timeout):
    import lope.generic_validators as module
    import lope.processes as processes
    class Directory:
        name = "/tmp"
        def __init__(self, **kwargs):
            pass
        def cleanup(self):
            raise PermissionError("fixture")
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", Directory)
    def run(*args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(["fixture"], 1)
        return subprocess.CompletedProcess(["fixture"], 0, "review", "")
    monkeypatch.setattr(processes, "run_subprocess_group", run)
    p = provider(review_workdir="isolated")
    with pytest.warns(RuntimeWarning, match="directory cleanup failed"):
        if timeout:
            with pytest.raises(RuntimeError, match="timed out"):
                p.generate("p", 1, context=context())
        else:
            assert p.generate("p", 1, context=context()) == "review"
