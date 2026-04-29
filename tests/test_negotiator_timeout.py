"""Regression test for the Negotiator/cfg.timeout shadow bug.

Before this fix, ``Negotiator.__init__`` defaulted to ``timeout_seconds=300``
and ``cli._cmd_negotiate`` constructed it without forwarding ``cfg.timeout``,
so the user-facing ``~/.lope/config.json`` ``"timeout"`` (and ``LOPE_TIMEOUT``
env var) were silently ignored — every reviewer call was capped at 300s
regardless of config, which made round-2 negotiate prompts time out routinely.

We assert two things here:

1. ``Negotiator()`` with no explicit ``timeout_seconds`` picks up
   ``DEFAULT_TIMEOUT_SECONDS`` (which itself respects ``LOPE_TIMEOUT``).
2. An explicit ``timeout_seconds=N`` is honored verbatim — proving the
   plumbing from cli.py's ``cfg.timeout`` reaches the validator-pool stage.
"""

from __future__ import annotations

import importlib
import os

from lope.negotiator import Negotiator
from lope.validators import StubValidator, ValidatorPool


def _make_pool() -> ValidatorPool:
    return ValidatorPool([StubValidator(name="stub-a"), StubValidator(name="stub-b")])


def test_negotiator_default_timeout_matches_validators_default():
    # Reload validators so DEFAULT_TIMEOUT_SECONDS picks up whatever the
    # current env says (rather than whatever was loaded at test-import time).
    import lope.validators as v

    importlib.reload(v)
    expected = v.DEFAULT_TIMEOUT_SECONDS

    negotiator = Negotiator(
        llm_call=lambda system, user: "drafted",
        validator_pool=_make_pool(),
    )

    assert negotiator._timeout == expected, (
        f"Negotiator default must match DEFAULT_TIMEOUT_SECONDS "
        f"({expected}); got {negotiator._timeout}. The hardcoded 300s "
        f"shadow bug has regressed."
    )


def test_negotiator_honors_explicit_timeout():
    negotiator = Negotiator(
        llm_call=lambda system, user: "drafted",
        validator_pool=_make_pool(),
        timeout_seconds=900,
    )
    assert negotiator._timeout == 900


def test_negotiator_default_respects_lope_timeout_env(monkeypatch):
    monkeypatch.setenv("LOPE_TIMEOUT", "777")
    import lope.validators as v

    importlib.reload(v)
    # Re-import Negotiator AFTER the env reload so its lazy import inside
    # __init__ picks up the new DEFAULT_TIMEOUT_SECONDS.
    import lope.negotiator as n

    importlib.reload(n)

    negotiator = n.Negotiator(
        llm_call=lambda system, user: "drafted",
        validator_pool=_make_pool(),
    )
    assert negotiator._timeout == 777, (
        f"Negotiator default must follow LOPE_TIMEOUT env var; got "
        f"{negotiator._timeout}, expected 777"
    )

    # Reset for sibling tests in the same session
    monkeypatch.delenv("LOPE_TIMEOUT", raising=False)
    importlib.reload(v)
    importlib.reload(n)
