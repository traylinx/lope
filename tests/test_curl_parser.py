"""Tests for lope.curl_parser — parse + translate pasted curl commands.

Covers:
  - Tokenization (quoting, line continuation, flag variants)
  - Body-shape detection + {prompt} injection
  - Credential handling (literal refused, ${VAR} trusted, --key-env swaps)
  - Response-path heuristics for OpenAI / Anthropic / Cohere / fallback
  - Unsupported curl forms (-u, -F, @file, -X GET) produce actionable errors
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lope.curl_parser import (
    CurlParseError,
    curl_to_provider_entry,
    parse_curl,
    suggest_env_name,
)


# ─── parse_curl: tokenization ──────────────────────────────────────


class TestParseCurlTokenization:
    def test_basic_post_with_headers_and_data(self):
        curl = (
            'curl https://api.openai.com/v1/chat/completions '
            "-H 'Authorization: Bearer ${OPENAI_API_KEY}' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"gpt-4","messages":[{"role":"user","content":"hi"}]}\''
        )
        parsed = parse_curl(curl)
        assert parsed["url"] == "https://api.openai.com/v1/chat/completions"
        assert parsed["method"] == "POST"
        assert parsed["headers"]["Authorization"] == "Bearer ${OPENAI_API_KEY}"
        assert parsed["headers"]["Content-Type"] == "application/json"
        assert json.loads(parsed["data"])["model"] == "gpt-4"

    def test_line_continuation_is_stripped(self):
        curl = """curl https://api.example.com/v1 \\
            -H 'X-Auth: ${KEY}' \\
            -d '{"prompt":"hi"}'"""
        parsed = parse_curl(curl)
        assert parsed["url"] == "https://api.example.com/v1"
        assert parsed["headers"]["X-Auth"] == "${KEY}"

    def test_explicit_method_preserved_when_post(self):
        curl = 'curl -X POST https://api.example.com/v1 -d \'{"x":1}\''
        parsed = parse_curl(curl)
        assert parsed["method"] == "POST"

    def test_method_is_post_when_data_present_without_X(self):
        curl = 'curl https://api.example.com/v1 -d \'{"x":1}\''
        parsed = parse_curl(curl)
        assert parsed["method"] == "POST"

    def test_data_raw_and_data_variants_all_work(self):
        for flag in ("-d", "--data", "--data-raw", "--data-ascii"):
            curl = f"curl https://api.example.com -H 'A: ${{K}}' {flag} '{{\"prompt\":\"x\"}}'"
            parsed = parse_curl(curl)
            assert parsed["data"] == '{"prompt":"x"}'

    def test_verbose_flags_are_ignored(self):
        curl = (
            'curl -s -S -L --compressed --fail '
            'https://api.example.com/v1 '
            "-H 'X: ${K}' "
            '-d \'{"prompt":"hi"}\''
        )
        parsed = parse_curl(curl)
        assert parsed["url"] == "https://api.example.com/v1"

    def test_single_arg_ignored_flags_consume_their_value(self):
        curl = (
            'curl -o /tmp/out.json --connect-timeout 30 '
            'https://api.example.com/v1 '
            "-H 'X: ${K}' "
            '-d \'{"prompt":"hi"}\''
        )
        parsed = parse_curl(curl)
        assert parsed["url"] == "https://api.example.com/v1"
        # Without value-consumption, "/tmp/out.json" would be parsed as the URL.


# ─── parse_curl: refusal paths ─────────────────────────────────────


class TestParseCurlRefusals:
    def test_empty_string_errors(self):
        with pytest.raises(CurlParseError):
            parse_curl("")

    def test_non_curl_command_errors(self):
        with pytest.raises(CurlParseError, match="expected a curl"):
            parse_curl("wget https://example.com")

    def test_missing_url_errors(self):
        with pytest.raises(CurlParseError, match="no URL"):
            parse_curl("curl -H 'X: y' -d '{}'")

    def test_unsupported_method_errors(self):
        with pytest.raises(CurlParseError, match="only POST"):
            parse_curl('curl -X GET https://api.example.com/v1')

    def test_basic_auth_refused_with_guidance(self):
        with pytest.raises(CurlParseError, match="basic auth"):
            parse_curl('curl -u user:pass https://api.example.com/v1 -d \'{"x":1}\'')

    def test_multipart_form_refused(self):
        with pytest.raises(CurlParseError, match="multipart"):
            parse_curl("curl -F 'file=@foo.png' https://api.example.com/v1")

    def test_data_binary_at_file_refused(self):
        with pytest.raises(CurlParseError, match="not supported"):
            parse_curl("curl https://api.example.com/v1 --data-binary @body.json")

    def test_malformed_header_errors(self):
        with pytest.raises(CurlParseError, match="malformed header"):
            parse_curl("curl https://api.example.com/v1 -H 'no-colon-here' -d '{}'")

    def test_unclosed_quote_errors(self):
        with pytest.raises(CurlParseError, match="unclosed quote"):
            parse_curl("curl https://api.example.com/v1 -d '{unclosed")


# ─── suggest_env_name ──────────────────────────────────────────────


class TestSuggestEnvName:
    def test_openai(self):
        assert suggest_env_name("https://api.openai.com/v1/chat/completions") == "OPENAI_API_KEY"

    def test_anthropic(self):
        assert suggest_env_name("https://api.anthropic.com/v1/messages") == "ANTHROPIC_API_KEY"

    def test_groq(self):
        assert suggest_env_name("https://api.groq.com/openai/v1/chat/completions") == "GROQ_API_KEY"

    def test_private_pod_falls_back(self):
        # 10.42.42.1 has no hostname segments — must still return something.
        assert suggest_env_name("http://10.42.42.1:18080/v1/chat/completions") == "API_KEY"

    def test_handles_no_hostname(self):
        assert suggest_env_name("") == "API_KEY"


# ─── curl_to_provider_entry: body injection ───────────────────────


class TestPromptInjection:
    def _parse_openai(self):
        curl = (
            'curl https://api.openai.com/v1/chat/completions '
            "-H 'Authorization: Bearer ${OPENAI_API_KEY}' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}]}\''
        )
        return parse_curl(curl)

    def test_openai_messages_inject_into_last_user(self):
        parsed = self._parse_openai()
        entry = curl_to_provider_entry("openai", parsed)
        user_msg = entry["body"]["messages"][-1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "{prompt}"

    def test_multi_turn_history_preserves_system_and_assistant(self):
        body = {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "second"},
            ],
        }
        curl = (
            f"curl https://api.example.com/v1 "
            f"-H 'X-API-Key: ${{K}}' "
            f"-H 'Content-Type: application/json' "
            f"-d '{json.dumps(body)}'"
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("x", parsed)
        msgs = entry["body"]["messages"]
        assert msgs[0]["content"] == "Be concise."
        assert msgs[1]["content"] == "first"
        assert msgs[2]["content"] == "ok"
        assert msgs[3]["content"] == "{prompt}"  # last user — swapped

    def test_top_level_prompt_key_is_replaced(self):
        curl = (
            'curl https://api.example.com/v1/completions '
            "-H 'Authorization: Bearer ${K}' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"x","prompt":"hello"}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("x", parsed)
        assert entry["body"]["prompt"] == "{prompt}"

    def test_cohere_message_key_is_replaced(self):
        curl = (
            'curl https://api.cohere.ai/v1/chat '
            "-H 'Authorization: Bearer ${COHERE_API_KEY}' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"command-r","message":"hi"}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("cohere", parsed)
        assert entry["body"]["message"] == "{prompt}"
        assert entry["response_path"] == "text"  # Cohere-specific

    def test_already_templated_body_passes_through(self):
        curl = (
            'curl https://api.example.com/v1 '
            "-H 'X-Auth: ${K}' "
            "-d '{\"prompt\":\"{prompt}\"}'"
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("x", parsed)
        assert entry["body"]["prompt"] == "{prompt}"

    def test_unrecognized_body_shape_errors_unless_user_put_placeholder(self):
        # A weird shape with no messages/prompt/input/message/query/text.
        curl = (
            'curl https://api.example.com/v1 '
            "-H 'X-Auth: ${K}' "
            "-d '{\"foo\":\"bar\"}'"
        )
        parsed = parse_curl(curl)
        with pytest.raises(CurlParseError, match="could not auto-detect"):
            curl_to_provider_entry("x", parsed)

    def test_no_body_errors(self):
        # -H but no -d. parse_curl sets method=GET which is rejected earlier,
        # so pre-build a parsed dict manually to exercise the body check.
        parsed = {
            "url": "https://api.example.com/v1",
            "method": "POST",
            "headers": {"X-Auth": "${K}"},
            "data": None,
        }
        with pytest.raises(CurlParseError, match="no request body"):
            curl_to_provider_entry("x", parsed)


# ─── curl_to_provider_entry: credentials ──────────────────────────


class TestCredentialHandling:
    def _make(self, auth_val):
        curl = (
            'curl https://api.openai.com/v1/chat/completions '
            f"-H 'Authorization: {auth_val}' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"m","messages":[{"role":"user","content":"hi"}]}\''
        )
        return parse_curl(curl)

    def test_templated_auth_passes_through(self):
        parsed = self._make("Bearer ${OPENAI_API_KEY}")
        entry = curl_to_provider_entry("openai", parsed)
        assert entry["headers"]["Authorization"] == "Bearer ${OPENAI_API_KEY}"

    def test_literal_bearer_refused_without_key_env(self):
        parsed = self._make("Bearer sk-proj-ABCDEFG1234567890")
        with pytest.raises(CurlParseError, match="literal credential"):
            curl_to_provider_entry("openai", parsed)

    def test_literal_key_swapped_when_key_env_provided(self):
        parsed = self._make("Bearer sk-proj-ABCDEFG1234567890")
        entry = curl_to_provider_entry("openai", parsed, key_env="OPENAI_API_KEY")
        assert entry["headers"]["Authorization"] == "Bearer ${OPENAI_API_KEY}"

    def test_literal_x_api_key_refused_without_env(self):
        curl = (
            'curl https://api.example.com/v1/chat '
            "-H 'X-API-Key: raw-key-12345' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"m","messages":[{"role":"user","content":"hi"}]}\''
        )
        parsed = parse_curl(curl)
        with pytest.raises(CurlParseError, match="literal credential"):
            curl_to_provider_entry("x", parsed)

    def test_literal_x_api_key_swapped_with_env(self):
        curl = (
            'curl https://api.example.com/v1/chat '
            "-H 'X-API-Key: raw-key-12345' "
            '-d \'{"model":"m","messages":[{"role":"user","content":"hi"}]}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("x", parsed, key_env="MY_KEY")
        # X-API-Key has no scheme prefix → bare ${MY_KEY}
        assert entry["headers"]["X-API-Key"] == "${MY_KEY}"

    def test_error_message_suggests_hostname_derived_env_name(self):
        parsed = self._make("Bearer sk-proj-raw")
        with pytest.raises(CurlParseError, match="OPENAI_API_KEY"):
            curl_to_provider_entry("openai", parsed)


# ─── curl_to_provider_entry: response_path inference ──────────────


class TestResponsePathInference:
    def test_openai_style(self):
        curl = (
            'curl https://api.openai.com/v1/chat/completions '
            "-H 'Authorization: Bearer ${K}' "
            '-d \'{"model":"m","messages":[{"role":"user","content":"hi"}]}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("openai", parsed)
        assert entry["response_path"] == "choices.0.message.content"

    def test_anthropic_by_hostname(self):
        curl = (
            'curl https://api.anthropic.com/v1/messages '
            "-H 'x-api-key: ${ANTHROPIC_API_KEY}' "
            "-H 'anthropic-version: 2023-06-01' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"claude-sonnet-4-5","max_tokens":4096,'
            '"messages":[{"role":"user","content":"hi"}]}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("anthropic", parsed)
        assert entry["response_path"] == "content.0.text"

    def test_anthropic_by_version_header(self):
        # Edge: URL doesn't contain "anthropic" (custom proxy) but the
        # anthropic-version header is there.
        curl = (
            'curl https://proxy.example.com/anthro '
            "-H 'x-api-key: ${K}' "
            "-H 'anthropic-version: 2023-06-01' "
            '-d \'{"model":"m","messages":[{"role":"user","content":"hi"}]}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("x", parsed)
        assert entry["response_path"] == "content.0.text"

    def test_cohere_by_hostname(self):
        curl = (
            'curl https://api.cohere.ai/v1/chat '
            "-H 'Authorization: Bearer ${COHERE_API_KEY}' "
            '-d \'{"model":"command-r","message":"hi"}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("cohere", parsed)
        assert entry["response_path"] == "text"

    def test_user_override_wins(self):
        curl = (
            'curl https://api.openai.com/v1/chat/completions '
            "-H 'Authorization: Bearer ${K}' "
            '-d \'{"model":"m","messages":[{"role":"user","content":"hi"}]}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry(
            "openai", parsed, response_path="custom.path.here"
        )
        assert entry["response_path"] == "custom.path.here"


# ─── curl_to_provider_entry: integration output shape ─────────────


class TestEntryShape:
    def test_entry_passes_validate_provider_config(self):
        """The entry must be accepted by the same validator used for
        hand-edited provider JSON — otherwise it can't be saved."""
        from lope.generic_validators import _validate_provider_config

        curl = (
            'curl https://api.openai.com/v1/chat/completions '
            "-H 'Authorization: Bearer ${OPENAI_API_KEY}' "
            "-H 'Content-Type: application/json' "
            '-d \'{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry("openai", parsed)
        _validate_provider_config(entry)  # raises ConfigError if malformed

    def test_wrap_and_timeout_propagate(self):
        curl = (
            'curl https://api.example.com/v1 '
            "-H 'X-API-Key: ${K}' "
            '-d \'{"prompt":"x"}\''
        )
        parsed = parse_curl(curl)
        entry = curl_to_provider_entry(
            "x", parsed, wrap="Be terse: {prompt}", timeout=120
        )
        assert entry["prompt_wrapper"] == "Be terse: {prompt}"
        assert entry["timeout"] == 120
