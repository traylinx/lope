"""Typed, budget-aware transient retry policy.

Only rate limiting, service unavailability, and connection-reset failures are
retryable. Timeouts, authentication, parsing, invalid input, and output limits
are terminal for the current call. One retry is the default hard ceiling.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Optional


MAX_TRANSIENT_RETRIES = 1
DEFAULT_TRANSIENT_DELAY_SECONDS = 0.25
MAX_JITTER_SECONDS = 0.25

_RETRY_AFTER_RE = re.compile(
    r"retry[-_ ]after\s*(?::|=)?\s*(?P<seconds>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FailureClassification:
    kind: str
    retryable: bool
    retry_after_seconds: Optional[float] = None


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    reason: str
    delay_seconds: float = 0.0
    classification: str = ""


def classify_failure(error: object) -> FailureClassification:
    text = str(error or "").strip()
    lowered = text.lower()
    retry_after = None
    match = _RETRY_AFTER_RE.search(text)
    if match:
        try:
            retry_after = max(0.0, float(match.group("seconds")))
        except (TypeError, ValueError):
            retry_after = None

    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return FailureClassification("rate_limited", True, retry_after)
    if "503" in lowered or "service unavailable" in lowered:
        return FailureClassification("service_unavailable", True, retry_after)
    if any(token in lowered for token in (
        "connection reset",
        "connectionreseterror",
        "econnreset",
        "remote end closed connection",
        "remotedisconnected",
    )):
        return FailureClassification("connection_reset", True, retry_after)
    if "timed out" in lowered or "timeout after" in lowered or "provider_timeout" in lowered:
        return FailureClassification("provider_timeout", False)
    if "output_limit" in lowered or "output limit" in lowered or "exceeded" in lowered and "stdout" in lowered:
        return FailureClassification("output_limit", False)
    if "input_limit" in lowered or "argv payload" in lowered or "prompt" in lowered and "too large" in lowered:
        return FailureClassification("input_limit", False)
    if any(token in lowered for token in (
        "unauthorized",
        "forbidden",
        "invalid api key",
        "authentication",
        "http 401",
        "http 403",
    )):
        return FailureClassification("auth_error", False)
    if any(token in lowered for token in (
        "parse error",
        "invalid json",
        "no ---verdict---",
        "unparseable",
        "empty output",
    )):
        return FailureClassification("parse_error", False)
    return FailureClassification("launch_error", False)


def _deterministic_jitter(seed: str) -> float:
    if os.environ.get("LOPE_RETRY_JITTER", "").strip().lower() in {
        "0", "off", "false", "no",
    }:
        return 0.0
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).digest()
    fraction = int.from_bytes(digest[:4], "big") / float(2**32 - 1)
    return fraction * MAX_JITTER_SECONDS


def decide_retry(
    error: object,
    *,
    attempt: int,
    remaining_seconds: float,
    requested_timeout: float,
    seed: str = "",
    max_retries: int = MAX_TRANSIENT_RETRIES,
) -> RetryDecision:
    classification = classify_failure(error)
    if not classification.retryable:
        return RetryDecision(False, "not_retryable", classification=classification.kind)
    if attempt >= max_retries:
        return RetryDecision(False, "retry_limit_reached", classification=classification.kind)

    if classification.retry_after_seconds is not None:
        delay = classification.retry_after_seconds
    else:
        delay = DEFAULT_TRANSIENT_DELAY_SECONDS + _deterministic_jitter(seed)

    required = delay + max(0.0, float(requested_timeout))
    if remaining_seconds < required:
        reason = (
            "retry_after_exceeds_budget"
            if classification.retry_after_seconds is not None
            else "insufficient_budget_for_complete_retry"
        )
        return RetryDecision(False, reason, delay, classification.kind)
    return RetryDecision(True, "transient_retry", delay, classification.kind)


__all__ = [
    "DEFAULT_TRANSIENT_DELAY_SECONDS",
    "FailureClassification",
    "MAX_TRANSIENT_RETRIES",
    "RetryDecision",
    "classify_failure",
    "decide_retry",
]
