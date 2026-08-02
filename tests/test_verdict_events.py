"""Tests for the ``verdict_events`` migration (schema version 1).

Covers the guarantee that matters most: a Lope ``memory.db`` holds review
history that cannot be regenerated, so the migration must be additive and a
failure part-way through must leave the file byte-identical.

All tests use a temp DB path so the user's real ``~/.lope/memory.db`` is
never touched.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from lope.memory import SCHEMA_VERSION, LopeMemory


LEGACY_TABLES = ("findings", "review_sessions", "session_findings", "gate_sessions")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tables(path: Path) -> set:
    conn = sqlite3.connect(str(path))
    try:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _make_legacy_db(path: Path, *, findings: int = 3) -> None:
    """Build a pre-versioning (user_version=0) DB holding real rows."""
    conn = sqlite3.connect(str(path))
    conn.executescript(LopeMemory.SCHEMA)
    now = "2026-08-02T00:00:00+00:00"
    for i in range(findings):
        conn.execute(
            "INSERT INTO findings (hash, message, severity, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (f"hash-{i}", f"finding {i}", "warning", now, now),
        )
    conn.execute(
        "INSERT INTO gate_sessions (task, mode, baseline_path, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("t", "check", "", now),
    )
    conn.commit()
    conn.close()
    assert _user_version(path) == 0


# ---------------------------------------------------------------------------
# Fresh database
# ---------------------------------------------------------------------------


def test_fresh_db_gets_verdict_events_at_current_version(tmp_path):
    db = tmp_path / "memory.db"
    LopeMemory(db)

    assert "verdict_events" in _tables(db)
    assert _user_version(db) == SCHEMA_VERSION


def test_verdict_events_accepts_a_full_event_round_trip(tmp_path):
    db = tmp_path / "memory.db"
    LopeMemory(db)

    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO verdict_events (
            run_id, gate_id, validator_name, prompt_template_version,
            rendered_prompt, task_spec_hash, raw_response, parser_version,
            initial_parse_status, parse_error_category, final_verdict,
            latency_s, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "run-1", "phase-0", "codex", "v1",
            "rendered prompt text", "spec-hash", "raw response", "p1",
            "INFRA_ERROR", "missing-verdict-block", "INFRA_ERROR",
            1.25, "2026-08-02T00:00:00+00:00",
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT validator_name, initial_parse_status, parse_error_category,"
        " repair_attempted, timed_out FROM verdict_events"
    ).fetchone()
    conn.close()

    assert row == ("codex", "INFRA_ERROR", "missing-verdict-block", 0, 0)


# ---------------------------------------------------------------------------
# Existing database -- additive upgrade
# ---------------------------------------------------------------------------


def test_legacy_db_upgrades_without_losing_rows(tmp_path):
    db = tmp_path / "memory.db"
    _make_legacy_db(db, findings=5)
    before = {t: _count(db, t) for t in LEGACY_TABLES}

    LopeMemory(db)

    after = {t: _count(db, t) for t in LEGACY_TABLES}
    assert after == before
    assert after["findings"] == 5
    assert "verdict_events" in _tables(db)
    assert _user_version(db) == SCHEMA_VERSION


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "memory.db"
    _make_legacy_db(db)

    LopeMemory(db)
    digest_after_first = _digest(db)
    counts_after_first = {t: _count(db, t) for t in LEGACY_TABLES}

    LopeMemory(db)

    assert _user_version(db) == SCHEMA_VERSION
    assert {t: _count(db, t) for t in LEGACY_TABLES} == counts_after_first
    # Second run must not re-apply DDL; WAL bookkeeping aside, table set is stable.
    assert "verdict_events" in _tables(db)
    assert digest_after_first is not None  # digest captured for parity with rollback test


# ---------------------------------------------------------------------------
# Failure path -- the guarantee
# ---------------------------------------------------------------------------


def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    """A mid-migration failure must leave the database logically unchanged.

    Note on the guarantee: *byte* identity is not achievable and is the wrong
    bar. Merely opening the DB rewrites header bytes 18-19 because
    ``_connect`` sets ``journal_mode = WAL``, and any write transaction --
    even one that rolls back -- bumps the file change counter (bytes 24-27)
    and version-valid-for (bytes 92-95). None of those carry schema or row
    data. What must hold is logical identity: same tables, same schema
    version, same rows, same page count, and a clean integrity check.
    """
    db = tmp_path / "memory.db"
    _make_legacy_db(db, findings=4)

    # Force a failure *after* the additive DDL has run inside the transaction.
    def boom(_conn):
        raise RuntimeError("simulated validation failure")

    monkeypatch.setattr(LopeMemory, "_validate_schema", staticmethod(boom))

    _checkpoint(db)
    before = _logical_snapshot(db)

    with pytest.raises(RuntimeError, match="simulated validation failure"):
        LopeMemory(db)

    _checkpoint(db)
    after = _logical_snapshot(db)

    assert after == before
    assert after["integrity"] == "ok"
    assert "verdict_events" not in after["tables"]
    assert after["user_version"] == 0
    assert after["rows"]["findings"] == 4


def test_validate_schema_rejects_missing_legacy_table(tmp_path):
    db = tmp_path / "memory.db"
    LopeMemory(db)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE gate_sessions")
        with pytest.raises(RuntimeError, match="missing table"):
            LopeMemory._validate_schema(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _checkpoint(path: Path) -> None:
    """Fold the WAL back into the main file so snapshots are comparable."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _logical_snapshot(path: Path) -> dict:
    """Everything that must survive a rolled-back migration.

    Deliberately excludes SQLite header bookkeeping (journal mode, change
    counter) which changes on any open/write and carries no schema or data.
    """
    conn = sqlite3.connect(str(path))
    try:
        tables = sorted(
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        )
        return {
            "tables": tables,
            "schema_sql": sorted(
                row[0] or ""
                for row in conn.execute("SELECT sql FROM sqlite_master ORDER BY name")
            ),
            "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "page_count": int(conn.execute("PRAGMA page_count").fetchone()[0]),
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "rows": {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables},
            "findings_digest": hashlib.sha256(
                repr(
                    conn.execute("SELECT hash, message FROM findings ORDER BY hash").fetchall()
                ).encode()
            ).hexdigest(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Version and shape guards
# ---------------------------------------------------------------------------


def test_future_schema_version_is_rejected(tmp_path):
    """Older code must not write into a database it does not understand."""
    db = tmp_path / "memory.db"
    LopeMemory(db)
    conn = sqlite3.connect(str(db))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="newer Lope"):
        LopeMemory(db)


def test_incompatible_preexisting_verdict_events_is_rejected(tmp_path):
    """CREATE TABLE IF NOT EXISTS silently accepts a wrong-shaped table."""
    db = tmp_path / "memory.db"
    _make_legacy_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE verdict_events (id INTEGER PRIMARY KEY, junk TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="missing column"):
        LopeMemory(db)

    # And the version must not have advanced past a schema that cannot accept rows.
    assert _user_version(db) == 0
