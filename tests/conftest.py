"""Suite-wide safety net: keep tests out of the operator's real ~/.lope.

The latency ledger is written from the runtime call-accounting path, which a
great many tests exercise with synthetic validator names ("stub", "a", "b").
Without this fixture, running the suite silently writes that fixture data into
the user's real ledger and skews the very budget advice the ledger exists to
provide. Tests that genuinely exercise the ledger opt back in by pointing
LOPE_HOME at tmp_path and clearing this variable — see tests/test_latency.py.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_latency_ledger(monkeypatch):
    monkeypatch.setenv("LOPE_LATENCY", "off")
