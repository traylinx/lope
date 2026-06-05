from __future__ import annotations

from lope.cli import _format_drafter_failure_diagnostic, _validator_command_shape
from lope.generic_validators import GenericSubprocessValidator
from lope.validators import OpencodeValidator


SECRET_PROMPT = "sprint body with SECRET_TOKEN_123"


def test_drafter_timeout_diagnostic_has_prompt_size_no_prompt_body():
    validator = GenericSubprocessValidator(
        {
            "name": "pi",
            "type": "subprocess",
            "command": ["pi", "--no-session", "--offline", "--no-tools", "-p", "{prompt}"],
        }
    )

    diag = _format_drafter_failure_diagnostic(
        validator,
        "pi timed out after 120s",
        timeout=120,
        prompt=SECRET_PROMPT,
    )

    assert "pi: pi timed out after 120s" in diag
    assert "invocation: custom subprocess provider" in diag
    assert "effective timeout: 120s" in diag
    assert "prompt:" in diag and "bytes" in diag and "lines" in diag
    assert "command: pi --no-session --offline --no-tools -p {prompt}" in diag
    assert "raw pi binary directly, not shell aliases" in diag
    assert "SECRET_TOKEN_123" not in diag
    assert SECRET_PROMPT not in diag


def test_opencode_diagnostic_includes_default_model_hint_without_prompt_body(monkeypatch):
    monkeypatch.delenv("LOPE_OPENCODE_ARGS", raising=False)
    validator = OpencodeValidator(binary="opencode")

    diag = _format_drafter_failure_diagnostic(
        validator,
        "opencode run timed out after 120s",
        timeout=120,
        prompt=SECRET_PROMPT,
    )

    assert "built-in OpencodeValidator" in diag
    assert "command: opencode run --pure --model myprovider/ail-compound --format json <prompt>" in diag
    assert "OpenCode default is `opencode run --pure --model myprovider/ail-compound --format json`" in diag
    assert "LOPE_OPENCODE_ARGS" in diag
    assert "SECRET_TOKEN_123" not in diag


def test_opencode_command_shape_mentions_override(monkeypatch):
    monkeypatch.setenv("LOPE_OPENCODE_ARGS", "--pure --model custom/model")
    validator = OpencodeValidator(binary="opencode")

    shape = _validator_command_shape(validator)

    assert shape == "opencode run $LOPE_OPENCODE_ARGS --format json <prompt>"
