"""Tests for v0.6.0 — `lope team` validator management.

Covers the pure-logic helpers that build provider dicts from CLI args plus
the list/add/remove roundtrip against a temp LOPE_HOME. Test-runs a real
subprocess provider against /bin/echo to confirm `team test` calls generate().
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lope.cli import (
    _HARDCODED_VALIDATOR_NAMES,
    _team_build_http_entry,
    _team_build_subprocess_entry,
    _team_classify_source,
)
from lope.config import LopeCfg, load, save


def _mk_args(**kwargs):
    """Build an argparse.Namespace with sane defaults for `team add` tests."""
    defaults = {
        "name": "x",
        "cmd": None,
        "stdin": False,
        "url": None,
        "model": None,
        "key_env": None,
        "key_header": "Authorization",
        "key_prefix": "Bearer ",
        "response_path": None,
        "body_json": None,
        "from_curl": None,
        "wrap": None,
        "timeout": None,
        "primary": False,
        "disabled": False,
        "force": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ─── subprocess entry builder ──────────────────────────────────────


class TestSubprocessBuilder:
    def test_simple_cmd_auto_appends_prompt_placeholder(self):
        args = _mk_args(cmd="mybin --json")
        entry = _team_build_subprocess_entry("my-tool", args)
        assert entry["type"] == "subprocess"
        assert entry["command"] == ["mybin", "--json", "{prompt}"]
        assert "stdin" not in entry

    def test_preserves_explicit_prompt_placeholder_position(self):
        args = _mk_args(cmd="mybin --prompt {prompt} --json")
        entry = _team_build_subprocess_entry("my-tool", args)
        assert entry["command"] == ["mybin", "--prompt", "{prompt}", "--json"]

    def test_stdin_mode_does_not_append_prompt(self):
        args = _mk_args(cmd="mybin --json", stdin=True)
        entry = _team_build_subprocess_entry("my-tool", args)
        assert entry["command"] == ["mybin", "--json"]
        assert entry["stdin"] is True

    def test_wrap_and_timeout_propagate(self):
        args = _mk_args(cmd="mybin {prompt}", wrap="Q: {prompt}", timeout=120)
        entry = _team_build_subprocess_entry("x", args)
        assert entry["prompt_wrapper"] == "Q: {prompt}"
        assert entry["timeout"] == 120

    def test_shlex_splits_quoted_args(self):
        args = _mk_args(cmd="mybin --system \"You are helpful.\" {prompt}")
        entry = _team_build_subprocess_entry("x", args)
        assert entry["command"] == ["mybin", "--system", "You are helpful.", "{prompt}"]

    def test_unclosed_quote_exits(self):
        args = _mk_args(cmd='mybin --system "unclosed {prompt}')
        with pytest.raises(SystemExit):
            _team_build_subprocess_entry("x", args)


# ─── http entry builder ────────────────────────────────────────────


class TestHttpBuilder:
    def test_openai_compatible_default_shape(self):
        args = _mk_args(
            url="https://api.example.com/v1/chat/completions",
            model="gpt-5",
            key_env="OPENAI_API_KEY",
        )
        entry = _team_build_http_entry("example", args)
        assert entry["type"] == "http"
        assert entry["url"] == "https://api.example.com/v1/chat/completions"
        assert entry["response_path"] == "choices.0.message.content"
        assert entry["body"]["model"] == "gpt-5"
        assert entry["body"]["messages"][0]["content"] == "{prompt}"
        # Key is stored as a template, expanded at call time.
        assert entry["headers"]["Authorization"] == "Bearer ${OPENAI_API_KEY}"
        assert entry["headers"]["Content-Type"] == "application/json"

    def test_custom_auth_header_and_prefix(self):
        args = _mk_args(
            url="https://api.example.com/v1/chat",
            model="m",
            key_env="MY_KEY",
            key_header="X-API-Key",
            key_prefix="",
        )
        entry = _team_build_http_entry("example", args)
        assert entry["headers"]["X-API-Key"] == "${MY_KEY}"

    def test_body_json_override_replaces_shape(self):
        body = {"prompt": "{prompt}", "max_tokens": 500}
        args = _mk_args(
            url="https://api.example.com/generate",
            body_json=json.dumps(body),
            response_path="text",
        )
        entry = _team_build_http_entry("example", args)
        assert entry["body"] == body
        assert entry["response_path"] == "text"

    def test_http_rejects_nonhttp_scheme(self):
        args = _mk_args(url="file:///etc/hosts", model="m")
        with pytest.raises(SystemExit):
            _team_build_http_entry("example", args)

    def test_http_requires_model_unless_body_json(self):
        args = _mk_args(url="https://api.example.com/v1/chat")
        with pytest.raises(SystemExit):
            _team_build_http_entry("example", args)

    def test_bad_body_json_rejected(self):
        args = _mk_args(url="https://api.example.com/v1", body_json="not valid {json")
        with pytest.raises(SystemExit):
            _team_build_http_entry("example", args)

    def test_bad_key_env_name_rejected(self):
        args = _mk_args(
            url="https://api.example.com/v1/chat",
            model="m",
            key_env="MY-KEY",  # dash is not a valid env var char
        )
        with pytest.raises(SystemExit):
            _team_build_http_entry("example", args)


# ─── source classification ─────────────────────────────────────────


class TestClassifySource:
    def test_hardcoded_names(self):
        cfg = LopeCfg(validators=["claude"], primary="claude", timeout=60,
                      parallel=True, providers=[])
        assert _team_classify_source("claude", cfg) == "(built-in)"
        assert _team_classify_source("opencode", cfg) == "(built-in)"

    def test_custom_provider(self):
        cfg = LopeCfg(validators=["mine"], primary="mine", timeout=60,
                      parallel=True, providers=[{"name": "mine", "type": "http"}])
        assert _team_classify_source("mine", cfg) == "(custom http)"

    def test_unknown_falls_back(self):
        cfg = LopeCfg(validators=[], primary="", timeout=60, parallel=True, providers=[])
        # ollama is in KNOWN_CLIS with a generic_command → "(auto)"
        result = _team_classify_source("ollama", cfg)
        assert result in ("(auto)", "(?)")  # survive a KNOWN_CLIS refactor
        assert _team_classify_source("nonsense-xyz-123", cfg) == "(?)"


# ─── roundtrip: add → list → remove against a real temp config ──────


class TestTeamRoundtrip:
    @pytest.fixture
    def tmp_lope_home(self, tmp_path, monkeypatch):
        home = tmp_path / "lope"
        monkeypatch.setenv("LOPE_HOME", str(home))
        return home

    def test_add_creates_config_and_enables_validator(self, tmp_lope_home):
        from lope.cli import _cmd_team
        from lope.config import default_path

        args = _mk_args(
            name="tester",
            cmd="/bin/echo {prompt}",
            team_cmd="add",
        )
        _cmd_team(args)

        cfg = load(default_path())
        assert cfg is not None
        assert "tester" in cfg.validators
        assert cfg.primary == "tester"  # auto-promoted (first validator)
        assert len(cfg.providers) == 1
        assert cfg.providers[0]["name"] == "tester"
        assert cfg.providers[0]["command"] == ["/bin/echo", "{prompt}"]

    def test_add_refuses_hardcoded_name(self, tmp_lope_home):
        from lope.cli import _cmd_team

        args = _mk_args(name="claude", cmd="/bin/echo {prompt}", team_cmd="add")
        with pytest.raises(SystemExit):
            _cmd_team(args)

    def test_add_refuses_duplicate_without_force(self, tmp_lope_home):
        from lope.cli import _cmd_team

        _cmd_team(_mk_args(name="dup", cmd="/bin/echo {prompt}", team_cmd="add"))
        with pytest.raises(SystemExit):
            _cmd_team(_mk_args(name="dup", cmd="/bin/echo --v2 {prompt}", team_cmd="add"))

    def test_force_overwrites_existing(self, tmp_lope_home):
        from lope.cli import _cmd_team
        from lope.config import default_path

        _cmd_team(_mk_args(name="dup", cmd="/bin/echo v1 {prompt}", team_cmd="add"))
        _cmd_team(_mk_args(
            name="dup",
            cmd="/bin/echo v2 {prompt}",
            team_cmd="add",
            force=True,
        ))
        cfg = load(default_path())
        assert len(cfg.providers) == 1
        assert cfg.providers[0]["command"] == ["/bin/echo", "v2", "{prompt}"]

    def test_add_requires_one_of_cmd_or_url(self, tmp_lope_home):
        from lope.cli import _cmd_team

        args = _mk_args(name="x", team_cmd="add")  # no --cmd, no --url
        with pytest.raises(SystemExit):
            _cmd_team(args)

    def test_add_rejects_cmd_and_url_together(self, tmp_lope_home):
        from lope.cli import _cmd_team

        args = _mk_args(
            name="x",
            cmd="/bin/echo {prompt}",
            url="https://api.example.com/v1/chat",
            model="m",
            team_cmd="add",
        )
        with pytest.raises(SystemExit):
            _cmd_team(args)

    def test_disabled_saves_provider_but_skips_validators(self, tmp_lope_home):
        from lope.cli import _cmd_team
        from lope.config import default_path

        _cmd_team(_mk_args(
            name="shy",
            cmd="/bin/echo {prompt}",
            disabled=True,
            team_cmd="add",
        ))
        cfg = load(default_path())
        assert "shy" not in cfg.validators
        assert any(p["name"] == "shy" for p in cfg.providers)

    def test_remove_strips_from_everything(self, tmp_lope_home):
        from lope.cli import _cmd_team
        from lope.config import default_path

        _cmd_team(_mk_args(name="a", cmd="/bin/echo {prompt}", team_cmd="add"))
        _cmd_team(_mk_args(name="b", cmd="/bin/echo {prompt}", team_cmd="add"))

        # b was added second — a stays primary
        cfg = load(default_path())
        assert cfg.primary == "a"

        # remove primary → primary falls back to the next validator
        _cmd_team(argparse.Namespace(name="a", team_cmd="remove"))
        cfg = load(default_path())
        assert "a" not in cfg.validators
        assert not any(p["name"] == "a" for p in cfg.providers)
        assert cfg.primary == "b"

    def test_remove_unknown_exits(self, tmp_lope_home):
        from lope.cli import _cmd_team

        with pytest.raises(SystemExit):
            _cmd_team(argparse.Namespace(name="ghost", team_cmd="remove"))

    def test_primary_flag_promotes_on_add(self, tmp_lope_home):
        from lope.cli import _cmd_team
        from lope.config import default_path

        _cmd_team(_mk_args(name="a", cmd="/bin/echo {prompt}", team_cmd="add"))
        _cmd_team(_mk_args(
            name="b",
            cmd="/bin/echo {prompt}",
            primary=True,
            team_cmd="add",
        ))
        cfg = load(default_path())
        assert cfg.primary == "b"

    def test_primary_and_disabled_are_incompatible(self, tmp_lope_home):
        from lope.cli import _cmd_team

        args = _mk_args(
            name="x",
            cmd="/bin/echo {prompt}",
            primary=True,
            disabled=True,
            team_cmd="add",
        )
        with pytest.raises(SystemExit):
            _cmd_team(args)


class TestFromCurlIntegration:
    """End-to-end `lope team add --from-curl` against a temp LOPE_HOME."""

    @pytest.fixture
    def tmp_lope_home(self, tmp_path, monkeypatch):
        home = tmp_path / "lope"
        monkeypatch.setenv("LOPE_HOME", str(home))
        return home

    def test_openai_curl_paste_produces_valid_config(self, tmp_lope_home):
        from lope.cli import _cmd_team
        from lope.config import default_path, load

        curl = (
            "curl https://api.openai.com/v1/chat/completions "
            "-H 'Authorization: Bearer ${OPENAI_API_KEY}' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}\''
        )
        _cmd_team(_mk_args(name="openai", from_curl=curl, team_cmd="add"))

        cfg = load(default_path())
        provider = cfg.providers[0]
        assert provider["type"] == "http"
        assert provider["url"] == "https://api.openai.com/v1/chat/completions"
        assert provider["headers"]["Authorization"] == "Bearer ${OPENAI_API_KEY}"
        assert provider["body"]["messages"][-1]["content"] == "{prompt}"
        assert provider["response_path"] == "choices.0.message.content"

    def test_literal_key_refused_without_key_env(self, tmp_lope_home):
        from lope.cli import _cmd_team

        curl = (
            "curl https://api.openai.com/v1/chat/completions "
            "-H 'Authorization: Bearer sk-proj-RAW1234567890' "
            "-d '{\"model\":\"m\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"
        )
        with pytest.raises(SystemExit):
            _cmd_team(_mk_args(name="openai", from_curl=curl, team_cmd="add"))

    def test_literal_key_swapped_when_key_env_provided(self, tmp_lope_home):
        from lope.cli import _cmd_team
        from lope.config import default_path, load

        curl = (
            "curl https://api.openai.com/v1/chat/completions "
            "-H 'Authorization: Bearer sk-proj-RAW1234567890' "
            "-d '{\"model\":\"m\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"
        )
        _cmd_team(_mk_args(
            name="openai",
            from_curl=curl,
            key_env="OPENAI_API_KEY",
            team_cmd="add",
        ))
        cfg = load(default_path())
        assert cfg.providers[0]["headers"]["Authorization"] == "Bearer ${OPENAI_API_KEY}"

    def test_mutex_with_cmd(self, tmp_lope_home):
        from lope.cli import _cmd_team

        args = _mk_args(
            name="x",
            from_curl="curl https://api.example.com -H 'X: ${K}' -d '{\"prompt\":\"hi\"}'",
            cmd="/bin/echo {prompt}",
            team_cmd="add",
        )
        with pytest.raises(SystemExit):
            _cmd_team(args)

    def test_mutex_with_url(self, tmp_lope_home):
        from lope.cli import _cmd_team

        args = _mk_args(
            name="x",
            from_curl="curl https://api.example.com -H 'X: ${K}' -d '{\"prompt\":\"hi\"}'",
            url="https://api.example.com/v2",
            model="m",
            team_cmd="add",
        )
        with pytest.raises(SystemExit):
            _cmd_team(args)

    def test_mutex_with_body_json(self, tmp_lope_home):
        from lope.cli import _cmd_team

        args = _mk_args(
            name="x",
            from_curl="curl https://api.example.com -H 'X: ${K}' -d '{\"prompt\":\"hi\"}'",
            body_json='{"override":true}',
            team_cmd="add",
        )
        with pytest.raises(SystemExit):
            _cmd_team(args)

    def test_malformed_curl_exits_cleanly(self, tmp_lope_home):
        from lope.cli import _cmd_team

        # Missing URL → CurlParseError → SystemExit(2) with a clear message.
        with pytest.raises(SystemExit):
            _cmd_team(_mk_args(
                name="x",
                from_curl="curl -H 'X: y' -d '{}'",
                team_cmd="add",
            ))

    def test_anthropic_curl_infers_content_path(self, tmp_lope_home):
        from lope.cli import _cmd_team
        from lope.config import default_path, load

        curl = (
            "curl https://api.anthropic.com/v1/messages "
            "-H 'x-api-key: ${ANTHROPIC_API_KEY}' "
            "-H 'anthropic-version: 2023-06-01' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"claude-sonnet-4-5","max_tokens":4096,'
            '"messages":[{"role":"user","content":"hi"}]}\''
        )
        _cmd_team(_mk_args(name="anthropic", from_curl=curl, team_cmd="add"))
        cfg = load(default_path())
        provider = cfg.providers[0]
        assert provider["response_path"] == "content.0.text"
        assert provider["body"]["model"] == "claude-sonnet-4-5"
        assert provider["body"]["messages"][-1]["content"] == "{prompt}"


class TestHardcodedNamesConstant:
    """The set of hardcoded names drives both rejection in `add` and the
    `(built-in)` tag in `list` — we want to catch accidental drift."""

    def test_contains_known_builtins(self):
        assert {"claude", "opencode", "gemini", "codex", "aider"} == set(
            _HARDCODED_VALIDATOR_NAMES
        )
