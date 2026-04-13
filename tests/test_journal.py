"""Tests for v0.4.0 Phase 5 — journal append/read.

Covers:
  - append_event writes one JSONL line per call with monotonic timestamp
  - read_recent returns the N newest entries in file order (newest last)
  - append_event is best-effort under disk-full / permission errors
    (never propagates an exception)
  - journal_path respects LOPE_HOME
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lope.journal import append_event, journal_path, read_recent


# ---------------------------------------------------------------------------
# journal_path respects LOPE_HOME
# ---------------------------------------------------------------------------

def test_journal_path_respects_lope_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    assert journal_path() == str(tmp_path / "journal.jsonl")


def test_journal_path_default_under_home(monkeypatch):
    monkeypatch.delenv("LOPE_HOME", raising=False)
    path = journal_path()
    assert path.endswith("journal.jsonl")
    assert ".lope" in path


# ---------------------------------------------------------------------------
# append_event writes one line per call
# ---------------------------------------------------------------------------

def test_append_event_writes_one_jsonl_line(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    append_event("heal_attempt", {"cli": "codex", "old_argv": ["codex", "exec"]})

    path = tmp_path / "journal.jsonl"
    assert path.exists()
    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["event"] == "heal_attempt"
    assert record["cli"] == "codex"
    assert record["old_argv"] == ["codex", "exec"]
    assert "timestamp" in record
    assert isinstance(record["timestamp"], (int, float))


def test_append_event_multiple_calls_append(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    append_event("heal_attempt", {"cli": "codex"})
    append_event("heal_success", {"cli": "codex", "confidence": 0.9})
    append_event("heal_failure", {"cli": "claude", "reason": "smoke_test_failed"})

    path = tmp_path / "journal.jsonl"
    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 3

    events = [json.loads(line) for line in lines]
    assert [e["event"] for e in events] == ["heal_attempt", "heal_success", "heal_failure"]
    assert events[0]["cli"] == "codex"
    assert events[1]["confidence"] == 0.9
    assert events[2]["reason"] == "smoke_test_failed"


def test_append_event_timestamps_are_monotonic_nondecreasing(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    for i in range(5):
        append_event("heal_attempt", {"i": i})
        time.sleep(0.001)

    path = tmp_path / "journal.jsonl"
    with open(path) as f:
        lines = f.readlines()
    events = [json.loads(line) for line in lines]
    timestamps = [e["timestamp"] for e in events]
    for earlier, later in zip(timestamps, timestamps[1:]):
        assert later >= earlier


def test_append_event_creates_parent_dir(tmp_path, monkeypatch):
    nested = tmp_path / "nested" / "deeper"
    monkeypatch.setenv("LOPE_HOME", str(nested))
    # Directory does not exist yet
    append_event("heal_attempt", {"cli": "test"})
    assert (nested / "journal.jsonl").exists()


# ---------------------------------------------------------------------------
# read_recent — newest entries, limit, order
# ---------------------------------------------------------------------------

def test_read_recent_empty_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    assert read_recent() == []


def test_read_recent_returns_all_when_under_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    append_event("a", {"i": 1})
    append_event("b", {"i": 2})
    append_event("c", {"i": 3})

    recent = read_recent(limit=20)
    assert len(recent) == 3
    assert [r["event"] for r in recent] == ["a", "b", "c"]


def test_read_recent_limit_returns_newest_last(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    for i in range(10):
        append_event("heal_attempt", {"i": i})

    recent = read_recent(limit=3)
    assert len(recent) == 3
    # Newest last — so indexes should be 7, 8, 9
    assert [r["i"] for r in recent] == [7, 8, 9]


def test_read_recent_skips_malformed_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path))
    path = tmp_path / "journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write('{"event": "good", "cli": "a"}\n')
        f.write('{not valid json\n')
        f.write('\n')  # blank line
        f.write('{"event": "also_good", "cli": "b"}\n')

    recent = read_recent(limit=20)
    assert len(recent) == 2
    assert [r["event"] for r in recent] == ["good", "also_good"]


# ---------------------------------------------------------------------------
# append_event is best-effort — errors are swallowed
# ---------------------------------------------------------------------------

def test_append_event_swallows_disk_errors(tmp_path, monkeypatch):
    """If the journal file cannot be written (permission, disk full, etc.),
    append_event must NOT raise — observability must never break the
    call site that invoked it."""
    # Point LOPE_HOME at a path that cannot be created
    # (e.g. a file masquerading as a directory)
    fake_home_file = tmp_path / "not_a_dir"
    fake_home_file.write_text("i am a file")
    monkeypatch.setenv("LOPE_HOME", str(fake_home_file))

    # Must not raise
    try:
        append_event("heal_attempt", {"cli": "codex"})
    except Exception as e:
        pytest.fail(f"append_event raised {type(e).__name__}: {e}")


def test_read_recent_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPE_HOME", str(tmp_path / "nonexistent"))
    assert read_recent(limit=10) == []
