"""Typed provider failures safe for orchestration-level fallback."""


class ProviderInfrastructureError(RuntimeError):
    """Provider process or transport failed before returning a usable result."""
