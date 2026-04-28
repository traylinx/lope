"""Generic evidence gates for Lope.

Gates are user-authored project commands that produce objective evidence
(exit status, JSON number, or regex number). Lope can save a baseline, compare
a later run, and feed regressions back into agent retries. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .redaction import redact_text

DEFAULT_CONFIG = Path('.lope') / 'rules.json'
DEFAULT_BASELINE = Path('.lope') / 'gate-baseline.json'
TAIL_CHARS = 4000


class GateConfigError(ValueError):
    """Invalid gate config or baseline."""


@dataclass
class GateSpec:
    name: str
    cmd: str
    type: str = 'exit'  # exit | json_number | regex_number
    required: bool = True
    path: Optional[str] = None
    regex: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_delta: Optional[float] = None
    max_delta_drop: Optional[float] = None
    timeout: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GateSpec':
        if not isinstance(data, dict):
            raise GateConfigError('gate entry must be an object')
        name = str(data.get('name') or '').strip()
        cmd = str(data.get('cmd') or '').strip()
        if not name:
            raise GateConfigError('gate missing non-empty name')
        if not cmd:
            raise GateConfigError('gate %r missing non-empty cmd' % name)
        typ = str(data.get('type') or 'exit').strip()
        if typ not in {'exit', 'json_number', 'regex_number'}:
            raise GateConfigError('gate %r has unsupported type %r' % (name, typ))
        if typ == 'json_number' and not data.get('path'):
            raise GateConfigError('gate %r type json_number requires path' % name)
        if typ == 'regex_number' and not data.get('regex'):
            raise GateConfigError('gate %r type regex_number requires regex' % name)
        return cls(
            name=name,
            cmd=cmd,
            type=typ,
            required=bool(data.get('required', True)),
            path=str(data['path']) if data.get('path') is not None else None,
            regex=str(data['regex']) if data.get('regex') is not None else None,
            min_value=_float_or_none(data.get('min_value')),
            max_value=_float_or_none(data.get('max_value')),
            min_delta=_float_or_none(data.get('min_delta')),
            max_delta_drop=_float_or_none(data.get('max_delta_drop')),
            timeout=_int_or_none(data.get('timeout')),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'cmd': self.cmd,
            'type': self.type,
            'required': self.required,
            'path': self.path,
            'regex': self.regex,
            'min_value': self.min_value,
            'max_value': self.max_value,
            'min_delta': self.min_delta,
            'max_delta_drop': self.max_delta_drop,
            'timeout': self.timeout,
        }


@dataclass
class GateResult:
    name: str
    ok: bool
    required: bool
    type: str
    value: Optional[float]
    exit_code: int
    stdout_tail: str = ''
    stderr_tail: str = ''
    duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'ok': self.ok,
            'required': self.required,
            'type': self.type,
            'value': self.value,
            'exit_code': self.exit_code,
            'stdout_tail': self.stdout_tail,
            'stderr_tail': self.stderr_tail,
            'duration_ms': self.duration_ms,
            'error': self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GateResult':
        return cls(
            name=str(data.get('name') or ''),
            ok=bool(data.get('ok')),
            required=bool(data.get('required', True)),
            type=str(data.get('type') or 'exit'),
            value=_float_or_none(data.get('value')),
            exit_code=int(data.get('exit_code', 0)),
            stdout_tail=str(data.get('stdout_tail') or ''),
            stderr_tail=str(data.get('stderr_tail') or ''),
            duration_ms=int(data.get('duration_ms') or 0),
            error=data.get('error'),
        )


@dataclass
class GateComparison:
    name: str
    passed: bool
    required: bool
    before: Optional[GateResult]
    after: GateResult
    delta: Optional[float]
    reason: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'passed': self.passed,
            'required': self.required,
            'before': self.before.to_dict() if self.before else None,
            'after': self.after.to_dict(),
            'delta': self.delta,
            'reason': self.reason,
        }


@dataclass
class GateRun:
    mode: str
    project_root: str
    config_path: Optional[str]
    baseline_path: str
    passed: bool
    results: List[GateResult] = field(default_factory=list)
    comparisons: List[GateComparison] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'mode': self.mode,
            'project_root': self.project_root,
            'config_path': self.config_path,
            'baseline_path': self.baseline_path,
            'passed': self.passed,
            'duration_ms': self.duration_ms,
            'results': [r.to_dict() for r in self.results],
            'comparisons': [c.to_dict() for c in self.comparisons],
        }

    def blocking_failures(self) -> List[str]:
        if self.comparisons:
            return [c.reason or c.name for c in self.comparisons if c.required and not c.passed]
        return [r.error or '%s failed' % r.name for r in self.results if r.required and not r.ok]


def load_gate_specs(config_path: Optional[str] = None, cwd: Optional[Path] = None) -> Tuple[List[GateSpec], Optional[Path]]:
    root = Path(cwd or os.getcwd()).resolve()
    path = Path(config_path).expanduser() if config_path else root / DEFAULT_CONFIG
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        return [], path
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise GateConfigError('%s: invalid JSON: %s' % (path, exc)) from None
    if not isinstance(payload, dict):
        raise GateConfigError('%s: root must be an object' % path)
    gates = payload.get('gates', [])
    if gates is None:
        gates = []
    if not isinstance(gates, list):
        raise GateConfigError('%s: gates must be a list' % path)
    return [GateSpec.from_dict(g) for g in gates], path


def default_baseline_path(cwd: Optional[Path] = None) -> Path:
    return Path(cwd or os.getcwd()).resolve() / DEFAULT_BASELINE


def run_gates(specs: Sequence[GateSpec], cwd: Optional[Path] = None, default_timeout: int = 480) -> List[GateResult]:
    root = Path(cwd or os.getcwd()).resolve()
    return [run_gate(spec, root, default_timeout=default_timeout) for spec in specs]


def run_gate(spec: GateSpec, cwd: Path, default_timeout: int = 480) -> GateResult:
    start = time.perf_counter_ns()
    timeout = spec.timeout or default_timeout
    try:
        proc = subprocess.run(
            spec.cmd,
            shell=True,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        duration_ms = int((time.perf_counter_ns() - start) / 1_000_000)
        stdout = _tail(proc.stdout or '')
        stderr = _tail(proc.stderr or '')
        value = None
        error = None
        ok = proc.returncode == 0
        if ok and spec.type == 'json_number':
            try:
                value = _extract_json_number(proc.stdout or '', spec.path or '')
            except GateConfigError as exc:
                ok = False
                error = str(exc)
        elif ok and spec.type == 'regex_number':
            try:
                value = _extract_regex_number((proc.stdout or '') + '\n' + (proc.stderr or ''), spec.regex or '')
            except GateConfigError as exc:
                ok = False
                error = str(exc)
        if ok and value is not None:
            ok, threshold_error = _check_value_thresholds(spec, value)
            if threshold_error:
                error = threshold_error
        if proc.returncode != 0 and not error:
            error = 'exit code %s' % proc.returncode
        return GateResult(
            name=spec.name,
            ok=ok,
            required=spec.required,
            type=spec.type,
            value=value,
            exit_code=int(proc.returncode),
            stdout_tail=stdout,
            stderr_tail=stderr,
            duration_ms=duration_ms,
            error=redact_text(error).strip() if error else None,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter_ns() - start) / 1_000_000)
        return GateResult(
            name=spec.name,
            ok=False,
            required=spec.required,
            type=spec.type,
            value=None,
            exit_code=124,
            stdout_tail=_tail(exc.stdout or ''),
            stderr_tail=_tail(exc.stderr or ''),
            duration_ms=duration_ms,
            error='timeout after %ss' % timeout,
        )
    except OSError as exc:
        duration_ms = int((time.perf_counter_ns() - start) / 1_000_000)
        return GateResult(
            name=spec.name,
            ok=False,
            required=spec.required,
            type=spec.type,
            value=None,
            exit_code=127,
            duration_ms=duration_ms,
            error=redact_text('%s: %s' % (type(exc).__name__, exc)).strip(),
        )


def save_baseline(results: Sequence[GateResult], path: Optional[Path] = None, cwd: Optional[Path] = None) -> Path:
    baseline = Path(path) if path is not None else default_baseline_path(cwd)
    baseline.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'version': 1,
        'created_at': int(time.time()),
        'results': [r.to_dict() for r in results],
    }
    tmp = baseline.with_suffix(baseline.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(baseline)
    return baseline


def load_baseline(path: Optional[Path] = None, cwd: Optional[Path] = None) -> Dict[str, GateResult]:
    baseline = Path(path) if path is not None else default_baseline_path(cwd)
    if not baseline.exists():
        raise GateConfigError('baseline not found at %s; run `lope gate save` first' % baseline)
    try:
        payload = json.loads(baseline.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise GateConfigError('%s: invalid baseline JSON: %s' % (baseline, exc)) from None
    results = payload.get('results', []) if isinstance(payload, dict) else []
    if not isinstance(results, list):
        raise GateConfigError('%s: baseline results must be a list' % baseline)
    out = {}
    for item in results:
        r = GateResult.from_dict(item)
        if r.name:
            out[r.name] = r
    return out


def compare_results(specs: Sequence[GateSpec], before: Dict[str, GateResult], after: Sequence[GateResult]) -> List[GateComparison]:
    specs_by_name = {s.name: s for s in specs}
    comparisons = []
    for current in after:
        spec = specs_by_name.get(current.name)
        required = current.required if spec is None else spec.required
        prior = before.get(current.name)
        passed = True
        reason = ''
        delta = None
        if not current.ok:
            passed = False
            reason = current.error or '%s failed' % current.name
        elif prior is None:
            passed = True
            reason = 'no baseline for gate'
        elif current.value is not None and prior.value is not None:
            delta = current.value - prior.value
            if spec is not None and spec.min_delta is not None and delta < spec.min_delta:
                passed = False
                reason = 'delta %.4g below min_delta %.4g' % (delta, spec.min_delta)
            if spec is not None and spec.max_delta_drop is not None and (prior.value - current.value) > spec.max_delta_drop:
                passed = False
                reason = 'drop %.4g exceeds max_delta_drop %.4g' % (prior.value - current.value, spec.max_delta_drop)
            if not reason:
                reason = 'delta %.4g' % delta
        else:
            reason = 'exit status unchanged' if current.ok else (current.error or 'failed')
        comparisons.append(GateComparison(current.name, passed, required, prior, current, delta, reason))
    return comparisons


def build_run(mode: str, specs: Sequence[GateSpec], config_path: Optional[Path], baseline_path: Path, results: Sequence[GateResult], comparisons: Sequence[GateComparison], started_ns: int, cwd: Optional[Path] = None) -> GateRun:
    if comparisons:
        passed = all(c.passed or not c.required for c in comparisons)
    else:
        passed = all(r.ok or not r.required for r in results)
    return GateRun(
        mode=mode,
        project_root=str(Path(cwd or os.getcwd()).resolve()),
        config_path=str(config_path) if config_path else None,
        baseline_path=str(baseline_path),
        passed=passed,
        results=list(results),
        comparisons=list(comparisons),
        duration_ms=int((time.perf_counter_ns() - started_ns) / 1_000_000),
    )


def prompt_summary(run: GateRun) -> str:
    lines = ['## Objective gate report']
    if not run.results and not run.comparisons:
        lines.append('- No gates configured.')
        return '\n'.join(lines)
    if run.comparisons:
        for c in run.comparisons:
            status = 'PASS' if c.passed else 'FAIL'
            if c.before and c.before.value is not None and c.after.value is not None:
                detail = '%s: %s, %.4g -> %.4g (%s)' % (c.name, status, c.before.value, c.after.value, c.reason)
            else:
                detail = '%s: %s, %s' % (c.name, status, c.reason)
            lines.append('- ' + detail)
    else:
        for r in run.results:
            status = 'PASS' if r.ok else 'FAIL'
            value = '' if r.value is None else ' value=%.4g' % r.value
            reason = '' if r.ok else ' (%s)' % (r.error or 'failed')
            lines.append('- %s: %s%s%s' % (r.name, status, value, reason))
    return '\n'.join(lines)


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == '':
        return None
    return float(value)


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == '':
        return None
    return int(value)


def _tail(text: str) -> str:
    return redact_text(str(text)[-TAIL_CHARS:]).rstrip()


def _extract_json_number(text: str, path: str) -> float:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateConfigError('JSON parse failed: %s' % exc) from None
    cur = data
    for part in path.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise GateConfigError('JSON path %r not found' % path)
    if not isinstance(cur, (int, float)):
        raise GateConfigError('JSON path %r did not resolve to number' % path)
    return float(cur)


def _extract_regex_number(text: str, regex: str) -> float:
    m = re.search(regex, text, re.MULTILINE)
    if not m:
        raise GateConfigError('regex did not match')
    value = m.group(1) if m.groups() else m.group(0)
    try:
        return float(value)
    except ValueError:
        raise GateConfigError('regex capture is not numeric: %r' % value) from None


def _check_value_thresholds(spec: GateSpec, value: float) -> Tuple[bool, Optional[str]]:
    if spec.min_value is not None and value < spec.min_value:
        return False, 'value %.4g below min_value %.4g' % (value, spec.min_value)
    if spec.max_value is not None and value > spec.max_value:
        return False, 'value %.4g above max_value %.4g' % (value, spec.max_value)
    return True, None
