"""Tests for the gate trust boundary (lope.trust).

Project-defined gate commands (.lope/rules.json) execute via shell, so lope
must not run them from an untrusted repo without consent. These tests pin the
fail-closed behavior and the per-(repo, command-set) trust memory.
"""

import io

from lope.gates import GateSpec
from lope.trust import (
    ensure_gates_trusted,
    gate_command_digest,
    is_trusted,
    record_trust,
)


def _specs(*cmds):
    return [GateSpec(name=f"g{i}", cmd=c) for i, c in enumerate(cmds)]


def test_no_specs_is_trivially_allowed():
    assert ensure_gates_trusted([], stream=io.StringIO()) is True


def test_non_interactive_untrusted_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    out = io.StringIO()
    ok = ensure_gates_trusted(_specs("echo hi"), cwd=tmp_path, stream=out)
    assert ok is False
    assert "Refusing to run gate commands" in out.getvalue()
    assert not (tmp_path / "trusted_gates.json").exists()


def test_assume_yes_allows_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    specs = _specs("echo hi", "pytest -q")
    ok = ensure_gates_trusted(specs, cwd=tmp_path, assume_yes=True, stream=io.StringIO())
    assert ok is True
    assert is_trusted(tmp_path, gate_command_digest(specs))


def test_env_bypass_allows_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    monkeypatch.setenv("LOPE_TRUST_GATES", "1")
    specs = _specs("echo hi")
    assert ensure_gates_trusted(specs, cwd=tmp_path, stream=io.StringIO()) is True
    assert is_trusted(tmp_path, gate_command_digest(specs))


def test_recorded_trust_persists_non_interactively(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    specs = _specs("echo hi")
    record_trust(tmp_path, gate_command_digest(specs))
    assert ensure_gates_trusted(specs, cwd=tmp_path, stream=io.StringIO()) is True


def test_changing_commands_revokes_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    ensure_gates_trusted(_specs("echo hi"), cwd=tmp_path, assume_yes=True, stream=io.StringIO())
    # A different command set hashes differently -> not trusted -> fail closed.
    assert ensure_gates_trusted(_specs("rm -rf /"), cwd=tmp_path, stream=io.StringIO()) is False


def test_interactive_always_records(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    specs = _specs("echo hi")
    ok = ensure_gates_trusted(specs, cwd=tmp_path, stream=_Tty(), input_fn=lambda _p: "always")
    assert ok is True
    assert is_trusted(tmp_path, gate_command_digest(specs))


def test_interactive_no_does_not_record(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    specs = _specs("echo hi")
    ok = ensure_gates_trusted(specs, cwd=tmp_path, stream=_Tty(), input_fn=lambda _p: "n")
    assert ok is False
    assert not is_trusted(tmp_path, gate_command_digest(specs))
