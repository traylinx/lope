"""Provider failures; permission denials must not trigger writer failover."""

import re


class ProviderInfrastructureError(RuntimeError):
    """Provider process or transport failed before returning a usable result."""


class ProviderToolPermissionError(RuntimeError):
    """A provider tool needs human approval; terminal, never auto-escalated.

    Deliberately NOT a ProviderInfrastructureError: changing implementation
    writers must not bypass a tool-policy decision.
    """


_AGY_PERMISSION_DENIAL = re.compile(
    r'(?:^|;\s*stderr:\s*)jetski: no output produced[^\n]*?'
    r'a tool required the "(?P<permission>[a-z][a-z0-9_-]{0,63})" permission '
    r'that headless mode cannot prompt for, so it was auto-denied\.',
    re.IGNORECASE | re.MULTILINE,
)


def tool_permission_denial(stderr: str) -> str:
    """Recognize the observed native diagnostic, not arbitrary model prose.

    Call on stderr BEFORE diagnostic clipping. The wrapped form is supported
    for older recorded Lope errors. No command text is invented or extracted
    from the vendor's suggested permission-bypass instructions.
    """
    text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr or "")
    match = _AGY_PERMISSION_DENIAL.search(text)
    return match.group("permission").lower() if match else ""
