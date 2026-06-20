"""Trust boundary for executing project-defined gate commands.

`lope gate` / `lope check` / `lope execute --gates` run the shell commands
declared in a repository's `.lope/rules.json` (see `lope.gates.run_gate`, which
uses ``subprocess.run(..., shell=True)``). Those commands come from the *repo*,
not from the user — so running them in an untrusted checkout is arbitrary code
execution with the user's privileges.

This module gates that execution behind an explicit, remembered decision:

- The first time a repo's gate commands would run, lope lists them and asks.
- Trust is recorded per ``(repo root, hash of the gate command set)`` under
  ``$LOPE_HOME/trusted_gates.json`` (``LOPE_HOME`` defaults to ``~/.lope``), so
  *changing* the gate commands re-prompts — a trusted repo can't silently add a
  new command later.
- Non-interactive sessions (CI, agents, pipes) fail **closed**: the commands are
  listed but not run unless trust is granted via ``--trust`` or
  ``LOPE_TRUST_GATES=1``.

The check lives at the CLI layer; the library functions in ``lope.gates`` are
left pure so they remain directly callable in tests and embeddings.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _lope_home() -> Path:
    return Path(os.environ.get("LOPE_HOME", os.path.expanduser("~/.lope")))


def _trust_store_path() -> Path:
    return _lope_home() / "trusted_gates.json"


def gate_command_digest(specs: Sequence) -> str:
    """Order-independent sha256 over the gate command set."""
    h = hashlib.sha256()
    for cmd in sorted((getattr(s, "cmd", "") or "") for s in specs):
        h.update(cmd.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _load_store() -> dict:
    try:
        data = json.loads(_trust_store_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_trusted(root: Path, digest: str) -> bool:
    entry = _load_store().get(str(Path(root).resolve()))
    return isinstance(entry, list) and digest in entry


def record_trust(root: Path, digest: str) -> None:
    store = _load_store()
    key = str(Path(root).resolve())
    digests = store.get(key)
    if not isinstance(digests, list):
        digests = []
    if digest not in digests:
        digests.append(digest)
    store[key] = digests
    path = _trust_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")


def ensure_gates_trusted(
    specs: Sequence,
    *,
    cwd: Optional[Path] = None,
    assume_yes: bool = False,
    stream=None,
    input_fn=input,
) -> bool:
    """Return True if it is OK to execute ``specs`` in ``cwd``.

    Fail-closed: in a non-interactive session an untrusted repo's gate commands
    are listed but NOT run unless trust is granted via ``assume_yes`` (the
    ``--trust`` flag) or the ``LOPE_TRUST_GATES`` / ``LOPE_YES_RUN_PROJECT_COMMANDS``
    environment variable.
    """
    out = stream if stream is not None else sys.stderr
    if not specs:
        return True

    root = Path(cwd or os.getcwd()).resolve()
    digest = gate_command_digest(specs)

    if assume_yes or _truthy_env("LOPE_TRUST_GATES") or _truthy_env("LOPE_YES_RUN_PROJECT_COMMANDS"):
        record_trust(root, digest)
        return True

    if is_trusted(root, digest):
        return True

    print(
        f"\n!  lope is about to run {len(specs)} project-defined gate command(s)",
        file=out,
    )
    print(f"   from {root}/.lope/rules.json — they execute with your privileges:", file=out)
    for spec in specs:
        print(f"     - {getattr(spec, 'name', '?')}: {getattr(spec, 'cmd', '')}", file=out)
    print("   Only run gate commands from a repository you trust.\n", file=out)

    stream_isatty = getattr(out, "isatty", lambda: False)
    interactive = sys.stdin.isatty() and stream_isatty()
    if not interactive:
        print(
            "   Refusing to run gate commands non-interactively from an untrusted repo.\n"
            "   Re-run with --trust, or set LOPE_TRUST_GATES=1, to allow them.",
            file=out,
        )
        return False

    try:
        answer = input_fn("   Run these gate commands? [y]es once / [a]lways / [N]o: ").strip().lower()
    except EOFError:
        answer = ""
    if answer in {"a", "always"}:
        record_trust(root, digest)
        return True
    if answer in {"y", "yes"}:
        return True
    print("   Skipped gate execution (repo not trusted).", file=out)
    return False
