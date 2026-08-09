"""Tests for the per-validator latency ledger and pre-launch fit check.

Covers:
  - record()/stats() round-trip, percentiles, and per-validator isolation
  - the sample window stays bounded and keeps the most recent observations
  - timed-out calls are stored as censored samples that bias estimates upward
  - LOPE_LATENCY=off disables recording, reading, and advice
  - a corrupt or unwritable ledger degrades to "no advice", never an exception
  - timeout precedence: stricter-wins by default, provider-wins under the flag
  - fit_warnings reports a clamp, reports a predicted timeout, and stays quiet
    when the budget fits or when there is not enough history to judge
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lope import latency


class _FakeValidator:
    """Minimal stand-in exposing the two attributes the ledger reads."""

    def __init__(self, name, timeout_override=None):
        self.name = name
        self._timeout_override = timeout_override


def _use_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    monkeypatch.delenv("LOPE_LATENCY", raising=False)
    monkeypatch.delenv("LOPE_RESPECT_PROVIDER_TIMEOUT", raising=False)
    return tmp_path / "latency.json"


# ---------------------------------------------------------------------------
# Recording and summarising
# ---------------------------------------------------------------------------

def test_record_then_stats_round_trip(tmp_path, monkeypatch):
    path = _use_ledger(tmp_path, monkeypatch)
    for value in (10.0, 20.0, 30.0):
        latency.record("alpha", value)

    assert path.exists()
    summary = latency.stats("alpha")
    assert summary is not None
    assert summary.count == 3
    assert summary.maximum == 30.0
    assert summary.timeouts == 0
    assert 10.0 <= summary.p50 <= 30.0


def test_stats_unknown_validator_is_none(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    latency.record("alpha", 5.0)
    assert latency.stats("never-run") is None


def test_validators_are_isolated(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    latency.record("alpha", 100.0)
    latency.record("beta", 1.0)

    alpha = latency.stats("alpha")
    beta = latency.stats("beta")
    assert alpha is not None and beta is not None
    assert alpha.maximum == 100.0
    assert beta.maximum == 1.0


def test_p90_tracks_the_slow_tail(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    for _ in range(9):
        latency.record("alpha", 10.0)
    latency.record("alpha", 200.0)

    summary = latency.stats("alpha")
    assert summary is not None
    assert summary.p50 == 10.0
    assert summary.p90 >= 10.0
    assert summary.maximum == 200.0
    # The recommendation must clear the tail it just observed.
    assert summary.recommended_ceiling() > summary.p90


def test_sample_window_is_bounded_and_keeps_recent(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    total = latency.MAX_SAMPLES_PER_VALIDATOR + 25
    for index in range(total):
        latency.record("alpha", float(index + 1))

    summary = latency.stats("alpha")
    assert summary is not None
    assert summary.count == latency.MAX_SAMPLES_PER_VALIDATOR
    # The oldest (smallest) observations were evicted, the newest kept.
    assert summary.maximum == float(total)


def test_timeouts_are_recorded_as_censored_samples(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    latency.record("alpha", 240.0, outcome="provider_timeout")
    latency.record("alpha", 30.0, outcome="ok")

    summary = latency.stats("alpha")
    assert summary is not None
    assert summary.count == 2
    assert summary.timeouts == 1
    # A kill at 240s is a lower bound, so it must drag the estimate upward.
    assert summary.maximum == 240.0


def test_non_positive_and_empty_observations_are_ignored(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    latency.record("alpha", 0.0)
    latency.record("alpha", -5.0)
    latency.record("alpha", 0.0004)  # rounds to 0.0 — meaningless as a sample
    latency.record("", 10.0)
    assert latency.stats("alpha") is None


def test_only_informative_outcomes_are_recorded(tmp_path, monkeypatch):
    """A crash or cancellation says nothing about how long the model needs."""
    _use_ledger(tmp_path, monkeypatch)
    for bad in ("launch_error", "nonzero_exit", "parse_error", "cancelled", "circuit_open"):
        latency.record("alpha", 5.0, outcome=bad)
    assert latency.stats("alpha") is None

    latency.record("alpha", 5.0, outcome="ok")
    latency.record("alpha", 240.0, outcome="provider_timeout")
    summary = latency.stats("alpha")
    assert summary is not None and summary.count == 2


# ---------------------------------------------------------------------------
# Failure containment — advisory telemetry must never break a run
# ---------------------------------------------------------------------------

def test_disabled_by_env(tmp_path, monkeypatch):
    path = _use_ledger(tmp_path, monkeypatch)
    monkeypatch.setenv("LOPE_LATENCY", "off")

    latency.record("alpha", 10.0)
    assert not path.exists()
    assert latency.stats("alpha") is None
    assert latency.fit_warnings([_FakeValidator("alpha", 600)], 10) == []


def test_corrupt_ledger_is_survivable(tmp_path, monkeypatch):
    path = _use_ledger(tmp_path, monkeypatch)
    path.write_text("{not json at all", encoding="utf-8")

    assert latency.stats("alpha") is None
    latency.record("alpha", 12.0)  # must not raise; rewrites the file
    summary = latency.stats("alpha")
    assert summary is not None and summary.count == 1


def test_ledger_with_wrong_shape_is_survivable(tmp_path, monkeypatch):
    path = _use_ledger(tmp_path, monkeypatch)
    path.write_text(json.dumps({"validators": {"alpha": "not-a-dict"}}), encoding="utf-8")
    assert latency.stats("alpha") is None


def test_record_never_raises_when_unwritable(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)

    def _boom(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(latency, "_atomic_write", _boom)
    latency.record("alpha", 10.0)  # swallowed


# ---------------------------------------------------------------------------
# Timeout precedence
# ---------------------------------------------------------------------------

def test_effective_ceiling_is_stricter_of_the_two_by_default(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    slow = _FakeValidator("slow", 600)
    assert latency.effective_ceiling(slow, 240) == 240
    assert latency.effective_ceiling(slow, 900) == 600


def test_effective_ceiling_honours_provider_under_flag(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    monkeypatch.setenv("LOPE_RESPECT_PROVIDER_TIMEOUT", "1")
    slow = _FakeValidator("slow", 600)
    assert latency.effective_ceiling(slow, 240) == 600


def test_effective_ceiling_without_provider_timeout(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    plain = _FakeValidator("plain", None)
    assert latency.effective_ceiling(plain, 240) == 240
    assert latency.effective_ceiling(plain, None) is None


def test_generic_validators_effective_timeout_matches(monkeypatch):
    from lope.generic_validators import _effective_timeout

    monkeypatch.delenv("LOPE_RESPECT_PROVIDER_TIMEOUT", raising=False)
    assert _effective_timeout(600, 240) == 240
    assert _effective_timeout(240, 600) == 240
    assert _effective_timeout(None, 240) == 240
    assert _effective_timeout(600, None) == 600

    monkeypatch.setenv("LOPE_RESPECT_PROVIDER_TIMEOUT", "1")
    assert _effective_timeout(600, 240) == 600
    # The escape hatch still cannot invent a budget out of nothing.
    assert _effective_timeout(None, 240) == 240


# ---------------------------------------------------------------------------
# Fit check
# ---------------------------------------------------------------------------

def test_fit_warning_reports_a_predicted_timeout(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    for _ in range(5):
        latency.record("slow", 178.0)

    lines = latency.fit_warnings([_FakeValidator("slow", None)], 240)
    assert len(lines) == 1
    assert "slow" in lines[0]
    assert "predicted timeout" in lines[0]
    assert "p90" in lines[0]


def test_fit_warning_reports_a_clamp(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    lines = latency.fit_warnings([_FakeValidator("slow", 600)], 240)
    assert len(lines) == 1
    assert "clamps its configured 600s" in lines[0]
    assert "--respect-provider-timeout" in lines[0]


def test_fit_check_is_quiet_when_the_budget_fits(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    for _ in range(5):
        latency.record("quick", 10.0)
    assert latency.fit_warnings([_FakeValidator("quick", None)], 600) == []


def test_fit_check_is_quiet_without_enough_history(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    latency.record("new", 500.0)  # one sample only
    assert latency.stats("new").count < latency.MIN_SAMPLES_FOR_ADVICE
    assert latency.fit_warnings([_FakeValidator("new", None)], 10) == []


def test_fit_check_quiet_when_no_call_timeout(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)
    for _ in range(5):
        latency.record("slow", 178.0)
    assert latency.fit_warnings([_FakeValidator("slow", None)], None) == []


def test_fit_check_uses_the_enforced_ceiling_not_the_flag(tmp_path, monkeypatch):
    """A provider clamped to 240 is judged against 240, not its configured 600."""
    _use_ledger(tmp_path, monkeypatch)
    for _ in range(5):
        latency.record("slow", 178.0)

    lines = latency.fit_warnings([_FakeValidator("slow", 600)], 240)
    assert len(lines) == 1
    assert "ceiling 240s" in lines[0]
    assert "clamps its configured 600s" in lines[0]
    assert "predicted timeout" in lines[0]

    # Honouring the provider's 600s budget resolves both complaints.
    monkeypatch.setenv("LOPE_RESPECT_PROVIDER_TIMEOUT", "1")
    assert latency.fit_warnings([_FakeValidator("slow", 600)], 240) == []


def test_fit_check_tolerates_broken_validator_objects(tmp_path, monkeypatch):
    _use_ledger(tmp_path, monkeypatch)

    class _Hostile:
        @property
        def name(self):
            raise RuntimeError("boom")

    assert latency.fit_warnings([_Hostile(), _FakeValidator("", None)], 240) == []
