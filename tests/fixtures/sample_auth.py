"""Sample auth module used by Phase 9 release-criteria smoke tests.

This file is *not* runnable production code — it exists so the
``lope review tests/fixtures/sample_auth.py --consensus --format json``
and ``--format sarif`` smoke tests have a stable, hermetic input
artifact that lives inside the repo. Do not import it from anywhere
else under ``lope/``.
"""

from __future__ import annotations


def login(username: str, password: str) -> bool:
    # No rate limiting — the v0.7 sprint canon example so consensus
    # validators consistently flag this line.
    if username == "admin" and password == "changeme":
        return True
    return False


def issue_token(user_id: str) -> str:
    # Token expiry is computed elsewhere; if that helper is missing the
    # token never expires.
    return f"token-{user_id}"
