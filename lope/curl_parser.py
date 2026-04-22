"""Parse a pasted `curl` command into a lope HTTP provider entry.

Rationale: the most grandma-friendly way to hook a new AI API into lope is to
copy the quickstart curl from the provider's docs and paste it. This module
turns that string into the same dict shape `_team_build_http_entry` produces,
so `lope team add <name> --from-curl "<curl>"` feeds straight into the existing
provider registration + validation pipeline.

Design rules:

1. **Never save a literal API key.** If a credential-bearing header contains a
   raw value (e.g. `Authorization: Bearer sk-…`), we require the user to pass
   `--key-env VAR` so the literal is swapped for `${VAR}` at parse time. Raw
   keys in config files (or in shell history, or in `ps`) are exactly the bug
   lope's existing `_validate_provider_config` refuses to produce by hand.

2. **Auto-inject `{prompt}` into the body.** The common request shapes —
   OpenAI/Anthropic-style `messages: [{role, content}]`, legacy-completions
   `prompt`, Cohere `message`, and a handful of other first-party keys — all
   have exactly one user-content slot. We substitute `{prompt}` into it so the
   resulting provider acts as a proper lope validator.

3. **Auto-detect response_path.** OpenAI → `choices.0.message.content`;
   Anthropic → `content.0.text`; Cohere → `text`. User can always override
   with `--response-path`.

4. **Reject unsupported curl shapes loudly.** `-u` basic auth, `-F` multipart,
   `--data-binary @file`, `-X GET` — all produce a clear message explaining
   the workaround. Silent misparsing is worse than a loud refusal.
"""

from __future__ import annotations

import copy
import json
import re
import shlex
import urllib.parse
from typing import Any, Dict, Optional, Tuple


class CurlParseError(ValueError):
    """Raised when a pasted curl can't be translated into a provider entry.

    The message is user-facing — it's printed verbatim to stderr by the CLI
    handler, so it should be actionable (explain what's wrong, what to try).
    """


# ─── Stage 1: tokenize the curl string ─────────────────────────────


_LINE_CONTINUATION_RE = re.compile(r"\\\s*\n\s*")


def _preclean(curl_str: str) -> str:
    """Normalize shell line-continuations (`\\` at EOL) into whitespace.

    `shlex.split(posix=True)` handles quoting and backslash-escape inside
    tokens correctly, but it trips over multi-line pastes where each line
    ends with `\\`. Collapsing them up-front avoids that class of bug.
    """
    return _LINE_CONTINUATION_RE.sub(" ", curl_str)


def parse_curl(curl_str: str) -> Dict[str, Any]:
    """Tokenize a curl command into its semantic parts.

    Returns: {"url": str, "method": str, "headers": {k: v}, "data": str|None}.
    Raises CurlParseError on any shape we refuse to handle.
    """
    if not curl_str or not curl_str.strip():
        raise CurlParseError("empty curl string")

    try:
        tokens = shlex.split(_preclean(curl_str), posix=True)
    except ValueError as e:
        raise CurlParseError(f"could not parse curl — unclosed quote? ({e})") from e

    if not tokens:
        raise CurlParseError("no tokens in curl string")
    if tokens[0] != "curl":
        raise CurlParseError(
            f"expected a curl command; got {tokens[0]!r} as the first token"
        )

    url: Optional[str] = None
    method: Optional[str] = None
    headers: Dict[str, str] = {}
    data: Optional[str] = None

    i = 1
    n = len(tokens)

    def _need_arg(flag: str) -> str:
        nonlocal i
        if i + 1 >= n:
            raise CurlParseError(f"{flag} expects an argument, got end of string")
        val = tokens[i + 1]
        i += 2
        return val

    while i < n:
        t = tokens[i]

        if t in ("-X", "--request"):
            method = _need_arg(t)
            continue

        if t in ("-H", "--header"):
            header_val = _need_arg(t)
            if ":" not in header_val:
                raise CurlParseError(
                    f"malformed header {header_val!r} — expected `Name: value`"
                )
            k, v = header_val.split(":", 1)
            headers[k.strip()] = v.strip()
            continue

        if t in ("-d", "--data", "--data-raw", "--data-ascii", "--data-urlencode"):
            val = _need_arg(t)
            if val.startswith("@"):
                raise CurlParseError(
                    f"{t} @file is not supported — paste the body inline instead"
                )
            data = val
            continue

        if t == "--data-binary":
            val = _need_arg(t)
            if val.startswith("@"):
                raise CurlParseError(
                    "--data-binary @file is not supported — paste the body inline"
                )
            data = val
            continue

        if t in ("-u", "--user"):
            raise CurlParseError(
                "basic auth (-u/--user) is not supported — pre-encode the "
                "credential with `echo -n user:pass | base64` and paste the "
                "result into an Authorization header using ${VAR} substitution."
            )

        if t in ("-F", "--form"):
            raise CurlParseError(
                "multipart form data (-F/--form) is not supported — only JSON "
                "bodies with -d/--data/--data-raw"
            )

        # Known no-arg flags we can safely ignore.
        if t in (
            "-s", "-S", "--silent", "--show-error",
            "-v", "--verbose", "-i", "--include",
            "-L", "--location", "-k", "--insecure",
            "--compressed", "--fail", "-f",
        ):
            i += 1
            continue

        # Known single-arg flags we ignore (but need to consume the value).
        if t in (
            "-o", "--output",
            "-A", "--user-agent",
            "-e", "--referer",
            "-b", "--cookie",
            "--connect-timeout",
            "-m", "--max-time",
            "--cacert", "--cert", "--key",
            "-w", "--write-out",
        ):
            _need_arg(t)
            continue

        # Long options in --flag=value form get collapsed.
        if t.startswith("--") and "=" in t:
            i += 1
            continue

        if t.startswith("-"):
            # Unknown flag — best-effort skip without consuming next token.
            i += 1
            continue

        # Positional → URL (first one wins; subsequent positionals ignored).
        if url is None:
            url = t
        i += 1

    if url is None:
        raise CurlParseError("no URL found in curl command")

    if method is None:
        method = "POST" if data is not None else "GET"
    if method.upper() != "POST":
        raise CurlParseError(
            f"only POST is supported for chat endpoints (got {method}). "
            f"Pass the body with -d/--data or use a different provider type."
        )

    return {"url": url, "method": "POST", "headers": headers, "data": data}


# ─── Stage 2: semantic heuristics ─────────────────────────────────


_CREDENTIAL_HEADERS = frozenset(
    h.lower() for h in (
        "authorization",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-access-token",
    )
)

# A schema-prefix like `Bearer`, `Basic`, `Token` followed by a credential.
_SCHEME_PREFIX_RE = re.compile(r"^(bearer|basic|token)\b\s+(.+)$", re.IGNORECASE)


def _header_has_literal_credential(header_key: str, header_val: str) -> bool:
    """True if the header carries a non-templated credential.

    A value is "templated" if it already contains ${VAR} (we trust the user's
    intent). Otherwise — if the header name is in the credential set — we
    treat whatever's there as a literal that must be swapped or refused.
    """
    if "${" in header_val:
        return False
    return header_key.lower() in _CREDENTIAL_HEADERS


def _substitute_with_env(header_val: str, env_var: str) -> str:
    """Replace the credential portion of a header value with ${env_var}.

    Preserves an optional scheme prefix (`Bearer `, `Basic `, `Token `) so the
    server still receives a well-formed Authorization line.
    """
    m = _SCHEME_PREFIX_RE.match(header_val)
    if m:
        return f"{m.group(1)} ${{{env_var}}}"
    return f"${{{env_var}}}"


def suggest_env_name(url: str) -> str:
    """Guess a conventional env var name from the API hostname.

    api.openai.com → OPENAI_API_KEY
    api.anthropic.com → ANTHROPIC_API_KEY
    generativelanguage.googleapis.com → GENERATIVELANGUAGE_API_KEY
    """
    host = urllib.parse.urlparse(url).hostname or ""
    parts = [
        p for p in host.split(".")
        if p and p not in ("api", "www") and not p.isdigit()
    ]
    if not parts:
        return "API_KEY"
    # Skip TLDs (last 1-2 segments) where possible.
    candidate_parts = parts[:-1] if len(parts) > 1 else parts
    # Pick the most distinctive segment.
    for seg in candidate_parts:
        if len(seg) >= 3:
            return f"{seg.upper()}_API_KEY"
    return f"{candidate_parts[0].upper()}_API_KEY"


# User-facing body keys we know how to inject {prompt} into.
_SCALAR_PROMPT_KEYS = ("prompt", "input", "message", "query", "text")


def _inject_prompt_placeholder(body: Any) -> Tuple[Any, bool]:
    """Find the user-content slot in a parsed JSON body and set it to `{prompt}`.

    Walks common shapes in order:
      1. `messages` list with OpenAI/Anthropic-style `{role, content}` dicts —
         targets the LAST `role=user` message (preserves system prompts and
         multi-turn history). Falls back to the last message if none is user.
      2. Top-level scalar keys: `prompt`, `input`, `message`, `query`, `text`.

    Returns (new_body, True) on success — the user can rely on the result being
    a proper lope template. Returns (original_body, False) if no slot was found
    (caller must decide whether to refuse or pass through).
    """
    if not isinstance(body, dict):
        return body, False

    body = copy.deepcopy(body)

    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                msg["content"] = "{prompt}"
                return body, True
        last = messages[-1]
        if isinstance(last, dict) and "content" in last:
            last["content"] = "{prompt}"
            return body, True

    for key in _SCALAR_PROMPT_KEYS:
        if key in body and isinstance(body[key], str):
            body[key] = "{prompt}"
            return body, True

    return body, False


def _infer_response_path(url: str, headers: Dict[str, str]) -> str:
    """Guess the JSON dot-path to the assistant's reply from the endpoint shape.

    Anthropic's /v1/messages → `content.0.text`.
    Cohere's /v1/chat → `text`.
    Everything else → OpenAI-compatible `choices.0.message.content` — this
    covers OpenAI, Groq, Together, Deepinfra, Tytus pods, vLLM, etc.
    """
    host_lower = (url or "").lower()
    header_keys_lower = {k.lower(): v for k, v in headers.items()}
    if "anthropic" in host_lower or "anthropic-version" in header_keys_lower:
        return "content.0.text"
    if "cohere" in host_lower:
        return "text"
    return "choices.0.message.content"


# ─── Stage 3: assemble the provider entry ─────────────────────────


def curl_to_provider_entry(
    name: str,
    parsed: Dict[str, Any],
    *,
    key_env: Optional[str] = None,
    response_path: Optional[str] = None,
    wrap: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Turn a parsed curl dict (from `parse_curl`) into a lope provider entry.

    `key_env` / `response_path` / `wrap` / `timeout` are user-supplied overrides
    from the CLI flags — they take precedence over auto-detection.

    Raises CurlParseError if the curl has a literal credential and the caller
    didn't provide a --key-env override, or if the body shape has no slot where
    {prompt} belongs and the user didn't manually put it in the body.
    """
    url: str = parsed["url"]
    headers: Dict[str, str] = dict(parsed["headers"])
    data_raw: Optional[str] = parsed.get("data")

    if not data_raw:
        raise CurlParseError(
            "curl has no request body (-d/--data). A chat endpoint needs "
            "a JSON body to know what model to call and where the prompt goes."
        )

    # Handle credentials. If a header is still literal after this loop, refuse.
    for hkey, hval in list(headers.items()):
        if _header_has_literal_credential(hkey, hval):
            if key_env:
                headers[hkey] = _substitute_with_env(hval, key_env)
            else:
                suggested = suggest_env_name(url)
                raise CurlParseError(
                    f"Header {hkey!r} in the pasted curl has a literal "
                    f"credential. Two ways to fix:\n"
                    f"  1. Before pasting, replace the credential with "
                    f"${{{suggested}}} — e.g. "
                    f"     'Authorization: Bearer ${{{suggested}}}'.\n"
                    f"  2. Re-run with --key-env {suggested} "
                    f"(or your env var name) and lope will swap the "
                    f"literal for you."
                )

    # Try to parse the body as JSON; fall back to raw string.
    try:
        body: Any = json.loads(data_raw)
        body_is_json = True
    except json.JSONDecodeError:
        body = data_raw
        body_is_json = False

    prompt_injected = False
    if body_is_json:
        body, prompt_injected = _inject_prompt_placeholder(body)

    # If we didn't auto-inject, the user must have put {prompt} in the pasted
    # body themselves — otherwise the provider can't work.
    if not prompt_injected:
        serialized = json.dumps(body) if body_is_json else str(body or "")
        if "{prompt}" not in serialized:
            raise CurlParseError(
                "could not auto-detect where the user prompt belongs in the "
                "request body. Options:\n"
                "  1. Before pasting, replace the test prompt in the body "
                "with {prompt} — e.g. "
                '\'{"messages":[{"role":"user","content":"{prompt}"}]}\'.\n'
                "  2. Use --body-json '<your JSON>' directly instead of "
                "--from-curl."
            )

    entry: Dict[str, Any] = {
        "name": name,
        "type": "http",
        "url": url,
        "headers": headers,
        "body": body,
        "response_path": response_path or _infer_response_path(url, headers),
    }
    if wrap:
        entry["prompt_wrapper"] = wrap
    if timeout:
        entry["timeout"] = timeout
    return entry


__all__ = [
    "CurlParseError",
    "curl_to_provider_entry",
    "parse_curl",
    "suggest_env_name",
]
