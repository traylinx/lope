from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from lope.executor import PhaseExecutor
from lope.implementation_runner import (
    invoke_writer_failover,
    ordered_writers,
    workspace_snapshot,
)
from lope.provider_errors import ProviderInfrastructureError
from lope.runtime import BudgetExhausted, InvocationContext, RunBudget


class _Writer:
    def __init__(self, name, available=True, safe=True):
        self.name = name
        self._available = available
        self.supports_safe_implementation_failover = safe

    def available(self):
        return self._available


class _Pool:
    def __init__(self, validators, primary):
        self._validators = validators
        self._primary = primary

    def validators(self):
        return list(self._validators)

    def primary_validator(self):
        return self._primary


def test_phase_forecast_reserves_every_writer_candidate_and_validator():
    codex = _Writer("codex")
    claude = _Writer("claude")
    pool = _Pool([codex, claude], primary=codex)
    pool._implementation_candidate_count = 3
    pool._invocation_context = InvocationContext(
        budget=RunBudget(mode="implement", run_timeout=1000, max_external_calls=8),
        mode="implement",
    )
    executor = PhaseExecutor(
        validator_pool=pool,
        implementation_fn=lambda **kwargs: None,
        timeout_seconds=120,
    )

    assert executor._can_fund_attempt(SimpleNamespace(index=1), 1) is True
    event = pool._invocation_context.budget.snapshot()["events"][-1]
    assert event["kind"] == "phase_forecast"
    assert event["calls"] == 7
    assert event["wall_seconds"] == 240


def test_ordered_writers_keeps_primary_first_and_deduplicates():
    codex = _Writer("codex")
    claude = _Writer("claude")
    pool = _Pool([claude, codex], primary=codex)

    result = ordered_writers(pool, ["codex", "claude", "missing", "claude"])

    assert [writer.name for writer in result] == ["codex", "claude"]


def test_ordered_writers_rejects_untyped_failover_provider():
    codex = _Writer("codex")
    opencode = _Writer("opencode", safe=False)
    pool = _Pool([codex, opencode], primary=codex)

    with pytest.raises(ValueError, match="unsupported: opencode"):
        ordered_writers(pool, ["codex", "opencode"])


def test_writer_failover_is_sequential_and_preserves_failure_context(monkeypatch):
    codex = _Writer("codex")
    claude = _Writer("claude")
    poison = _Writer("poison")
    calls = []

    def fake_invoke(writer, prompt, timeout, **kwargs):
        calls.append((writer.name, prompt, timeout, kwargs))
        if writer.name == "codex":
            raise ProviderInfrastructureError("provider timeout")
        if writer.name == "poison":
            raise AssertionError("writer invoked after success")
        return "implemented"

    monkeypatch.setattr("lope.invocation.invoke_generate", fake_invoke)
    monkeypatch.setattr("lope.implementation_runner.workspace_snapshot", lambda: b"clean")
    result = invoke_writer_failover(
        [codex, claude, poison],
        "base prompt",
        120,
        phase_index=1,
        context=SimpleNamespace(),
        print_fn=lambda *args, **kwargs: None,
    )

    assert result.ok is True
    assert result.summary == "implemented"
    assert [call[0] for call in calls] == ["codex", "claude"]
    assert "codex: ProviderInfrastructureError: provider timeout" in calls[1][1]
    assert "Do not invoke Lope" in calls[1][1]
    assert calls[1][3]["metadata"]["writer_attempt"] == 2
    assert calls[1][3]["max_retries"] == 0


def test_writer_failover_reports_all_failures(monkeypatch):
    unavailable = _Writer("codex", available=False)
    claude = _Writer("claude")

    def fail(*args, **kwargs):
        raise ProviderInfrastructureError("tool reference missing")

    monkeypatch.setattr("lope.invocation.invoke_generate", fail)
    monkeypatch.setattr("lope.implementation_runner.workspace_snapshot", lambda: b"clean")
    result = invoke_writer_failover(
        [unavailable, claude],
        "base prompt",
        120,
        phase_index=1,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result.ok is False
    assert "codex: unavailable" in result.error
    assert "claude: ProviderInfrastructureError: tool reference missing" in result.error


def test_writer_failover_stops_on_untyped_error(monkeypatch):
    codex = _Writer("codex")
    claude = _Writer("claude")
    calls = []

    def fail(writer, *args, **kwargs):
        calls.append(writer.name)
        raise ValueError("implementation contract failed")

    monkeypatch.setattr("lope.invocation.invoke_generate", fail)
    monkeypatch.setattr("lope.implementation_runner.workspace_snapshot", lambda: b"clean")
    result = invoke_writer_failover(
        [codex, claude],
        "base prompt",
        120,
        phase_index=1,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result.ok is False
    assert calls == ["codex"]
    assert "ValueError" in result.error


def test_writer_failover_stops_on_budget_exhaustion(monkeypatch):
    codex = _Writer("codex")
    claude = _Writer("claude")
    calls = []

    def fail(writer, *args, **kwargs):
        calls.append(writer.name)
        raise BudgetExhausted("run_budget_exhausted", "no review reserve")

    monkeypatch.setattr("lope.invocation.invoke_generate", fail)
    monkeypatch.setattr("lope.implementation_runner.workspace_snapshot", lambda: b"clean")
    result = invoke_writer_failover(
        [codex, claude],
        "base prompt",
        120,
        phase_index=1,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result.ok is False
    assert calls == ["codex"]
    assert "BudgetExhausted" in result.error


def test_writer_failover_blocks_when_failed_writer_changes_workspace(monkeypatch):
    codex = _Writer("codex")
    claude = _Writer("claude")
    snapshots = iter([b"before", b"after"])
    calls = []

    def fail(writer, *args, **kwargs):
        calls.append(writer.name)
        raise ProviderInfrastructureError("timeout")

    monkeypatch.setattr("lope.invocation.invoke_generate", fail)
    monkeypatch.setattr("lope.implementation_runner.workspace_snapshot", lambda: next(snapshots))
    result = invoke_writer_failover(
        [codex, claude],
        "base prompt",
        120,
        phase_index=1,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result.ok is False
    assert calls == ["codex"]
    assert "failed writer changed workspace" in result.error


def test_workspace_snapshot_detects_change_inside_already_dirty_file(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("committed\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    tracked.write_text("dirty-one\n")
    first = workspace_snapshot(str(tmp_path))
    tracked.write_text("dirty-two\n")
    second = workspace_snapshot(str(tmp_path))

    assert first is not None
    assert second is not None
    assert first != second


def test_workspace_snapshot_detects_staged_untracked_and_head_changes(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("committed\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    commit = [
        "git",
        "-C",
        str(tmp_path),
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
    ]
    subprocess.run([*commit, "initial"], check=True)
    clean = workspace_snapshot(str(tmp_path))

    tracked.write_text("staged\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    staged = workspace_snapshot(str(tmp_path))
    untracked = tmp_path / "new.txt"
    untracked.write_text("one\n")
    untracked_one = workspace_snapshot(str(tmp_path))
    untracked.write_text("two\n")
    untracked_two = workspace_snapshot(str(tmp_path))
    untracked.unlink()
    before_commit = workspace_snapshot(str(tmp_path))
    subprocess.run([*commit, "second"], check=True)
    after_commit = workspace_snapshot(str(tmp_path))

    assert clean != staged
    assert staged != untracked_one
    assert untracked_one != untracked_two
    assert before_commit != after_commit


def test_workspace_snapshot_detects_ignored_file_change(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".env\n")
    (tmp_path / "tracked.txt").write_text("committed\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".gitignore", "tracked.txt"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    ignored = tmp_path / ".env"
    ignored.write_text("TOKEN=one\n")
    first = workspace_snapshot(str(tmp_path))
    ignored.write_text("TOKEN=two\n")
    second = workspace_snapshot(str(tmp_path))

    assert first is not None
    assert second is not None
    assert first != second


def test_writer_failover_blocks_when_snapshot_unavailable(monkeypatch):
    codex = _Writer("codex")
    claude = _Writer("claude")
    calls = []

    def fail(writer, *args, **kwargs):
        calls.append(writer.name)
        raise ProviderInfrastructureError("timeout")

    monkeypatch.setattr("lope.invocation.invoke_generate", fail)
    monkeypatch.setattr("lope.implementation_runner.workspace_snapshot", lambda: None)
    result = invoke_writer_failover(
        [codex, claude],
        "base prompt",
        120,
        phase_index=1,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result.ok is False
    assert calls == ["codex"]
    assert "workspace state unavailable" in result.error


def test_writer_failover_uses_one_shared_stage_deadline(monkeypatch):
    codex = _Writer("codex")
    claude = _Writer("claude")
    calls = []
    clock = iter([0.0, 10.0, 130.0])

    def fail(writer, *args, **kwargs):
        calls.append(writer.name)
        raise ProviderInfrastructureError("timeout")

    monkeypatch.setattr("lope.invocation.invoke_generate", fail)
    monkeypatch.setattr("lope.implementation_runner.workspace_snapshot", lambda: b"same")
    monkeypatch.setattr("lope.implementation_runner.time.monotonic", lambda: next(clock))
    result = invoke_writer_failover(
        [codex, claude],
        "base prompt",
        120,
        phase_index=1,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result.ok is False
    assert calls == ["codex"]
    assert "implementation stage deadline exhausted" in result.error


def test_writer_failover_stops_on_not_implemented(monkeypatch):
    codex = _Writer("codex")
    claude = _Writer("claude")
    calls = []

    def fail(writer, *args, **kwargs):
        calls.append(writer.name)
        raise NotImplementedError("generate unavailable")

    monkeypatch.setattr("lope.invocation.invoke_generate", fail)
    monkeypatch.setattr("lope.implementation_runner.workspace_snapshot", lambda: b"same")
    result = invoke_writer_failover(
        [codex, claude],
        "base prompt",
        120,
        phase_index=1,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result.ok is False
    assert calls == ["codex"]
    assert "generate not supported" in result.error
