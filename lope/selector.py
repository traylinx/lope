"""Interactive CLI selector wizard for Lope validator configuration."""

from __future__ import annotations

import sys
from typing import List, Optional

from .cli_discovery import CliInfo, defaults
from .config import LopeCfg, save


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def run_selector(
    available: List[CliInfo], current: Optional[LopeCfg] = None
) -> LopeCfg:
    """Show interactive selector. In non-interactive mode returns defaults silently."""
    if not is_interactive():
        defs = defaults(available)
        cfg = LopeCfg(
            validators=[c.name for c in defs] if defs else [],
            primary=defs[0].name if defs else "",
            timeout=480,
            parallel=True,
        )
        return cfg

    print()
    print("Lope \u2014 CLI validator setup")
    print("\u2500" * 50)
    print()
    print("Detected AI CLIs on this machine:")
    print()

    max_disp = max(len(c.display_name) for c in available)
    for i, cli in enumerate(available, start=1):
        if cli.is_default:
            marker = f"  \u25cf DEFAULT"
            padded = cli.display_name.ljust(max_disp)
            print(f"  [{i}] {padded}  ({cli.name}){marker}")
        else:
            print(f"  [{i}] {cli.display_name:<{max_disp}}  ({cli.name})")

    default_names = [c.name for c in defaults(available)]
    default_display = ",".join(default_names) if default_names else "[none detected]"

    print()
    print(
        f"Select validators (comma-separated numbers, or Enter for defaults [{default_display}]):"
    )
    print("> ", end="", flush=True)
    raw_input = sys.stdin.readline().strip()

    if not raw_input:
        selected_names = default_names[:]
    else:
        selected: List[CliInfo] = []
        for part in raw_input.split(","):
            part = part.strip()
            if not part.isdigit():
                continue
            idx = int(part) - 1
            if 0 <= idx < len(available):
                selected.append(available[idx])
        if not selected:
            selected_names = default_names[:]
        else:
            selected_names = [c.name for c in selected]

    print()
    print("Primary validator (tie-breaker, Enter for [1]):")
    print("> ", end="", flush=True)
    raw_primary = sys.stdin.readline().strip()

    if not raw_primary:
        primary = selected_names[0] if selected_names else ""
    elif raw_primary.isdigit():
        idx = int(raw_primary) - 1
        if 0 <= idx < len(selected_names):
            primary = selected_names[idx]
        else:
            primary = selected_names[0] if selected_names else ""
    else:
        primary = raw_primary.strip()

    print()
    print("Parallel mode? Runs all validators concurrently (Y/n, Enter for Y):")
    print("> ", end="", flush=True)
    raw_parallel = sys.stdin.readline().strip()

    if raw_parallel.lower() in ("n", "no"):
        parallel = False
    else:
        parallel = True

    return LopeCfg(
        validators=selected_names,
        primary=primary,
        timeout=480,
        parallel=parallel,
    )
