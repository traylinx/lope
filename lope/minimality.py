"""Minimality discipline for Lope prompts.

Ponytail-inspired, but Lope-native: prompt/rubric text only, no third-party
runtime hooks. Defaults to audit for engineering execute/implement paths so
normal Lope runs get over-engineering review without making every finding a
hard failure. Set ``LOPE_MINIMALITY=off`` to disable or ``enforce`` to gate
material bloat.
"""

from __future__ import annotations

import os

VALID_MODES = {"off", "audit", "enforce"}
DEFAULT_MODE = "audit"


def _env_has_override() -> bool:
    return "LOPE_MINIMALITY" in os.environ


def mode(value: str | None = None) -> str:
    """Resolve LOPE_MINIMALITY mode."""

    raw = os.environ.get("LOPE_MINIMALITY", DEFAULT_MODE) if value is None else value
    resolved = str(raw or "").strip().lower()
    return resolved if resolved in VALID_MODES else DEFAULT_MODE


def _effective_mode(value: str | None = None, *, domain: str | None = None) -> str:
    """Resolve mode with domain-aware defaults.

    Engineering gets default audit. Business/research stay off unless the user
    explicitly sets LOPE_MINIMALITY or tests pass a value.
    """

    resolved = mode(value)
    normalized_domain = str(domain or "engineering").strip().lower()
    explicit = value is not None or _env_has_override()
    if not explicit and normalized_domain not in ("", "engineering"):
        return "off"
    return resolved


def implementation_directive(value: str | None = None, *, domain: str | None = None) -> str:
    """Return implementer guidance for the current minimality mode."""

    resolved = _effective_mode(value, domain=domain)
    if resolved == "off":
        return ""

    level = "enforced completion contract" if resolved == "enforce" else "audit guidance"
    return f"""Minimality discipline ({resolved}, {level}):
- Understand touched flow before writing.
- Prefer no build, existing code, stdlib, native platform, installed deps, then minimum custom code.
- Reuse helpers before creating new ones.
- Bugfix root cause once; search sibling callers.
- Do not cut security, validation, accessibility, data-loss handling, or explicit requirements.
- Leave one runnable check for non-trivial logic."""


def validator_rubric(
    value: str | None = None,
    *,
    stage: str | None = None,
    domain: str | None = None,
) -> str:
    """Return validator minimality rubric for quality/legacy review stages."""

    resolved = _effective_mode(value, domain=domain)
    if resolved == "off":
        return ""
    if stage not in (None, "quality"):
        return ""

    threshold = (
        "In enforce mode, return NEEDS_FIX only for material bloat with a concrete safer replacement."
        if resolved == "enforce"
        else "In audit mode, report material findings but do not fail spec-compliant work for style alone."
    )
    return f"""Minimality review ({resolved}):
- Flag unnecessary new dependencies.
- Flag duplicate helpers/patterns already present.
- Flag interfaces/factories/providers/config with one real implementation.
- Flag broad rewrites where a local/shared fix satisfies the phase.
- Flag symptom patches that miss sibling callers.
- Never ask to remove security, validation, accessibility, data-loss handling, or explicit requirements.
{threshold}"""


OVER_ENGINEERING_FOCUS = """Review for over-engineering only. One line per finding:
<path>:L<line>: <tag>: <what to cut>. <replacement>.
Tags: delete, stdlib, native, yagni, reuse, shrink, wrong-layer.
Do not report correctness, security, performance, or style issues unless the issue is unnecessary complexity.
Never recommend removing validation, security, accessibility, data-loss handling, or explicit requirements.
If nothing real: Lean already. Ship."""

FOCUS_ALIASES = {
    "over-engineering",
    "overengineering",
    "minimality",
    "lazy-build",
    "lazy build",
}


def resolve_review_focus(focus: str | None) -> str:
    """Expand convenience focus aliases for ``lope review --focus``."""

    text = str(focus or "").strip()
    if text.lower() in FOCUS_ALIASES:
        return OVER_ENGINEERING_FOCUS
    return text
