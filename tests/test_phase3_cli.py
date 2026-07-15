from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lope.runtime import InvocationContext, RunBudget


class _StubValidator:
    name = "stub"


class _StubPool:
    def __init__(self, tmp_path):
        self._validators = [_StubValidator()]
        budget = RunBudget(
            mode="ask",
            run_timeout=60,
            max_external_calls=96,
            max_input_bytes=16 * 1024 * 1024,
            max_output_bytes=32 * 1024 * 1024,
        )
        self._invocation_context = InvocationContext(budget=budget, mode="ask")
        self._primary = self._validators[0]
        self.tmp_path = tmp_path

    def names(self):
        return [item.name for item in self._validators]

    def primary_validator(self):
        return self._primary


def _cfg(**overrides):
    values = {
        "request_policy": "auto",
        "max_chunks": 32,
        "max_calls": 96,
        "max_input_bytes": 16 * 1024 * 1024,
        "timeout": 1,
        "parallel": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_33_chunk_cli_rejection_launches_zero_validators(
    monkeypatch, capsys, tmp_path
):
    import lope.cli as cli

    pool = _StubPool(tmp_path)
    launches = []
    monkeypatch.setattr(cli, "_ensure_config", lambda _args: (_cfg(), pool))
    monkeypatch.setattr(
        cli,
        "_fanout_generate",
        lambda *_args, **_kwargs: launches.append(True) or [],
    )
    args = SimpleNamespace(
        question="x" * (33 * 64 * 1024),
        context="",
        brain_context=None,
        json=False,
        synth=False,
        anonymous=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_ask(args)

    assert exc_info.value.code == 2
    assert launches == []
    assert "--max-chunks 33 --max-calls 34" in capsys.readouterr().err


def test_chunk_evidence_path_is_exposed_in_machine_runtime(
    monkeypatch, tmp_path
):
    import lope.cli as cli

    pool = _StubPool(tmp_path)
    monkeypatch.setenv("LOPE_RUN_OUTPUT_DIR", str(tmp_path / "outputs"))
    path = cli._write_chunk_evidence(
        pool,
        "ask",
        [{"chunk": 1, "chunk_label": "part", "validator": "stub", "answer": "ok"}],
    )

    assert path is not None
    artifact = pool._invocation_context.budget.snapshot()["artifacts"][0]
    assert artifact["kind"] == "ask_chunk_evidence"
    assert artifact["path"] == path
    assert artifact["chunks"] == 1
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["chunks"][0]["answer"] == "ok"


def test_forced_single_chunk_executes_forecast_reduction(
    monkeypatch, tmp_path
):
    import lope.cli as cli
    from lope.request_plan import plan_request
    from lope.synthesis import SynthesisResult

    pool = _StubPool(tmp_path)
    monkeypatch.setenv("LOPE_RUN_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(
        cli,
        "_fanout_generate",
        lambda *_args, **_kwargs: [
            ("stub", "A substantive mapped answer with enough detail.", None)
        ],
    )
    reductions = []
    monkeypatch.setattr(
        cli,
        "_execute_bounded_synthesis",
        lambda *_args, **_kwargs: reductions.append(True)
        or SynthesisResult(ok=True, text="summary", primary="stub"),
    )
    plan = plan_request(
        "small input",
        mode="ask",
        validators=pool._validators,
        policy="chunk",
        chunk_extra_calls=1,
    )
    args = SimpleNamespace(synth=False, anonymous=False)

    _results, synthesis, _evidence = cli._run_chunked_fanout(
        args,
        _cfg(request_policy="chunk"),
        pool,
        plan,
        task="answer",
        mode="ask",
    )

    assert plan.required_chunks == 1
    assert plan.planned_calls == 2
    assert reductions == [True]
    assert synthesis is not None and synthesis.ok


def test_overlap_findings_are_deduped_after_chunk_merge(
    monkeypatch, tmp_path
):
    import lope.cli as cli
    import lope.review as review_module
    from lope.findings import Finding
    from lope.request_plan import RequestChunk
    from lope.review import ReviewReport

    pool = _StubPool(tmp_path)
    monkeypatch.setenv("LOPE_RUN_OUTPUT_DIR", str(tmp_path / "outputs"))

    def fake_review(*_args, **_kwargs):
        finding = Finding(
            message="Boundary bug is duplicated by overlap",
            validator="stub",
            file="sample.py",
            line=9,
            severity="high",
            confidence=0.9,
        )
        return ReviewReport(
            target="chunk",
            focus="correctness",
            validators=["stub"],
            raw_results=[
                {
                    "validator": "stub",
                    "answer": "- [HIGH] sample.py:9 — Boundary bug is duplicated by overlap",
                    "error": None,
                }
            ],
            parse_methods={"stub": "structured"},
            findings=[finding],
            merged=[],
            scored=[],
            errors=[],
            raw_count=1,
            merged_count=0,
            fallback=False,
        )

    monkeypatch.setattr(review_module, "run_consensus_review", fake_review)
    chunks = [
        RequestChunk(0, "line 8\nline 9\n", ("first",), 8, 9, "python"),
        RequestChunk(
            1,
            "line 9\nline 10\n",
            ("second",),
            9,
            10,
            "python",
            overlap_bytes=len("line 9\n"),
        ),
    ]
    args = SimpleNamespace(
        focus="correctness",
        similarity=0.85,
        min_consensus=0.0,
        json=True,
    )

    report = cli._build_report_via_semantic_chunks(
        args,
        chunks=chunks,
        target_label="sample.py",
        fallback_source="sample.py",
        validator_names=["stub"],
        pool=pool,
        cfg=_cfg(),
        brain_context_block=None,
    )

    assert report.raw_count == 2
    assert report.merged_count == 1
    assert len(report.scored) == 1


def test_chunk_quorum_counts_validators_not_chunk_rows():
    from lope.cli import _fanout_quorum

    ok, reason = _fanout_quorum(
        [
            ("claude", "", "chunk 1 timeout"),
            ("claude", "A substantive answer from chunk 2.", None),
        ],
        expected_names=["claude"],
    )
    assert ok
    assert reason == ""


def test_raw_quorum_rejects_two_timeouts_and_one_tool_only_result():
    from lope.cli import _fanout_quorum

    ok, reason = _fanout_quorum(
        [
            ("a", "", "timeout"),
            ("b", "", "timeout"),
            ("c", '{"tool_calls":[{"name":"read"}]}', None),
        ],
        expected_names=["a", "b", "c"],
    )
    assert not ok
    assert "0/2" in reason


def test_full_cli_shape_two_timeouts_and_tool_only_exits_nonzero_with_diagnostics(
    monkeypatch,
    capsys,
    tmp_path,
):
    import lope.cli as cli

    pool = _StubPool(tmp_path)
    pool._validators = [
        SimpleNamespace(name="a"),
        SimpleNamespace(name="b"),
        SimpleNamespace(name="c"),
    ]
    pool._primary = pool._validators[0]
    monkeypatch.setattr(cli, "_ensure_config", lambda _args: (_cfg(), pool))
    monkeypatch.setattr(
        cli,
        "_fanout_generate",
        lambda *_args, **_kwargs: [
            ("a", "", "provider timed out"),
            ("b", "", "provider timed out"),
            ("c", '{"tool_calls":[{"name":"read"}]}', None),
        ],
    )
    args = SimpleNamespace(
        question="Review the sprint.",
        context="",
        brain_context=None,
        json=True,
        synth=False,
        anonymous=False,
        brain_log=False,
    )

    with pytest.raises(SystemExit) as raised:
        cli._cmd_ask(args)

    assert raised.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["partial"] is True
    assert "substantive-result quorum 0/2" in payload["reason"]
    responses = payload["responses"]
    assert [row["error"] for row in responses[:2]] == [
        "provider timed out",
        "provider timed out",
    ]
    assert responses[2]["answer"].startswith('{"tool_calls"')
