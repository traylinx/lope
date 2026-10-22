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
import base64
import os
import subprocess
import sys
import time
import tempfile
import warnings
from contextlib import contextmanager, nullcontext
from typing import Any, Dict, List, Optional

from .models import ValidatorResult
from .diagnostics import diagnostic_suffix, redact_diagnostic
from .runtime import DEFAULT_MODEL_CALL_TIMEOUT_SECONDS
from .validators import Validator, parse_opencode_verdict
from .provider_errors import ProviderToolPermissionError, tool_permission_denial


class ConfigError(ValueError):
    """Raised when a provider config entry is invalid."""


@contextmanager
def _review_directory():
    directory = tempfile.TemporaryDirectory(prefix="lope-review-")
    try:
        yield directory.name
    finally:
        try:
            directory.cleanup()
        except OSError:
            # Preserve the provider result/original exception, but never hide
            # a directory left behind. No provider payload in the warning.
            warnings.warn(f"Lope review directory cleanup failed: {directory.name}", RuntimeWarning)


def respect_provider_timeout() -> bool:
    """True when a provider's configured timeout outranks the call ceiling.

    Off by default: the stricter-wins rule below is deliberate and protects
    bounded probes like `team test --timeout 10`. This escape hatch exists for
    the opposite case — a provider configured slow *on purpose* (a
    high-reasoning-effort model) being clamped by a generic ceiling that some
    caller guessed. Set by `--respect-provider-timeout`.
    """

    return os.environ.get("LOPE_RESPECT_PROVIDER_TIMEOUT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _effective_timeout(provider_timeout: Optional[int], call_timeout: Optional[int]) -> Optional[int]:
    """Return the timeout Lope should actually enforce for one provider call.

    Provider configs may set a timeout because some CLIs need a shorter safety
    cap than the global Lope default. That provider value must never silently
    extend an explicit per-call timeout (`lope ask --timeout 30`, `team test
    --timeout 10`, etc.). Use the stricter value when both are present.

    The clamp is reported before launch by `latency.fit_warnings`, and can be
    inverted per-invocation with `--respect-provider-timeout` when the provider
    is the one that knows better.
    """
    if provider_timeout is None:
        return call_timeout
    if call_timeout is None:
        return provider_timeout
    if respect_provider_timeout():
        return provider_timeout
    return min(provider_timeout, call_timeout)


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
        review_cmd = entry.get("review_command")
        if review_cmd is not None:
            if (not isinstance(review_cmd, list) or not review_cmd
                    or not all(isinstance(c, str) for c in review_cmd)
                    or not review_cmd[0].strip()):
                raise ConfigError(f"provider {name!r} review_command must be a nonempty argv list")
            if "${" in " ".join(review_cmd):
                raise ConfigError(f"provider {name!r}: ${{VAR}} not allowed in review_command")
        if entry.get("review_workdir", "caller") not in ("caller", "isolated"):
            raise ConfigError(f"provider {name!r} review_workdir must be caller or isolated")
        if "review_stdin" in entry and not isinstance(entry["review_stdin"], bool):
            raise ConfigError(f"provider {name!r} review_stdin must be a boolean")
        selected_review = review_cmd if review_cmd is not None else cmd
        has_review_profile = any(key in entry for key in ("review_command", "review_stdin", "review_workdir"))
        if (has_review_profile and not entry.get("review_stdin", entry.get("stdin", False))
                and not any("{prompt}" in arg for arg in selected_review)):
            raise ConfigError(f"provider {name!r}: review profile needs stdin or a {{prompt}} argument")
        if (entry.get("review_workdir") == "isolated" and selected_review
                and os.path.dirname(selected_review[0])
                and not os.path.isabs(selected_review[0])):
            raise ConfigError(
                f"provider {name!r}: isolated review requires an absolute executable or PATH name"
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
        self._review_command = (
            list(config["review_command"]) if config.get("review_command") is not None else None
        )
        self._review_workdir = config.get("review_workdir", "caller")
        self._review_stdin = config.get("review_stdin", self._stdin)
        self._has_review_profile = any(key in config for key in ("review_command", "review_stdin", "review_workdir"))

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        import shutil
        if not self._command:
            return False
        return shutil.which(self._command[0]) is not None

    def execution_profile(self, mode: str) -> Dict[str, Any]:
        """Safe preflight metadata: no argv, prompts or permission mutation.

        An isolated cwd reduces project startup context; it is NOT a filesystem,
        network or tool sandbox. Tool permissions remain owned by the CLI.
        """
        import shutil
        review = mode == "review"
        command = self._review_command if review and self._review_command else self._command
        available = bool(command and shutil.which(command[0]))
        isolated = review and self._review_workdir == "isolated"
        configured = review and self._has_review_profile
        warnings = []
        if not available:
            warnings.append(f"{self.name}: selected provider executable is unavailable")
        if review:
            warnings.append(
                f"{self.name}: review uses {'isolated temporary' if isolated else 'caller'} cwd; "
                "CLI tool permissions are unchanged (not a tool sandbox). "
                "Headless tool approval can fail; no automatic permission bypass."
            )
        return {
            "validator": self.name, "review_profile_configured": bool(configured),
            "workdir": "isolated" if isolated else "caller", "launchable": available,
            "tool_policy": "provider_owned_unchanged", "warnings": warnings,
        }

    def _permission_error(self, stderr: str):
        permission = tool_permission_denial(stderr)
        if permission:
            return ProviderToolPermissionError(
                f"{self.name} tool_permission_denied: {permission} requires approval "
                "unavailable in headless mode. Lope will not retry, bypass, or grant this "
                "permission. Use another validator or review the requested action manually. "
                "The CLI diagnostic does not identify the requested command."
            )
        return None

    def _executable_for(self, context=None) -> str:
        if getattr(context, "mode", None) == "review" and self._review_command:
            return self._review_command[0]
        return self._command[0]

    def _run(self, prompt: str, timeout: int, context=None) -> tuple[int, str, str, float]:
        """Execute the subprocess; return (returncode, stdout, stderr, duration).

        Shared between validate() and generate(). Handles argv-substitution
        vs. stdin modes, prompt wrapper, timeout override, and the common
        error-to-infra-error translation at the caller site.

        Uses the safe process-group runner so timeout kills the entire
        process tree, not just the direct child.
        """
        import time as _time
        started = _time.time()
        if self._prompt_wrapper:
            prompt = self._prompt_wrapper.format(prompt=prompt)
        review = getattr(context, "mode", None) == "review"
        command = self._review_command if review and self._review_command else self._command
        stdin = self._review_stdin if review else self._stdin
        if stdin:
            cmd = list(command)
            stdin_data = prompt
        else:
            cmd = [arg.replace("{prompt}", prompt) for arg in command]
            stdin_data = None
        effective_timeout = _effective_timeout(self._timeout_override, timeout)
        from .processes import run_subprocess_group
        try:
            caller_cwd = os.environ.get("LOPE_WORKDIR", os.getcwd())
            workdir = (
                _review_directory()
                if review and self._review_workdir == "isolated" else nullcontext(caller_cwd)
            )
            with workdir as cwd:
                proc = run_subprocess_group(
                    cmd, input_text=stdin_data, timeout=effective_timeout,
                    cwd=cwd, context=context,
                    env={"LOPE_WORKDIR": cwd} if review and self._review_workdir == "isolated" else None,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            raise
        return proc.returncode, proc.stdout or "", proc.stderr or "", _time.time() - started

    def validate(self, prompt: str, timeout: int = DEFAULT_MODEL_CALL_TIMEOUT_SECONDS, *, context=None) -> ValidatorResult:
        try:
            rc, stdout, stderr, duration = self._run(prompt, timeout, context=context)
        except subprocess.TimeoutExpired as e:
            denial = self._permission_error(e.stderr)
            if denial is not None:
                return self._infra_error(str(denial), 0.0)
            effective_timeout = _effective_timeout(self._timeout_override, timeout)
            return self._infra_error(
                f"timeout after {effective_timeout}s" + diagnostic_suffix(e.output, e.stderr), 0.0
            )
        except FileNotFoundError as e:
            return self._infra_error(
                "binary not found: " + redact_diagnostic(e.filename or self._executable_for(context)), 0.0
            )
        except Exception as e:
            denial = self._permission_error(getattr(e, "stderr", None))
            if denial is not None:
                return self._infra_error(str(denial), 0.0)
            return self._infra_error(
                "subprocess error: " + redact_diagnostic(e)
                + diagnostic_suffix(getattr(e, "stdout", None), getattr(e, "stderr", None)), 0.0,
            )

        denial = self._permission_error(stderr)
        if denial is not None:
            return self._infra_error(str(denial), duration)
        if rc != 0:
            return self._infra_error(
                f"exit {rc}" + diagnostic_suffix(stdout, stderr), duration
            )

        if not stdout.strip():
            return self._infra_error("returned empty output" + diagnostic_suffix(stdout, stderr), duration)

        verdict = parse_opencode_verdict(
            stdout, validator_name=self._name, fallback_duration=duration
        )
        return ValidatorResult(
            validator_name=self._name,
            verdict=verdict,
            raw_response=stdout,
            error="",
        )

    def generate(self, prompt: str, timeout: int = DEFAULT_MODEL_CALL_TIMEOUT_SECONDS, *, context=None) -> str:
        """Raw CLI invocation — no VERDICT parsing, returns stdout text.

        Used by the `ask` / `review` / `vote` / `compare` / `pipe` verbs
        where we want the model's natural response, not a validation
        verdict. Raises RuntimeError on infra failure so callers can
        per-validator-isolate errors (see `_fanout_generate` in cli.py).
        """
        try:
            rc, stdout, stderr, _duration = self._run(prompt, timeout, context=context)
        except subprocess.TimeoutExpired as e:
            denial = self._permission_error(e.stderr)
            if denial is not None:
                raise denial from None
            effective_timeout = _effective_timeout(self._timeout_override, timeout)
            raise RuntimeError(
                f"{self._name} timed out after {effective_timeout}s"
                + diagnostic_suffix(e.output, e.stderr)
            ) from None
        except FileNotFoundError as e:
            raise RuntimeError(
                f"{self._name} binary not found: "
                + redact_diagnostic(e.filename or self._executable_for(context))
            ) from None
        except Exception as e:
            denial = self._permission_error(getattr(e, "stderr", None))
            if denial is not None:
                raise denial from None
            raise RuntimeError(
                f"{self._name} subprocess error: " + redact_diagnostic(e)
                + diagnostic_suffix(getattr(e, "stdout", None), getattr(e, "stderr", None))
            ) from None

        denial = self._permission_error(stderr)
        if denial is not None:
            raise denial
        if rc != 0:
            raise RuntimeError(
                f"{self._name} exited {rc}" + diagnostic_suffix(stdout, stderr)
            )
        if not stdout.strip():
            raise RuntimeError(f"{self._name} returned empty output" + diagnostic_suffix(stdout, stderr))
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
        self._response_limit: int = int(config.get("response_limit", 2 * 1024 * 1024))
        if self._response_limit <= 0:
            raise ConfigError(f"provider {self._name!r} response_limit must be positive")

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        # HTTP validators are always available (assume network works)
        return True

    def _request(self, prompt: str, timeout: int, context=None) -> tuple[str, float]:
        started = time.time()
        if self._prompt_wrapper:
            prompt = self._prompt_wrapper.format(prompt=prompt)

        # Expand ${VAR} then substitute {prompt}
        headers = _expand_env_dict(self._headers)
        body = _substitute_prompt(_expand_env_dict(self._body), prompt, self._max_tokens)
        if self._max_tokens is not None and isinstance(body, dict) and "max_tokens" not in body:
            body["max_tokens"] = int(self._max_tokens)

        effective_timeout = _effective_timeout(self._timeout_override, timeout)
        payload = _json.dumps(body).encode("utf-8")
        worker_spec = {
            "url": self._url,
            "headers": headers,
            "method": "POST",
            "body_b64": base64.b64encode(payload).decode("ascii"),
            "socket_timeout": min(float(effective_timeout), 30.0),
            "response_limit": self._response_limit,
        }
        from .processes import run_subprocess_group
        proc = run_subprocess_group(
            [sys.executable, "-m", "lope.http_worker"],
            input_text=_json.dumps(worker_spec, separators=(",", ":")),
            timeout=effective_timeout,
            stdout_limit=max(64 * 1024, self._response_limit * 2),
            stderr_limit=256 * 1024,
            context=context,
        )
        try:
            envelope = _json.loads(proc.stdout or "{}")
        except _json.JSONDecodeError as exc:
            raise RuntimeError(f"http worker returned invalid JSON: {exc}")
        if proc.returncode != 0 or envelope.get("error"):
            raise RuntimeError(str(envelope.get("error") or proc.stderr or "HTTP worker failed")[:500])
        status = int(envelope.get("status") or 0)
        response_headers = {
            str(key).lower(): str(value)
            for key, value in (envelope.get("headers") or {}).items()
        }
        raw = base64.b64decode(envelope.get("body_b64") or "").decode(
            "utf-8", errors="replace"
        )
        if status < 200 or status >= 300:
            retry_after = response_headers.get("retry-after")
            retry_detail = f" Retry-After: {retry_after};" if retry_after else ""
            raise RuntimeError(f"HTTP {status}:{retry_detail} {raw[:300]}")
        return raw, time.time() - started

    def validate(self, prompt: str, timeout: int = DEFAULT_MODEL_CALL_TIMEOUT_SECONDS, *, context=None) -> ValidatorResult:
        started = time.time()
        try:
            raw, duration = self._request(prompt, timeout, context=context)
        except Exception as e:
            return self._infra_error(f"http error: {e}", time.time() - started)

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

    def generate(self, prompt: str, timeout: int = DEFAULT_MODEL_CALL_TIMEOUT_SECONDS, *, context=None) -> str:
        try:
            raw, _duration = self._request(prompt, timeout, context=context)
        except Exception as exc:
            raise RuntimeError(f"{self._name} http error: {exc}")
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            data = raw
        text = _extract_response(data, self._response_path)
        if not text.strip():
            raise RuntimeError(f"{self._name} returned empty output")
        return text

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
