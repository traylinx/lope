"""Conservative adapter-output validity normalization.

This does not rewrite model prose. It only classifies empty/native tool-call
envelopes versus substantive text so quorum logic cannot mistake intent to use
a tool for a completed answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OutputValidity:
    substantive: bool
    kind: str
    text: str = ""


def _content_from_envelope(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("response", "answer", "text", "content", "output_text"):
        content = value.get(key)
        if isinstance(content, str) and content.strip():
            return content.strip()
    choices = value.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else {}
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def classify_output(raw: str) -> OutputValidity:
    text = (raw or "").strip()
    if not text:
        return OutputValidity(False, "empty")
    if text.upper() in ("CLEAN", "PASS", "OK", "NO FINDINGS"):
        return OutputValidity(True, "structured_clean", text)
    try:
        envelope = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        envelope = None
    if isinstance(envelope, dict):
        content = _content_from_envelope(envelope)
        if content:
            return OutputValidity(True, "envelope_text", content)
        tool_keys = ("tool_calls", "tool_use", "function_call", "tool")
        if any(envelope.get(key) for key in tool_keys):
            return OutputValidity(False, "tool_only")
        return OutputValidity(False, "empty_envelope")
    lowered = text.lower()
    if lowered.startswith(("<tool_use", "<tool_call", "tool_calls:")) and "verdict" not in lowered:
        return OutputValidity(False, "tool_only")
    return OutputValidity(True, "text", text)


__all__ = ["OutputValidity", "classify_output"]
