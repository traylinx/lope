"""CLI discovery adapter contracts."""

from __future__ import annotations

from lope.cli_discovery import KNOWN_CLIS


def test_pi_auto_adapter_is_stateless_validator_mode():
    pi = next(cli for cli in KNOWN_CLIS if cli.name == "pi")

    assert pi.generic_command == [
        "pi",
        "--no-session",
        "--offline",
        "--no-tools",
        "-p",
        "{prompt}",
    ]
