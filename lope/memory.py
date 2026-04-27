"""Persistent cross-session memory for Lope findings.

When a user runs ``lope review --consensus --remember``, the deduped
:class:`~lope.findings.ConsensusFinding` results are upserted into a
local SQLite database so future sessions can answer questions like:

* "Is this a recurring issue? When did we first see it?"
* "Which files keep getting flagged in the last 30 days?"
* "Show me everything stored about ``auth.py``."

The store is stdlib-only (``sqlite3``), local, and opt-in. Writes are
gated by:

* ``LOPE_MEMORY=off`` — global kill switch; ``--remember`` becomes a
  visible no-op so the user knows their request was honored.
* ``LOPE_MEMORY_DB=/some/path/memory.db`` — relocate the DB without
  changing ``LOPE_HOME``.

All inbound text passes through :func:`lope.redaction.redact_text`
before it is bound to a row, so credentials in raw critique prose
never reach disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .findings import ConsensusFinding
from .redaction import redact_text


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


ENV_DISABLE = "LOPE_MEMORY"
ENV_DB_PATH = "LOPE_MEMORY_DB"
DEFAULT_DIR_NAME = ".lope"
DEFAULT_DB_NAME = "memory.db"


def is_memory_disabled(env: Optional[Dict[str, str]] = None) -> bool:
    """Return ``True`` when ``LOPE_MEMORY`` is set to a disable token.

    Accepted disable tokens (case-insensitive): ``off``, ``no``, ``0``,
    ``false``, ``disabled``. Anything else (including unset) means
    memory is active. The function is environment-aware so tests can
    pass a custom dict without mutating ``os.environ``.
    """

    src = env if env is not None else os.environ
    raw = src.get(ENV_DISABLE, "")
    if not raw:
        return False
    return raw.strip().lower() in {"off", "no", "0", "false", "disabled"}


def default_db_path(env: Optional[Dict[str, str]] = None) -> Path:
    """Resolve the SQLite path from env or the default ``~/.lope`` slot."""

    src = env if env is not None else os.environ
    explicit = src.get(ENV_DB_PATH, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = Path(src.get("HOME") or Path.home())
    return home / DEFAULT_DIR_NAME / DEFAULT_DB_NAME


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class FindingRecord:
    """One row in the ``findings`` table.

    ``first_seen_at`` is preserved across re-detections; ``last_seen_at``
    and ``seen_count`` advance every time the same hash is recorded.
    """

    hash: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    end_line: Optional[int] = None
    severity: str = "info"
    category: Optional[str] = None
    confidence: float = 0.0
    consensus_score: float = 0.0
    consensus_level: str = "unverified"
    agreement_count: int = 0
    total_validators: int = 0
    detected_by: List[str] = field(default_factory=list)
    evidence: Dict[str, str] = field(default_factory=dict)
    first_seen_at: str = ""
    last_seen_at: str = ""
    seen_count: int = 0
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "hash": self.hash,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "severity": self.severity,
            "category": self.category,
            "confidence": round(self.confidence, 3),
            "consensus_score": round(self.consensus_score, 3),
            "consensus_level": self.consensus_level,
            "agreement_count": self.agreement_count,
            "total_validators": self.total_validators,
            "detected_by": list(self.detected_by),
            "evidence": dict(self.evidence),
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "seen_count": self.seen_count,
        }


@dataclass
class ReviewSessionRecord:
    """One row in the ``review_sessions`` table."""

    id: Optional[int]
    task: str
    focus: str
    target_path: str
    validators: List[str]
    raw_count: int
    merged_count: int
    created_at: str
    duration_ms: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "focus": self.focus,
            "target_path": self.target_path,
            "validators": list(self.validators),
            "raw_count": self.raw_count,
            "merged_count": self.merged_count,
            "created_at": self.created_at,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _record_from_consensus(finding: ConsensusFinding, *, now: str) -> FindingRecord:
    """Build a redaction-clean :class:`FindingRecord` from a consensus finding."""

    detected = [redact_text(name).strip() for name in (finding.detected_by or [])]
    evidence = {
        redact_text(k).strip(): redact_text(v).strip()
        for k, v in (finding.evidence or {}).items()
    }
    file_value = redact_text(finding.file).strip() if finding.file else None
    category_value = (
        redact_text(finding.category).strip() if finding.category else None
    )
    return FindingRecord(
        hash=finding.hash,
        message=redact_text(finding.message).strip(),
        file=file_value or None,
        line=finding.line,
        end_line=finding.end_line,
        severity=finding.severity or "info",
        category=category_value or None,
        confidence=float(finding.confidence_max),
        consensus_score=float(finding.consensus_score),
        consensus_level=finding.consensus_level.value,
        agreement_count=int(finding.agreement_count),
        total_validators=int(finding.total_validators),
        detected_by=detected,
        evidence=evidence,
        first_seen_at=now,
        last_seen_at=now,
        seen_count=1,
    )


# ---------------------------------------------------------------------------
# LopeMemory
# ---------------------------------------------------------------------------


class LopeMemory:
    """SQLite-backed cross-session memory store.

    Instantiate with an explicit ``db_path`` for tests; production
    callers usually build via :func:`open_memory` to inherit the
    ``LOPE_MEMORY_DB`` and disable-switch handling.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS findings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        hash            TEXT UNIQUE NOT NULL,
        message         TEXT NOT NULL,
        file            TEXT,
        line            INTEGER,
        end_line        INTEGER,
        severity        TEXT NOT NULL DEFAULT 'info',
        category        TEXT,
        confidence      REAL NOT NULL DEFAULT 0.0,
        consensus_score REAL NOT NULL DEFAULT 0.0,
        consensus_level TEXT NOT NULL DEFAULT 'unverified',
        agreement_count INTEGER NOT NULL DEFAULT 0,
        total_validators INTEGER NOT NULL DEFAULT 0,
        detected_by_json TEXT NOT NULL DEFAULT '[]',
        evidence_json    TEXT NOT NULL DEFAULT '{}',
        first_seen_at    TEXT NOT NULL,
        last_seen_at     TEXT NOT NULL,
        seen_count       INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS review_sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task            TEXT NOT NULL DEFAULT '',
        focus           TEXT NOT NULL DEFAULT '',
        target_path     TEXT NOT NULL DEFAULT '',
        validators_json TEXT NOT NULL DEFAULT '[]',
        raw_count       INTEGER NOT NULL DEFAULT 0,
        merged_count    INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        duration_ms     INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS session_findings (
        session_id    INTEGER NOT NULL,
        finding_hash  TEXT NOT NULL,
        PRIMARY KEY (session_id, finding_hash)
    );

    CREATE INDEX IF NOT EXISTS idx_findings_hash         ON findings(hash);
    CREATE INDEX IF NOT EXISTS idx_findings_file         ON findings(file);
    CREATE INDEX IF NOT EXISTS idx_findings_score        ON findings(consensus_score DESC);
    CREATE INDEX IF NOT EXISTS idx_findings_last_seen    ON findings(last_seen_at DESC);
    CREATE INDEX IF NOT EXISTS idx_session_findings_hash ON session_findings(finding_hash);
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        # ``check_same_thread=False`` keeps tests that share fixtures safe;
        # production paths only ever touch one thread at a time.
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    # -- store -------------------------------------------------------------

    def store_review_session(
        self,
        *,
        task: str,
        focus: str,
        target_path: str,
        validators: Sequence[str],
        findings: Sequence[ConsensusFinding],
        duration_ms: int = 0,
    ) -> Tuple[int, List[FindingRecord]]:
        """Persist a review session and upsert each finding.

        Returns ``(session_id, stored_records)`` where each
        :class:`FindingRecord` reflects the post-upsert row state
        (preserved ``first_seen_at`` + incremented ``seen_count``).
        """

        now = _now_iso()
        records = [_record_from_consensus(f, now=now) for f in findings]

        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO review_sessions "
                "(task, focus, target_path, validators_json, raw_count, "
                "merged_count, created_at, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    redact_text(task or "").strip(),
                    redact_text(focus or "").strip(),
                    redact_text(str(target_path or "")).strip(),
                    json.dumps([redact_text(v).strip() for v in (validators or [])]),
                    len(findings),
                    sum(1 for _ in findings),
                    now,
                    int(duration_ms or 0),
                ),
            )
            session_id = int(cur.lastrowid)

            stored = [self._upsert_finding(conn, record, now=now) for record in records]

            conn.executemany(
                "INSERT OR IGNORE INTO session_findings (session_id, finding_hash) VALUES (?, ?)",
                [(session_id, r.hash) for r in stored if r.hash],
            )
            conn.commit()

        return session_id, stored

    def _upsert_finding(
        self,
        conn: sqlite3.Connection,
        record: FindingRecord,
        *,
        now: str,
    ) -> FindingRecord:
        """Insert ``record`` or merge into the existing row by hash."""

        existing = conn.execute(
            "SELECT * FROM findings WHERE hash = ?",
            (record.hash,),
        ).fetchone()

        if existing is None:
            conn.execute(
                "INSERT INTO findings "
                "(hash, message, file, line, end_line, severity, category, "
                "confidence, consensus_score, consensus_level, agreement_count, "
                "total_validators, detected_by_json, evidence_json, "
                "first_seen_at, last_seen_at, seen_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.hash, record.message, record.file, record.line,
                    record.end_line, record.severity, record.category,
                    record.confidence, record.consensus_score, record.consensus_level,
                    record.agreement_count, record.total_validators,
                    json.dumps(record.detected_by),
                    json.dumps(record.evidence),
                    record.first_seen_at, record.last_seen_at, record.seen_count,
                ),
            )
            return record

        # Merge semantics: keep first_seen_at; bump last_seen_at and
        # seen_count; take MAX of confidence / consensus_score (a finding
        # never gets weaker over time); promote consensus_level forward
        # (confirmed > likely > needs-verification > unverified > disputed).
        merged_level = _max_consensus_level(
            str(existing["consensus_level"]),
            record.consensus_level,
        )
        merged_detected = _merge_string_lists(
            json.loads(existing["detected_by_json"] or "[]"),
            record.detected_by,
        )
        merged_evidence = dict(json.loads(existing["evidence_json"] or "{}"))
        merged_evidence.update(record.evidence)

        new_seen = int(existing["seen_count"]) + 1
        new_confidence = max(float(existing["confidence"]), record.confidence)
        new_score = max(float(existing["consensus_score"]), record.consensus_score)

        conn.execute(
            "UPDATE findings SET "
            "message = ?, file = ?, line = ?, end_line = ?, severity = ?, "
            "category = ?, confidence = ?, consensus_score = ?, "
            "consensus_level = ?, agreement_count = ?, total_validators = ?, "
            "detected_by_json = ?, evidence_json = ?, last_seen_at = ?, "
            "seen_count = ? "
            "WHERE hash = ?",
            (
                record.message, record.file, record.line, record.end_line,
                record.severity, record.category, new_confidence, new_score,
                merged_level, record.agreement_count, record.total_validators,
                json.dumps(merged_detected), json.dumps(merged_evidence),
                now, new_seen, record.hash,
            ),
        )

        record.id = int(existing["id"])
        record.first_seen_at = str(existing["first_seen_at"])
        record.last_seen_at = now
        record.seen_count = new_seen
        record.confidence = new_confidence
        record.consensus_score = new_score
        record.consensus_level = merged_level
        record.detected_by = merged_detected
        record.evidence = merged_evidence
        return record

    # -- query -------------------------------------------------------------

    def search_findings(
        self,
        query: str,
        *,
        min_score: float = 0.0,
        limit: int = 20,
    ) -> List[FindingRecord]:
        """LIKE search on ``message`` filtered by ``min_score``."""

        like = f"%{query.strip()}%" if query else "%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM findings "
                "WHERE message LIKE ? AND consensus_score >= ? "
                "ORDER BY consensus_score DESC, seen_count DESC, last_seen_at DESC "
                "LIMIT ?",
                (like, float(min_score), int(limit)),
            ).fetchall()
        return [_row_to_finding(r) for r in rows]

    def findings_for_file(
        self,
        file: str,
        *,
        limit: int = 50,
    ) -> List[FindingRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM findings WHERE file = ? "
                "ORDER BY consensus_score DESC, last_seen_at DESC LIMIT ?",
                (file, int(limit)),
            ).fetchall()
        return [_row_to_finding(r) for r in rows]

    def hotspots(
        self,
        *,
        days: int = 30,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        cutoff = (datetime.utcnow() - timedelta(days=int(days))).isoformat(
            timespec="seconds"
        )
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file, COUNT(*) AS finding_count, "
                "SUM(seen_count) AS detection_count, "
                "AVG(consensus_score) AS avg_score, "
                "MAX(last_seen_at) AS last_seen_at "
                "FROM findings "
                "WHERE file IS NOT NULL AND last_seen_at >= ? "
                "GROUP BY file "
                "ORDER BY finding_count DESC, detection_count DESC "
                "LIMIT ?",
                (cutoff, int(limit)),
            ).fetchall()
        return [
            {
                "file": r["file"],
                "finding_count": int(r["finding_count"]),
                "detection_count": int(r["detection_count"] or 0),
                "avg_score": round(float(r["avg_score"] or 0.0), 3),
                "last_seen_at": r["last_seen_at"],
            }
            for r in rows
        ]

    def stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
            sessions = conn.execute(
                "SELECT COUNT(*) FROM review_sessions"
            ).fetchone()[0]
            confirmed = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE consensus_level = 'confirmed'"
            ).fetchone()[0]
            recurring = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE seen_count > 1"
            ).fetchone()[0]
            files = conn.execute(
                "SELECT COUNT(DISTINCT file) FROM findings WHERE file IS NOT NULL"
            ).fetchone()[0]
            last_session = conn.execute(
                "SELECT created_at FROM review_sessions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return {
            "db_path": str(self.db_path),
            "total_findings": int(total),
            "total_sessions": int(sessions),
            "confirmed_findings": int(confirmed),
            "recurring_findings": int(recurring),
            "flagged_files": int(files),
            "last_session_at": last_session[0] if last_session else None,
        }

    # -- mutate ------------------------------------------------------------

    def forget(
        self,
        *,
        hash: Optional[str] = None,  # noqa: A002 — matches CLI flag
        file: Optional[str] = None,
    ) -> int:
        """Delete findings matching ``hash`` or ``file``.

        At least one selector is required. Returns the number of rows
        removed from ``findings`` (junction rows are removed too).
        """

        if not hash and not file:
            raise ValueError("forget() requires hash or file")
        with self._connect() as conn:
            if hash:
                # Junction first so any FK-style dependants are cleared
                # while we still hold the parent row for reference.
                conn.execute(
                    "DELETE FROM session_findings WHERE finding_hash = ?",
                    (hash,),
                )
                cursor = conn.execute(
                    "DELETE FROM findings WHERE hash = ?", (hash,)
                )
            else:
                # Order matters: the junction subquery references findings,
                # so it has to run before findings rows are deleted. The
                # earlier ``DELETE FROM findings`` first ordering left the
                # junction subquery empty and orphaned every link.
                conn.execute(
                    "DELETE FROM session_findings WHERE finding_hash IN "
                    "(SELECT hash FROM findings WHERE file = ?)",
                    (file,),
                )
                cursor = conn.execute(
                    "DELETE FROM findings WHERE file = ?", (file,)
                )
            conn.commit()
        return int(cursor.rowcount or 0)

    def clear(self) -> None:
        """Wipe all stored memory. Used by tests; never wired into the CLI."""

        with self._connect() as conn:
            conn.execute("DELETE FROM session_findings")
            conn.execute("DELETE FROM review_sessions")
            conn.execute("DELETE FROM findings")
            conn.commit()

    def get_finding_by_hash(self, finding_hash: str) -> Optional[FindingRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM findings WHERE hash = ?", (finding_hash,)
            ).fetchone()
        return _row_to_finding(row) if row else None


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


# The promotion ladder used by ``_max_consensus_level`` is the same ordering
# advertised in :func:`_upsert_finding`'s docstring:
#   confirmed > likely > needs-verification > unverified > disputed
# Disputed sits at the bottom on purpose: in v0.7 it is reserved for the
# explicit-negation phase and should never silently demote a previously
# confirmed finding, but neither should it inherit promotion strength.
_LEVEL_RANK = {
    "confirmed": 4,
    "likely": 3,
    "needs-verification": 2,
    "unverified": 1,
    "disputed": 0,
    "": 0,
}


def _max_consensus_level(a: str, b: str) -> str:
    if _LEVEL_RANK.get(b, 0) > _LEVEL_RANK.get(a, 0):
        return b
    return a


def _merge_string_lists(existing: Iterable[str], incoming: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in list(existing) + list(incoming):
        if value and value not in out:
            out.append(value)
    return out


def _row_to_finding(row: sqlite3.Row) -> FindingRecord:
    return FindingRecord(
        id=int(row["id"]),
        hash=str(row["hash"]),
        message=str(row["message"]),
        file=row["file"],
        line=row["line"],
        end_line=row["end_line"],
        severity=str(row["severity"]),
        category=row["category"],
        confidence=float(row["confidence"]),
        consensus_score=float(row["consensus_score"]),
        consensus_level=str(row["consensus_level"]),
        agreement_count=int(row["agreement_count"]),
        total_validators=int(row["total_validators"]),
        detected_by=json.loads(row["detected_by_json"] or "[]"),
        evidence=json.loads(row["evidence_json"] or "{}"),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        seen_count=int(row["seen_count"]),
    )


def open_memory(
    *,
    db_path: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> Optional[LopeMemory]:
    """Return a :class:`LopeMemory` or ``None`` when memory is disabled.

    The function is the canonical entry point for CLI commands so the
    ``LOPE_MEMORY=off`` kill switch only has to be implemented once.
    Tests can pass an explicit ``env`` mapping to exercise both
    branches without touching ``os.environ``.
    """

    if is_memory_disabled(env):
        return None
    return LopeMemory(db_path=db_path or default_db_path(env))


__all__ = [
    "DEFAULT_DB_NAME",
    "DEFAULT_DIR_NAME",
    "ENV_DB_PATH",
    "ENV_DISABLE",
    "FindingRecord",
    "LopeMemory",
    "ReviewSessionRecord",
    "default_db_path",
    "is_memory_disabled",
    "open_memory",
]
