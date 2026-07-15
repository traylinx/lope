import subprocess
import sys

from lope.gates import (
    GateResult, GateSpec, compare_results, load_baseline,
    load_gate_specs, run_gate, run_gates, save_baseline,
)
from lope.runtime import InvocationContext, RunBudget


def test_missing_config_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    specs, path = load_gate_specs()
    assert specs == []
    assert path == tmp_path / '.lope' / 'rules.json'


def test_exit_gate_passes_and_fails(tmp_path):
    ok = run_gate(GateSpec(name='ok', cmd=sys.executable + ' -c "print(1)"'), tmp_path)
    bad = run_gate(GateSpec(name='bad', cmd=sys.executable + ' -c "raise SystemExit(3)"'), tmp_path)
    assert ok.ok is True
    assert bad.ok is False
    assert bad.exit_code == 3


def test_json_number_gate_extracts_path(tmp_path):
    spec = GateSpec(name='cov', cmd=sys.executable + ' -c "import json; print(json.dumps({\'totals\':{\'pct\':84.2}}))"', type='json_number', path='totals.pct')
    result = run_gate(spec, tmp_path)
    assert result.ok is True
    assert result.value == 84.2


def test_regex_number_gate_extracts_capture(tmp_path):
    spec = GateSpec(name='score', cmd=sys.executable + ' -c "print(\'score=91.5\')"', type='regex_number', regex=r'score=(\d+\.\d+)')
    result = run_gate(spec, tmp_path)
    assert result.ok is True
    assert result.value == 91.5


def test_baseline_compare_min_delta(tmp_path):
    before = [GateSpec(name='score', cmd=sys.executable + ' -c "print(100)"', type='regex_number', regex=r'(\d+)')]
    before_results = run_gates(before, tmp_path)
    save_baseline(before_results, cwd=tmp_path)
    loaded = load_baseline(cwd=tmp_path)
    after_spec = GateSpec(name='score', cmd=sys.executable + ' -c "print(90)"', type='regex_number', regex=r'(\d+)', min_delta=0)
    after_results = run_gates([after_spec], tmp_path)
    comps = compare_results([after_spec], loaded, after_results)
    assert comps[0].passed is False
    assert comps[0].delta == -10
    assert 'min_delta' in comps[0].reason


def test_gate_timeout_clamps_to_remaining_command_budget(tmp_path, monkeypatch):
    class Clock:
        now = 100.0

        def __call__(self):
            return self.now

    clock = Clock()
    context = InvocationContext(
        budget=RunBudget(mode="gate", run_timeout=10, clock=clock),
        mode="gate",
    )
    seen = []

    def fake_run(command, **kwargs):
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("lope.processes.run_subprocess_group", fake_run)
    result = run_gate(
        GateSpec(name="required", cmd="slow", timeout=480),
        tmp_path,
        context=context,
    )
    assert seen == [5.0]
    assert result.exit_code == 124
    assert result.error.startswith("run_budget_exhausted")


def test_gate_suite_stops_scheduling_after_deadline_and_required_fails_closed(
    tmp_path, monkeypatch
):
    class Clock:
        now = 100.0

        def __call__(self):
            return self.now

    clock = Clock()
    context = InvocationContext(
        budget=RunBudget(
            mode="gate",
            run_timeout=10,
            cleanup_reserve_seconds=0,
            clock=clock,
        ),
        mode="gate",
    )
    calls = []

    def fake_gate(spec, _root, default_timeout=480, context=None):
        calls.append(spec.name)
        clock.now += 11
        return GateResult(spec.name, True, spec.required, spec.type, None, 0)

    monkeypatch.setattr("lope.gates.run_gate", fake_gate)
    specs = [
        GateSpec(name="first", cmd="true"),
        GateSpec(name="required-skipped", cmd="true", required=True),
        GateSpec(name="optional-skipped", cmd="true", required=False),
    ]
    results = run_gates(specs, tmp_path, context=context)
    assert calls == ["first"]
    assert results[1].required and not results[1].ok
    assert results[1].exit_code == 124
    assert results[2].required is False and not results[2].ok
