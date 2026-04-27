"""Secret redaction helpers for Lope reports, memory, logs, and exports.

Lope is often asked to review prompts, configs, cURL snippets, and CI logs.
Those surfaces commonly contain API keys or private keys. This module is the
single stdlib-only place that scrubs secrets before v0.7 structured data writes
anything durable.
"""

from __future__ import annotations

import re
from typing import Any

# Keep replacements stable and human-readable. Tests assert these exact shapes
# because downstream memory/export code should be able to rely on them.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM private key blocks. Preserve the key family marker without retaining
    # any body material.
    (
        re.compile(
            r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----.*?-----END \1-----",
            re.DOTALL,
        ),
        r"-----BEGIN \1-----\n<redacted>\n-----END \1-----",
    ),
    # OpenAI-style keys and common sk-* provider keys.
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), "sk-<redacted>"),
    # GitHub personal access tokens.
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"), "ghp_<redacted>"),
    # Bearer tokens in headers / logs.
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]{10,}\b", re.IGNORECASE), "Bearer <redacted>"),
    # Header-like long opaque tokens: X-API-Key: abc..., api_key=abc..., token: abc...
    (
        re.compile(
            r"(?i)\b(api[-_ ]?key|x-api-key|authorization|token|access_token|secret)"
            r"(\s*[:=]\s*)(['\"]?)[A-Za-z0-9._~+\-/=]{16,}\3"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}<redacted>{m.group(3)}",
    ),
)


def redact_text(value: Any) -> str:
    """Return ``value`` as text with known secret shapes removed.

    The function is intentionally conservative: it catches high-signal key
    patterns and header-style tokens without rewriting ordinary prose. It is
    safe to call repeatedly; replacements are idempotent.
    """
    text = "" if value is None else str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Redact string leaves in a mapping, recursively.

    Useful for JSON payloads assembled by CLI commands. Nested dict/list values
    are traversed recursively, but object identity is not preserved.
    """
    return {str(k): _redact_value(v) for k, v in data.items()}


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    return value


__all__ = ["redact_text", "redact_mapping"]
