"""Bounded, credential-redacted subprocess diagnostics, never a full transcript."""

from __future__ import annotations

import re


def redact_diagnostic(value) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    text = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)",
        "[REDACTED PRIVATE KEY]", text,
    )
    text = re.sub(r"<think>[\s\S]*?(?:</think>|$)", "[REDACTED REASONING]", text)
    text = re.sub(
        r'(?im)^.*(?:"thinking_delta"|"reasoning_content"|"thinking"\s*:).*$',
        "[REDACTED REASONING]", text,
    )
    text = re.sub(
        r"(?i)\b(Bearer|Basic)\s+[^\s\"'<>]+", r"\1 [REDACTED]", text,
    )
    text = re.sub(
        r'''(?ix)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)
            ["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''',
        r"\1[REDACTED]", text,
    )
    text = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})\b",
        "[REDACTED]", text,
    )
    text = re.sub(
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED JWT]", text,
    )
    text = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    return text


def subprocess_diagnostics(stdout, stderr, limit: int = 500) -> str:
    """Redact before clipping so a tail cannot expose half of a credential."""
    details = []
    for name, value in (("stdout", stdout), ("stderr", stderr)):
        text = redact_diagnostic(value).strip()
        if text:
            if len(text) > limit:
                text = "...[truncated]..." + text[-limit:]
            details.append(f"{name}: {text}")
    return "; ".join(details)


def diagnostic_suffix(stdout, stderr) -> str:
    detail = subprocess_diagnostics(stdout, stderr)
    return "; " + detail if detail else ""
