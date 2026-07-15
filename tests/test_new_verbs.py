"""Tests for v0.5.0 — ask / review / vote / compare / pipe.

Covers the pure logic that lives in cli.py: vote-tally parsing, winner
election, JSON output shape. Integration with real validators is tested
manually via dogfood — each subprocess run takes seconds per CLI.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

# Ensure the lope package under test is importable even when the test
# harness is run from an arbitrary cwd.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lope.cli import _choice_quorum, _parse_vote, _vote_winner


# ─── _parse_vote — option label extraction ─────────────────────────

class TestParseVote:
    def test_matches_exact_label(self):
        assert _parse_vote("I choose A", ["A", "B", "C"]) == "A"
        assert _parse_vote("B is the right answer", ["A", "B", "C"]) == "B"

    def test_case_insensitive(self):
        assert _parse_vote("option a", ["A", "B"]) == "A"
        assert _parse_vote("YES", ["yes", "no"]) == "yes"

    def test_preserves_canonical_case(self):
        # Canonical label comes from the options list, not the answer text.
        assert _parse_vote("YES please", ["yes", "no"]) == "yes"
        assert _parse_vote("no thanks", ["Yes", "No"]) == "No"

    def test_word_boundary_prevents_substring_match(self):
        # 'A' must not match inside 'ALGORITHM'
        assert _parse_vote("ALGORITHM is great", ["A", "B"]) is None
        # 'yes' must not match inside 'yesterday'
        assert _parse_vote("yesterday was great", ["yes", "no"]) is None

    def test_longest_first_disambiguation(self):
        # When options overlap, the longest label wins.
        assert _parse_vote("3.13 is newest", ["3.1", "3.13"]) == "3.13"

    def test_none_when_no_match(self):
        assert _parse_vote("neither of those", ["A", "B", "C"]) is None
        assert _parse_vote("", ["A", "B"]) is None

    def test_first_occurrence_wins(self):
        # If the answer mentions multiple options, the first matched
        # label (in the options iteration order — which we iterate
        # longest-first) is what comes back.
        result = _parse_vote("maybe A or possibly B", ["A", "B"])
        assert result in {"A", "B"}  # deterministic but order-sensitive
        # Key property: we MUST return one of them, not None.

    def test_multiword_option(self):
        assert _parse_vote("I pick no way", ["yes", "no way", "maybe"]) == "no way"

    def test_handles_punctuation(self):
        assert _parse_vote("My answer: A.", ["A", "B"]) == "A"
        assert _parse_vote("(B) is better", ["A", "B"]) == "B"


# ─── _vote_winner — plurality election with tie detection ─────────

class TestVoteWinner:
    def test_strict_plurality(self):
        assert _vote_winner({"A": 3, "B": 1, "C": 0}) == "A"
        assert _vote_winner({"A": 1, "B": 2}) == "B"

    def test_tie_returns_none(self):
        assert _vote_winner({"A": 2, "B": 2}) is None
        assert _vote_winner({"A": 1, "B": 1, "C": 1}) is None

    def test_all_zero_returns_none(self):
        assert _vote_winner({"A": 0, "B": 0}) is None
        # Empty tally also returns None, not KeyError.
        assert _vote_winner({}) is None

    def test_single_voter(self):
        assert _vote_winner({"A": 1, "B": 0}) == "A"

    def test_one_option_zero_votes(self):
        assert _vote_winner({"A": 0}) is None


class TestChoiceQuorum:
    class Pool:
        def names(self):
            return ["a", "b", "c"]

    def test_requires_majority_of_selected_valid_choices(self):
        ok, reason = _choice_quorum(self.Pool(), [
            ("a", "A", "A", None),
            ("b", None, "", "timeout"),
            ("c", None, "tool call", None),
        ])
        assert not ok
        assert "1/2" in reason

    def test_two_valid_choices_meet_quorum(self):
        ok, reason = _choice_quorum(self.Pool(), [
            ("a", "A", "A", None),
            ("b", "B", "B", None),
            ("c", None, "", "timeout"),
        ])
        assert ok
        assert reason == ""


# ─── EnsemblePool re-export compat ────────────────────────────────

class TestEnsembleReExports:
    def test_import_from_validators(self):
        """Back-compat — old imports must keep working."""
        from lope.validators import EnsemblePool as V_Ensemble
        from lope.validators import _synthesize as v_syn

        from lope.ensemble import EnsemblePool as E_Ensemble
        from lope.ensemble import synthesize as e_syn

        assert V_Ensemble is E_Ensemble
        assert v_syn is e_syn

    def test_import_from_package_root(self):
        """`from lope import EnsemblePool` — the library-facing entry point."""
        from lope import EnsemblePool
        from lope.ensemble import EnsemblePool as E_Ensemble
        assert EnsemblePool is E_Ensemble

    def test_ensemble_pool_requires_validators(self):
        from lope.ensemble import EnsemblePool
        with pytest.raises(ValueError, match="at least one validator"):
            EnsemblePool(validators=[])


# ─── GenericSubprocessValidator.generate ──────────────────────────

class TestGenericGenerate:
    """The v0.5.0 addition — generic providers now support .generate().

    We use `echo` as a trivial subprocess target so the tests stay
    hermetic and don't shell out to any actual AI CLI.
    """

    def _make(self, command):
        from lope.generic_validators import GenericSubprocessValidator
        return GenericSubprocessValidator({
            "name": "test-echo",
            "type": "subprocess",
            "command": command,
        })

    def test_generate_returns_stdout(self):
        v = self._make(["echo", "hello world"])
        out = v.generate("ignored-prompt", timeout=5)
        assert out.strip() == "hello world"

    def test_generate_raises_on_empty_output(self):
        # `true` exits 0 with no stdout — should surface as RuntimeError
        # so the fan-out helper can mark the validator as errored rather
        # than silently producing an empty "answer".
        v = self._make(["true"])
        with pytest.raises(RuntimeError, match="empty output"):
            v.generate("ignored", timeout=5)

    def test_generate_raises_on_nonzero_exit(self):
        v = self._make(["false"])
        with pytest.raises(RuntimeError, match="exited"):
            v.generate("ignored", timeout=5)

    def test_generate_raises_on_missing_binary(self):
        v = self._make(["definitely-not-a-real-binary-xyz-123"])
        with pytest.raises(RuntimeError, match="not found|No such"):
            v.generate("ignored", timeout=5)

    def test_generate_honors_prompt_substitution(self):
        v = self._make(["echo", "marker-{prompt}-end"])
        out = v.generate("payload", timeout=5)
        assert "marker-payload-end" in out

    def test_provider_timeout_cannot_extend_call_timeout(self):
        from lope.generic_validators import GenericSubprocessValidator

        v = GenericSubprocessValidator({
            "name": "slow",
            "type": "subprocess",
            "command": [sys.executable, "-c", "import time; time.sleep(2)"],
            "timeout": 10,
        })
        started = time.time()
        with pytest.raises(RuntimeError, match="timed out after 1s"):
            v.generate("ignored", timeout=1)
        assert time.time() - started < 2

    def test_provider_timeout_can_shorten_call_timeout(self):
        from lope.generic_validators import GenericSubprocessValidator

        v = GenericSubprocessValidator({
            "name": "slow",
            "type": "subprocess",
            "command": [sys.executable, "-c", "import time; time.sleep(2)"],
            "timeout": 1,
        })
        with pytest.raises(RuntimeError, match="timed out after 1s"):
            v.generate("ignored", timeout=10)


class TestFanoutGenerate:
    def test_fanout_deadline_returns_pending_validator_error(self):
        from lope.cli import _fanout_generate

        class FakeValidator:
            def __init__(self, name, delay, answer):
                self.name = name
                self.delay = delay
                self.answer = answer

            def available(self):
                return True

            def generate(self, prompt, timeout):
                time.sleep(self.delay)
                return self.answer

        class FakePool:
            def __init__(self):
                self._validators = [
                    FakeValidator("fast", 0.0, "OK"),
                    FakeValidator("slow", 0.8, "LATE"),
                ]

        started = time.time()
        results = _fanout_generate(FakePool(), "prompt", timeout=0.05)
        elapsed = time.time() - started

        assert elapsed < 0.6
        by_name = {name: (answer, error) for name, answer, error in results}
        assert by_name["fast"] == ("OK", None)
        assert by_name["slow"][1] == "slow fanout timed out after 0.05s"

    def test_common_fanout_respects_sequential_pool_setting(self):
        import threading

        from lope.cli import _fanout_generate

        active = 0
        peak = 0
        lock = threading.Lock()

        class FakeValidator:
            def __init__(self, name):
                self.name = name

            def available(self):
                return True

            def generate(self, prompt, timeout):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.03)
                with lock:
                    active -= 1
                return f"{self.name}:{prompt}"

        class FakePool:
            _parallel = False
            _validators = [FakeValidator("a"), FakeValidator("b")]

        results = _fanout_generate(
            FakePool(),
            "base",
            timeout=1,
            prompts_by_validator={"a": "role-a", "b": "role-b"},
        )
        assert peak == 1
        assert {name: answer for name, answer, _error in results} == {
            "a": "a:role-a",
            "b": "b:role-b",
        }

    def test_sequential_context_uses_run_deadline_not_one_call_window(self):
        from lope.cli import _fanout_generate
        from lope.runtime import InvocationContext, RunBudget

        class FakeValidator:
            def __init__(self, name):
                self.name = name

            def available(self):
                return True

            def generate(self, _prompt, _timeout):
                time.sleep(0.55)
                return self.name

        class FakePool:
            _parallel = False
            _validators = [FakeValidator("a"), FakeValidator("b")]

        context = InvocationContext(
            budget=RunBudget(mode="ask", run_timeout=10),
            mode="ask",
        )
        results = _fanout_generate(
            FakePool(),
            "prompt",
            timeout=1,
            context=context,
        )
        assert [(name, answer) for name, answer, error in results if not error] == [
            ("a", "a"),
            ("b", "b"),
        ]
