"""
Lope journal — append-only JSONL log at ~/.lope/journal.jsonl (v0.4.0).

Every self-heal attempt writes an event here. Format is one JSON object
per line with a monotonic timestamp, event type, and payload dict.

Event types:
  - heal_attempt   — healer started, context captured
  - heal_success   — new adapter learned and persisted
  - heal_failure   — proposal rejected (smoke test failed, no proposal, etc.)
  - heal_skipped   — should_attempt() returned False

Readers: `lope status` surfaces the most recent entries. External tooling
may tail this file for dashboards. Never parse the lope source to discover
events — always read from here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict


def journal_path() -> str:
    """Return path to ~/.lope/journal.jsonl, respecting LOPE_HOME."""
    home = os.environ.get("LOPE_HOME", os.path.expanduser("~/.lope"))
    return os.path.join(home, "journal.jsonl")


def append_event(event: str, payload: Dict[str, Any]) -> None:
    """Append a single JSONL line with a monotonic timestamp.

    Creates the journal file and parent directory if missing. Silently
    skips on any write error — the journal is observability, never
    load-bearing, so a disk-full or permission error must never break
    a heal attempt or a sprint run.
    """
    record = {
        "timestamp": time.time(),
        "event": event,
        **payload,
    }
    try:
        path = Path(journal_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass  # observability layer is best-effort


def read_recent(limit: int = 20) -> list[dict]:
    """Read the most recent journal entries (up to `limit`). Newest last.

    Returns an empty list if the journal is missing or unreadable.
    Skips malformed lines silently.
    """
    path = journal_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    entries: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
