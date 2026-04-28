from pathlib import Path
import sys

from lope.gates import (
    GateSpec, compare_results, default_baseline_path, load_baseline,
    load_gate_specs, run_gate, run_gates, save_baseline,
)


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
    baseline = save_baseline(before_results, cwd=tmp_path)
    loaded = load_baseline(cwd=tmp_path)
    after_spec = GateSpec(name='score', cmd=sys.executable + ' -c "print(90)"', type='regex_number', regex=r'(\d+)', min_delta=0)
    after_results = run_gates([after_spec], tmp_path)
    comps = compare_results([after_spec], loaded, after_results)
    assert comps[0].passed is False
    assert comps[0].delta == -10
    assert 'min_delta' in comps[0].reason
