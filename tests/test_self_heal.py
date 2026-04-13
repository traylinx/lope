"""Tests for v0.4.0 Phases 3 + 4 — self-heal detection and execution.

Covers:
  - _is_flag_error matches representative stderr from multiple CLIs
  - _is_flag_error returns False for network / rate-limit / content errors
  - AdapterFlagError carries cli_name + old_argv + stderr
  - _infra_error attaches flag_error_hint when pattern matches
  - SelfHealer.should_attempt gate (env var, session state, reviewer pool)
  - _parse_heal_response handles fenced / bare / malformed JSON
  - _build_heal_prompt includes old argv, stderr, help output
  - _fill_template expands {prompt} and {binary}
  - is_adapter_expired uses the 90-day TTL
  - SelfHealer.attempt end-to-end with mocked reviewer + mocked subprocess:
      - success path persists a LearnedAdapter
      - reviewer returns garbage → no persistence, returns None
      - smoke test fails → no persistence, returns None
      - help capture fails → no persistence, returns None
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lope.config import LearnedAdapter, LopeCfg, default_path, load, save
from lope.healer import (
    LEARNED_ADAPTER_TTL_SECONDS,
    SelfHealer,
    _build_heal_prompt,
    _fill_template,
    _parse_heal_response,
    is_adapter_expired,
)
from lope.validators import (
    AdapterFlagError,
    _is_flag_error,
    _infra_error,
)
from lope.models import VerdictStatus


# ---------------------------------------------------------------------------
# _is_flag_error — pattern detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stderr", [
    "error: unrecognized arguments: --quiet",
    "error: unknown option '--prompt'",
    "error: unrecognized argument '--quiet' found",
    "error: no such option: --print",
    "error: invalid option -- 'x'",
    "Usage: codex [OPTIONS] [PROMPT]",
    "usage: claude [FLAGS]\n  try again",
    "error: unknown flag '--output-format'",
])
def test_is_flag_error_matches_real_patterns(stderr):
    assert _is_flag_error(stderr) is True, f"expected match: {stderr!r}"


@pytest.mark.parametrize("stderr", [
    "",
    "HTTP 429 rate limit exceeded",
    "ConnectionError: Failed to establish connection",
    "timeout after 30s",
    "You've hit your usage limit. Upgrade to Plus",
    "ERROR: Model quota exhausted",
    "Permission denied",
    "content policy violation",
])
def test_is_flag_error_rejects_non_flag_failures(stderr):
    assert _is_flag_error(stderr) is False, f"expected non-match: {stderr!r}"


# ---------------------------------------------------------------------------
# AdapterFlagError — carries the healer context
# ---------------------------------------------------------------------------

def test_adapter_flag_error_carries_context():
    err = AdapterFlagError(
        cli_name="codex",
        old_argv=["codex", "exec", "--quiet", "{prompt}"],
        stderr="error: unrecognized arguments: --quiet",
    )
    assert err.cli_name == "codex"
    assert err.old_argv == ["codex", "exec", "--quiet", "{prompt}"]
    assert "unrecognized" in err.stderr


def test_adapter_flag_error_copies_argv_list():
    argv = ["claude", "--print", "{prompt}"]
    err = AdapterFlagError("claude", argv, "stderr")
    # Mutating original should not affect the stored argv
    argv.append("--extra")
    assert err.old_argv == ["claude", "--print", "{prompt}"]


# ---------------------------------------------------------------------------
# _infra_error — attaches flag_error_hint when pattern matches
# ---------------------------------------------------------------------------

def test_infra_error_attaches_flag_hint_on_match():
    result = _infra_error(
        "codex",
        "codex exited 2: error: unrecognized arguments: --quiet",
    )
    assert result.verdict.status == VerdictStatus.INFRA_ERROR
    assert result.flag_error_hint != ""
    assert "unrecognized" in result.flag_error_hint


def test_infra_error_no_hint_on_non_flag_failure():
    result = _infra_error(
        "codex",
        "HTTP 429 rate limit exceeded",
    )
    assert result.verdict.status == VerdictStatus.INFRA_ERROR
    assert result.flag_error_hint == ""


# ---------------------------------------------------------------------------
# SelfHealer.should_attempt — the gate
# ---------------------------------------------------------------------------

def test_should_attempt_false_when_env_unset(monkeypatch):
    monkeypatch.delenv("LOPE_SELF_HEAL", raising=False)
    h = SelfHealer()
    assert h.should_attempt("claude", reviewer_available=True) is False


def test_should_attempt_true_when_env_one(monkeypatch):
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")
    h = SelfHealer()
    assert h.should_attempt("claude", reviewer_available=True) is True


@pytest.mark.parametrize("val", ["true", "yes", "on", "TRUE", "Yes"])
def test_should_attempt_accepts_truthy_env_values(monkeypatch, val):
    monkeypatch.setenv("LOPE_SELF_HEAL", val)
    h = SelfHealer()
    assert h.should_attempt("claude", reviewer_available=True) is True


def test_should_attempt_false_on_second_call_same_cli(monkeypatch):
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")
    h = SelfHealer()
    assert h.should_attempt("claude", reviewer_available=True) is True
    h.mark_attempted("claude")
    assert h.should_attempt("claude", reviewer_available=True) is False
    # Different CLI still allowed
    assert h.should_attempt("codex", reviewer_available=True) is True


def test_should_attempt_false_when_no_reviewer(monkeypatch):
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")
    h = SelfHealer()
    assert h.should_attempt("claude", reviewer_available=False) is False


def test_should_attempt_false_when_empty_cli_name(monkeypatch):
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")
    h = SelfHealer()
    assert h.should_attempt("", reviewer_available=True) is False


# ---------------------------------------------------------------------------
# _parse_heal_response — tolerant JSON extractor
# ---------------------------------------------------------------------------

def test_parse_heal_response_fenced_json():
    response = """Sure, here is the corrected invocation:
```json
{
  "argv_template": ["claude", "-p", "{prompt}"],
  "stdin_mode": "none",
  "stdout_parser": "plaintext",
  "confidence": 0.92,
  "rationale": "claude 3 uses -p instead of --print"
}
```
"""
    adapter = _parse_heal_response(response)
    assert adapter is not None
    assert adapter.argv_template == ["claude", "-p", "{prompt}"]
    assert adapter.stdin_mode == "none"
    assert adapter.stdout_parser == "plaintext"
    assert adapter.confidence == 0.92


def test_parse_heal_response_bare_json():
    response = '{"argv_template": ["codex", "exec", "{prompt}"], "stdin_mode": "pipe", "confidence": 0.85}'
    adapter = _parse_heal_response(response)
    assert adapter is not None
    assert adapter.argv_template == ["codex", "exec", "{prompt}"]
    assert adapter.stdin_mode == "pipe"
    assert adapter.confidence == 0.85


def test_parse_heal_response_malformed_returns_none():
    assert _parse_heal_response("") is None
    assert _parse_heal_response("not json at all") is None
    assert _parse_heal_response("```json\n{broken\n```") is None


def test_parse_heal_response_missing_argv_returns_none():
    response = '{"stdin_mode": "none", "confidence": 0.9}'
    assert _parse_heal_response(response) is None


def test_parse_heal_response_argv_not_list_returns_none():
    response = '{"argv_template": "claude --print", "confidence": 0.9}'
    assert _parse_heal_response(response) is None


# ---------------------------------------------------------------------------
# _build_heal_prompt — reviewer prompt assembly
# ---------------------------------------------------------------------------

def test_build_heal_prompt_includes_all_context():
    prompt = _build_heal_prompt(
        cli_name="codex",
        old_argv=["codex", "exec", "--quiet", "{prompt}"],
        stderr="error: unrecognized arguments: --quiet",
        help_text="Usage: codex exec [OPTIONS] [PROMPT]",
    )
    assert "codex" in prompt
    assert "--quiet" in prompt
    assert "unrecognized" in prompt
    assert "Usage: codex exec" in prompt
    assert "argv_template" in prompt  # schema instructions
    assert "{prompt}" in prompt       # placeholder documentation


def test_build_heal_prompt_truncates_long_help():
    huge_help = "x" * 10_000
    prompt = _build_heal_prompt(
        cli_name="test",
        old_argv=["test"],
        stderr="err",
        help_text=huge_help,
    )
    # Prompt should be reasonably bounded — help is capped at 4000 chars
    assert len(prompt) < 15_000


# ---------------------------------------------------------------------------
# _fill_template — placeholder expansion
# ---------------------------------------------------------------------------

def test_fill_template_expands_prompt():
    result = _fill_template("{prompt}", "hello world", "claude")
    assert result == "hello world"


def test_fill_template_expands_binary():
    result = _fill_template("{binary}", "hello", "claude")
    assert result == "claude"


def test_fill_template_passes_through_plain_strings():
    result = _fill_template("--print", "hello", "claude")
    assert result == "--print"


# ---------------------------------------------------------------------------
# is_adapter_expired — 90-day TTL
# ---------------------------------------------------------------------------

def test_adapter_expired_old_timestamp():
    adapter = LearnedAdapter(
        argv_template=["x"],
        timestamp=time.time() - LEARNED_ADAPTER_TTL_SECONDS - 3600,
    )
    assert is_adapter_expired(adapter) is True


def test_adapter_fresh_timestamp():
    adapter = LearnedAdapter(
        argv_template=["x"],
        timestamp=time.time() - 3600,  # 1 hour ago
    )
    assert is_adapter_expired(adapter) is False


def test_adapter_zero_timestamp_never_expires():
    """Legacy adapters with no timestamp should not be auto-expired —
    that lets old configs survive the v0.4.0 upgrade."""
    adapter = LearnedAdapter(argv_template=["x"], timestamp=0.0)
    assert is_adapter_expired(adapter) is False


# ---------------------------------------------------------------------------
# SelfHealer.attempt end-to-end — mocked reviewer + mocked subprocess
# ---------------------------------------------------------------------------

class _MockReviewer:
    """Stand-in for a real validator with .generate()."""
    def __init__(self, name: str, response: str):
        self.name = name
        self._response = response

    def generate(self, prompt: str, timeout: int = 120) -> str:
        return self._response


class _RaisingReviewer:
    name = "raising"

    def generate(self, prompt: str, timeout: int = 120) -> str:
        raise RuntimeError("reviewer subprocess crashed")


def _valid_reviewer_response(argv_template):
    return json.dumps({
        "argv_template": argv_template,
        "stdin_mode": "none",
        "stdout_parser": "plaintext",
        "confidence": 0.91,
        "rationale": "corrected invocation",
    })


def test_self_heal_attempt_success_path(tmp_path, monkeypatch):
    """Mocked reviewer returns valid JSON + mocked subprocess returns 'OK'
    → LearnedAdapter persists to ~/.lope/config.json."""
    # Redirect ~/.lope to tmp_path via LOPE_HOME so we don't touch real config
    lope_home = tmp_path / "home_lope"
    lope_home.mkdir()
    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")

    # Seed a minimal global config so the healer has something to merge into
    from lope.config import save as cfg_save
    initial = LopeCfg(validators=["claude", "opencode"], primary="claude",
                      timeout=480, parallel=True)
    cfg_save(initial, str(lope_home / "config.json"))

    # Mock the help capture and smoke test to avoid spawning real subprocesses
    healer = SelfHealer()

    def mock_capture_help(cli_binary):
        return f"Usage: {cli_binary} exec [PROMPT]\n\n(mocked help output)"

    def mock_smoke_test(cli_binary, proposal):
        return True, "OK\n"

    healer._capture_help = mock_capture_help
    healer._smoke_test = mock_smoke_test

    reviewer = _MockReviewer("claude",
                              _valid_reviewer_response(["codex", "exec", "{prompt}"]))

    adapter = healer.attempt(
        cli_name="codex",
        cli_binary="codex",
        old_argv=["codex", "exec", "--quiet", "{prompt}"],
        stderr="error: unrecognized arguments: --quiet",
        reviewer=reviewer,
    )

    assert adapter is not None
    assert adapter.argv_template == ["codex", "exec", "{prompt}"]
    assert adapter.source_cli == "claude"
    assert adapter.timestamp > 0

    # Must be persisted to the temp config
    persisted = load(str(lope_home / "config.json"))
    assert persisted is not None
    assert "codex" in persisted.learned_adapters


def test_self_heal_attempt_reviewer_garbage_no_persist(tmp_path, monkeypatch):
    lope_home = tmp_path / "home_lope"
    lope_home.mkdir()
    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")

    from lope.config import save as cfg_save
    initial = LopeCfg(validators=["claude", "opencode"], primary="claude",
                      timeout=480, parallel=True)
    cfg_save(initial, str(lope_home / "config.json"))

    healer = SelfHealer()
    healer._capture_help = lambda cli: "Usage: codex exec [PROMPT]"

    reviewer = _MockReviewer("claude", "this is not JSON at all")

    adapter = healer.attempt(
        cli_name="codex",
        cli_binary="codex",
        old_argv=["codex", "exec", "--quiet"],
        stderr="unrecognized",
        reviewer=reviewer,
    )

    assert adapter is None
    persisted = load(str(lope_home / "config.json"))
    assert persisted is not None
    assert "codex" not in persisted.learned_adapters


def test_self_heal_attempt_smoke_test_fail_no_persist(tmp_path, monkeypatch):
    lope_home = tmp_path / "home_lope"
    lope_home.mkdir()
    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")

    from lope.config import save as cfg_save
    initial = LopeCfg(validators=["claude", "opencode"], primary="claude",
                      timeout=480, parallel=True)
    cfg_save(initial, str(lope_home / "config.json"))

    healer = SelfHealer()
    healer._capture_help = lambda cli: "Usage: codex"
    healer._smoke_test = lambda cli, proposal: (False, "NO response does not contain OK")

    reviewer = _MockReviewer("claude",
                              _valid_reviewer_response(["codex", "exec", "{prompt}"]))

    adapter = healer.attempt(
        cli_name="codex",
        cli_binary="codex",
        old_argv=["codex", "exec", "--quiet"],
        stderr="unrecognized",
        reviewer=reviewer,
    )

    assert adapter is None
    persisted = load(str(lope_home / "config.json"))
    assert persisted is not None
    assert "codex" not in persisted.learned_adapters


def test_self_heal_attempt_help_capture_fail_no_persist(tmp_path, monkeypatch):
    lope_home = tmp_path / "home_lope"
    lope_home.mkdir()
    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")

    from lope.config import save as cfg_save
    initial = LopeCfg(validators=["claude", "opencode"], primary="claude",
                      timeout=480, parallel=True)
    cfg_save(initial, str(lope_home / "config.json"))

    healer = SelfHealer()
    healer._capture_help = lambda cli: None  # help binary missing / errors
    reviewer = _MockReviewer("claude", _valid_reviewer_response(["x"]))

    adapter = healer.attempt(
        cli_name="codex",
        cli_binary="codex",
        old_argv=["codex", "exec", "--quiet"],
        stderr="unrecognized",
        reviewer=reviewer,
    )

    assert adapter is None
    persisted = load(str(lope_home / "config.json"))
    assert persisted is not None
    assert "codex" not in persisted.learned_adapters


def test_self_heal_attempt_reviewer_exception_returns_none(tmp_path, monkeypatch):
    lope_home = tmp_path / "home_lope"
    lope_home.mkdir()
    monkeypatch.setenv("LOPE_HOME", str(lope_home))
    monkeypatch.setenv("LOPE_SELF_HEAL", "1")

    from lope.config import save as cfg_save
    initial = LopeCfg(validators=["claude"], primary="claude",
                      timeout=480, parallel=True)
    cfg_save(initial, str(lope_home / "config.json"))

    healer = SelfHealer()
    healer._capture_help = lambda cli: "Usage: codex"

    adapter = healer.attempt(
        cli_name="codex",
        cli_binary="codex",
        old_argv=["codex", "exec"],
        stderr="unrecognized",
        reviewer=_RaisingReviewer(),
    )

    assert adapter is None
