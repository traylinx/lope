"""
Generic validators — infinitely extensible via config.

Two classes cover 100% of real cases:

- GenericSubprocessValidator: runs any binary with prompt via argv or stdin
- GenericHttpValidator: POSTs any HTTP endpoint with JSON body, parses response

Both read provider definitions from ~/.lope/config.json under the "providers"
key. No Python needed to add new AI backends — just JSON.

Security:
- subprocess runs with shell=False, command is always a list[str]
- {prompt} substitutes as list element, never string-interpolated
- ${VAR} env substitution allowed ONLY in headers/body, never in command/url
- Shell type deliberately not supported (injection risk)

Example config:

    {
        "providers": [
            {
                "name": "ollama-qwen",
                "type": "subprocess",
                "command": ["ollama", "run", "qwen3:8b", "{prompt}"]
            },
            {
                "name": "openai-gpt4",
                "type": "http",
                "url": "https://api.openai.com/v1/chat/completions",
                "headers": {
                    "Authorization": "Bearer ${OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                "body": {
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "{prompt}"}]
                },
                "response_path": "choices.0.message.content"
            }
        ]
    }
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import time
import urllib.request
from typing import Any, Dict, List, Optional

from .models import ValidatorResult
from .validators import Validator, parse_opencode_verdict


class ConfigError(ValueError):
    """Raised when a provider config entry is invalid."""


def _validate_provider_config(entry: Dict[str, Any]) -> None:
    """Reject malformed configs at load time, not runtime."""
    if not isinstance(entry, dict):
        raise ConfigError(f"provider entry must be a dict, got {type(entry).__name__}")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("provider missing 'name' field")
    ptype = entry.get("type")
    if ptype not in ("subprocess", "http"):
        raise ConfigError(f"provider {name!r} type must be 'subprocess' or 'http', got {ptype!r}")
    if ptype == "subprocess":
        cmd = entry.get("command")
        if not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd):
            raise ConfigError(f"provider {name!r} command must be a list of strings")
        if "${" in " ".join(cmd):
            raise ConfigError(
                f"provider {name!r}: ${{VAR}} not allowed in command (API keys in argv are visible via ps)"
            )
    elif ptype == "http":
        url = entry.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ConfigError(f"provider {name!r} url must be http:// or https://")
        if "${" in url:
            raise ConfigError(
                f"provider {name!r}: ${{VAR}} not allowed in url (leaks to server logs)"
            )


def _expand_env_str(s: str) -> str:
    """Replace ${VAR} with os.environ.get(VAR, ''). Shell-safe — no eval."""
    import re
    return re.sub(
        r"\$\{([A-Z_][A-Z0-9_]*)\}",
        lambda m: os.environ.get(m.group(1), ""),
        s,
    )


def _expand_env_dict(d: Any) -> Any:
    """Recursively expand ${VAR} in string values of a dict/list structure."""
    if isinstance(d, str):
        return _expand_env_str(d)
    if isinstance(d, dict):
        return {k: _expand_env_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_expand_env_dict(v) for v in d]
    return d


def _substitute_prompt(obj: Any, prompt: str, max_tokens: Optional[int] = None) -> Any:
    """Replace {prompt} and {max_tokens} placeholders.

    If max_tokens is provided and {max_tokens} appears in the body, it is replaced
    with the integer value. Otherwise {max_tokens} is left as-is (user may have set
    it explicitly via curl --max-tokens / --body-json).
    """
    if isinstance(obj, str):
        result = obj.replace("{prompt}", prompt)
        if max_tokens is not None and "{max_tokens}" in result:
            result = result.replace("{max_tokens}", str(max_tokens))
        return result
    if isinstance(obj, dict):
        return {k: _substitute_prompt(v, prompt, max_tokens) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_prompt(v, prompt, max_tokens) for v in obj]
    return obj


def _extract_response(data: Any, path: Optional[str]) -> str:
    """Walk a dot-path into a JSON response. `choices.0.message.content` style."""
    if path is None:
        return str(data) if not isinstance(data, str) else data
    cur = data
    for segment in path.split("."):
        if segment.isdigit() and isinstance(cur, list):
            idx = int(segment)
            if idx >= len(cur):
                return ""
            cur = cur[idx]
        elif isinstance(cur, dict):
            cur = cur.get(segment, "")
        else:
            return ""
    return str(cur) if cur is not None else ""


class GenericSubprocessValidator(Validator):
    """Runs any binary with prompt via argv substitution or stdin."""

    def __init__(self, config: Dict[str, Any]):
        _validate_provider_config(config)
        self._name = config["name"]
        self._command: List[str] = list(config["command"])
        self._stdin: bool = bool(config.get("stdin", False))
        self._prompt_wrapper: Optional[str] = config.get("prompt_wrapper")
        self._timeout_override: Optional[int] = config.get("timeout")

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        import shutil
        if not self._command:
            return False
        return shutil.which(self._command[0]) is not None

    def _run(self, prompt: str, timeout: int) -> tuple[int, str, str, float]:
        """Execute the subprocess; return (returncode, stdout, stderr, duration).

        Shared between validate() and generate(). Handles argv-substitution
        vs. stdin modes, prompt wrapper, timeout override, and the common
        error-to-infra-error translation at the caller site.
        """
        started = time.time()
        if self._prompt_wrapper:
            prompt = self._prompt_wrapper.format(prompt=prompt)
        if self._stdin:
            cmd = list(self._command)
            stdin_data = prompt
        else:
            cmd = [arg.replace("{prompt}", prompt) for arg in self._command]
            stdin_data = None
        effective_timeout = self._timeout_override or timeout
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            shell=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or "", time.time() - started

    def validate(self, prompt: str, timeout: int = 480) -> ValidatorResult:
        try:
            rc, stdout, stderr, duration = self._run(prompt, timeout)
        except subprocess.TimeoutExpired:
            return self._infra_error(
                f"timeout after {self._timeout_override or timeout}s", 0.0
            )
        except FileNotFoundError:
            return self._infra_error(f"binary not found: {self._command[0]}", 0.0)
        except Exception as e:
            return self._infra_error(f"subprocess error: {e}", 0.0)

        if rc != 0:
            return self._infra_error(
                f"exit {rc}: {(stderr or '')[:200]}", duration
            )

        verdict = parse_opencode_verdict(
            stdout, validator_name=self._name, fallback_duration=duration
        )
        return ValidatorResult(
            validator_name=self._name,
            verdict=verdict,
            raw_response=stdout,
            error="",
        )

    def generate(self, prompt: str, timeout: int = 480) -> str:
        """Raw CLI invocation — no VERDICT parsing, returns stdout text.

        Used by the `ask` / `review` / `vote` / `compare` / `pipe` verbs
        where we want the model's natural response, not a validation
        verdict. Raises RuntimeError on infra failure so callers can
        per-validator-isolate errors (see `_fanout_generate` in cli.py).
        """
        try:
            rc, stdout, stderr, _duration = self._run(prompt, timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"{self._name} timed out after {self._timeout_override or timeout}s"
            )
        except FileNotFoundError:
            raise RuntimeError(f"{self._name} binary not found: {self._command[0]}")
        except Exception as e:
            raise RuntimeError(f"{self._name} subprocess error: {e}")

        if rc != 0:
            raise RuntimeError(
                f"{self._name} exited {rc}: {(stderr or '')[:300]}"
            )
        if not stdout.strip():
            raise RuntimeError(f"{self._name} returned empty output")
        return stdout

    def _infra_error(self, msg: str, duration: float) -> ValidatorResult:
        from .models import PhaseVerdict, VerdictStatus
        return ValidatorResult(
            validator_name=self._name,
            verdict=PhaseVerdict(
                status=VerdictStatus.INFRA_ERROR,
                rationale=msg,
                duration_seconds=duration,
                validator_name=self._name,
            ),
            raw_response="",
            error=msg,
        )


class GenericHttpValidator(Validator):
    """POSTs any HTTP endpoint with JSON body, parses response via dot-path."""

    def __init__(self, config: Dict[str, Any]):
        _validate_provider_config(config)
        self._name = config["name"]
        self._url: str = config["url"]
        self._headers: Dict[str, str] = dict(config.get("headers", {}))
        self._body: Any = config.get("body", {})
        self._response_path: Optional[str] = config.get("response_path")
        self._prompt_wrapper: Optional[str] = config.get("prompt_wrapper")
        self._timeout_override: Optional[int] = config.get("timeout")
        self._max_tokens: Optional[int] = config.get("max_tokens")

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        # HTTP validators are always available (assume network works)
        return True

    def validate(self, prompt: str, timeout: int = 480) -> ValidatorResult:
        started = time.time()
        if self._prompt_wrapper:
            prompt = self._prompt_wrapper.format(prompt=prompt)

        # Expand ${VAR} then substitute {prompt}
        headers = _expand_env_dict(self._headers)
        body = _substitute_prompt(_expand_env_dict(self._body), prompt, self._max_tokens)

        effective_timeout = self._timeout_override or timeout
        try:
            payload = _json.dumps(body).encode("utf-8")
            req = urllib.request.Request(self._url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return self._infra_error(f"http error: {e}", time.time() - started)

        duration = time.time() - started
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            # Not JSON — treat as plain text
            data = raw

        text = _extract_response(data, self._response_path)
        verdict = parse_opencode_verdict(
            text, validator_name=self._name, fallback_duration=duration
        )
        return ValidatorResult(
            validator_name=self._name,
            verdict=verdict,
            raw_response=text,
            error="",
        )

    def _infra_error(self, msg: str, duration: float) -> ValidatorResult:
        from .models import PhaseVerdict, VerdictStatus
        return ValidatorResult(
            validator_name=self._name,
            verdict=PhaseVerdict(
                status=VerdictStatus.INFRA_ERROR,
                rationale=msg,
                duration_seconds=duration,
                validator_name=self._name,
            ),
            raw_response="",
            error=msg,
        )


def build_provider(config: Dict[str, Any]) -> Validator:
    """Instantiate a generic validator from a provider config entry."""
    _validate_provider_config(config)
    ptype = config["type"]
    if ptype == "subprocess":
        return GenericSubprocessValidator(config)
    if ptype == "http":
        return GenericHttpValidator(config)
    raise ConfigError(f"unknown provider type: {ptype}")
