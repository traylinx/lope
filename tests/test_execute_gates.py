from lope.executor import ImplementationResult, PhaseExecutor
from lope.models import Phase, PhaseVerdict, SprintDoc, ValidatorResult, VerdictStatus


class _Pool:
    def __init__(self):
        self.prompts = []

    def validate(self, prompt, timeout=480):
        self.prompts.append(prompt)
        return ValidatorResult(
            validator_name='stub',
            verdict=PhaseVerdict(
                status=VerdictStatus.PASS,
                confidence=0.9,
                rationale='tests passed in foo.py:1',
            ),
            raw_response='ok',
        )


class _GateRun:
    def __init__(self, failures):
        self._failures = failures

    def blocking_failures(self):
        return list(self._failures)


def test_execute_gates_downgrade_pass_and_retry():
    pool = _Pool()
    calls = {'impl': 0, 'gate': 0}

    def impl(phase, fix_context=None):
        calls['impl'] += 1
        if calls['impl'] == 2:
            assert fix_context == ['Objective gate failed: coverage dropped']
        return ImplementationResult(ok=True, summary='changed foo.py')

    def gate_runner(phase, attempt):
        calls['gate'] += 1
        if calls['gate'] == 1:
            return _GateRun(['coverage dropped'])
        return _GateRun([])

    doc = SprintDoc(
        slug='x',
        title='SPRINT-X',
        phases=[Phase(index=1, name='p1', goal='do x', criteria=['works'])],
    )
    report = PhaseExecutor(
        pool,
        impl,
        max_rounds_per_phase=2,
        gate_runner=gate_runner,
    ).run(doc)

    assert report.ok is True
    assert calls == {'impl': 2, 'gate': 2}
    assert any('Objective gate report' in p for p in pool.prompts)


def test_execute_gates_escalate_after_retries_exhausted():
    pool = _Pool()

    def impl(phase, fix_context=None):
        return ImplementationResult(ok=True, summary='changed foo.py')

    def gate_runner(phase, attempt):
        return _GateRun(['tests failed'])

    doc = SprintDoc(
        slug='x',
        title='SPRINT-X',
        phases=[Phase(index=1, name='p1', goal='do x')],
    )
    report = PhaseExecutor(
        pool,
        impl,
        max_rounds_per_phase=1,
        gate_runner=gate_runner,
    ).run(doc)

    assert report.ok is False
    assert 'objective gates' in report.error.lower()
    assert doc.phases[0].verdict.status == VerdictStatus.NEEDS_FIX
