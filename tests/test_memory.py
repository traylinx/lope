"""Tests for ``lope.memory`` — schema, upsert semantics, redaction,
search/hotspots/file/forget, and the ``LOPE_MEMORY``/``LOPE_MEMORY_DB``
environment switches.

All tests use a temp DB path so the user's real ``~/.lope/memory.db``
is never touched.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from lope.findings import ConsensusFinding, ConsensusLevel
from lope.memory import (
    ENV_DB_PATH,
    ENV_DISABLE,
    FindingRecord,
    LopeMemory,
    ReviewSessionRecord,
    default_db_path,
    is_memory_disabled,
    open_memory,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "memory.db"


@pytest.fixture
def store(tmp_db: Path) -> LopeMemory:
    return LopeMemory(db_path=tmp_db)


def _consensus(
    *,
    message="missing rate limit",
    file="auth.py",
    line=42,
    severity="high",
    category="security",
    confidence=0.8,
    score=0.85,
    detected_by=("claude", "gemini"),
    agreement_count=2,
    total_validators=3,
    level=ConsensusLevel.CONFIRMED,
    end_line=None,
):
    return ConsensusFinding(
        message=message,
        file=file,
        line=line,
        end_line=end_line,
        severity=severity,
        category=category,
        detected_by=list(detected_by),
        evidence={n: f"{n} saw it" for n in detected_by},
        confidence_max=confidence,
        confidence_avg=confidence,
        agreement_count=agreement_count,
        total_validators=total_validators,
        agreement_ratio=agreement_count / max(total_validators, 1),
        consensus_score=score,
        consensus_level=level,
    )


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def test_is_memory_disabled_recognizes_known_tokens():
    for value in ("off", "OFF", "no", "0", "false", "disabled"):
        assert is_memory_disabled({ENV_DISABLE: value}) is True
    assert is_memory_disabled({ENV_DISABLE: "on"}) is False
    assert is_memory_disabled({}) is False
    assert is_memory_disabled({ENV_DISABLE: ""}) is False


def test_default_db_path_honors_env_override(tmp_path):
    target = tmp_path / "elsewhere" / "lope.db"
    resolved = default_db_path({ENV_DB_PATH: str(target), "HOME": str(tmp_path)})
    assert resolved == target


def test_default_db_path_uses_home_when_unset(tmp_path):
    resolved = default_db_path({"HOME": str(tmp_path)})
    assert resolved == tmp_path / ".lope" / "memory.db"


def test_open_memory_returns_none_when_disabled(tmp_db):
    result = open_memory(db_path=tmp_db, env={ENV_DISABLE: "off"})
    assert result is None
    # And the disabled path should NOT have created a DB file.
    assert not tmp_db.exists()


def test_open_memory_returns_store_when_enabled(tmp_db):
    result = open_memory(db_path=tmp_db, env={})
    assert isinstance(result, LopeMemory)
    assert tmp_db.exists()


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------


def test_constructor_creates_schema(tmp_db):
    LopeMemory(db_path=tmp_db)
    with sqlite3.connect(str(tmp_db)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"findings", "review_sessions", "session_findings"}.issubset(tables)


def test_schema_is_idempotent(tmp_db):
    LopeMemory(db_path=tmp_db)
    LopeMemory(db_path=tmp_db)  # second open must not raise


def test_constructor_creates_parent_directory(tmp_path):
    nested = tmp_path / "deep" / "nested" / "memory.db"
    LopeMemory(db_path=nested)
    assert nested.exists()


# ---------------------------------------------------------------------------
# store_review_session + upsert
# ---------------------------------------------------------------------------


def test_store_review_session_inserts_session_and_findings(store):
    session_id, records = store.store_review_session(
        task="audit auth",
        focus="security",
        target_path="auth.py",
        validators=["claude", "gemini", "codex"],
        findings=[_consensus()],
    )
    assert session_id == 1
    assert len(records) == 1
    assert records[0].seen_count == 1
    s = store.stats()
    assert s["total_findings"] == 1
    assert s["total_sessions"] == 1
    assert s["confirmed_findings"] == 1


def test_upsert_preserves_first_seen_at_and_increments_seen_count(store):
    finding = _consensus()
    sid1, _ = store.store_review_session(
        task="t",
        focus="",
        target_path="auth.py",
        validators=["claude", "gemini"],
        findings=[finding],
    )
    first = store.get_finding_by_hash(finding.hash)
    time.sleep(1.05)  # ensure ISO seconds tick over so last_seen_at differs
    sid2, _ = store.store_review_session(
        task="t",
        focus="",
        target_path="auth.py",
        validators=["claude", "gemini"],
        findings=[finding],
    )
    second = store.get_finding_by_hash(finding.hash)
    assert sid2 != sid1
    assert second.seen_count == 2
    assert second.first_seen_at == first.first_seen_at
    assert second.last_seen_at >= first.last_seen_at


def test_upsert_keeps_max_confidence_and_score(store):
    finding = _consensus(confidence=0.5, score=0.4)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[finding],
    )
    stronger = _consensus(confidence=0.9, score=0.85)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude", "gemini"], findings=[stronger],
    )
    rec = store.get_finding_by_hash(finding.hash)
    assert rec.confidence == pytest.approx(0.9)
    assert rec.consensus_score == pytest.approx(0.85)


def test_upsert_promotes_consensus_level_forward(store):
    base = _consensus(level=ConsensusLevel.UNVERIFIED)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[base],
    )
    upgrade = _consensus(level=ConsensusLevel.CONFIRMED)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude", "gemini"], findings=[upgrade],
    )
    rec = store.get_finding_by_hash(base.hash)
    assert rec.consensus_level == "confirmed"


def test_upsert_does_not_demote_consensus_level(store):
    strong = _consensus(level=ConsensusLevel.CONFIRMED)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[strong],
    )
    weak = _consensus(level=ConsensusLevel.UNVERIFIED)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[weak],
    )
    rec = store.get_finding_by_hash(strong.hash)
    assert rec.consensus_level == "confirmed"


def test_upsert_unions_detected_by_and_evidence(store):
    a = _consensus(detected_by=("claude",))
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[a],
    )
    b = _consensus(detected_by=("gemini",))
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["gemini"], findings=[b],
    )
    rec = store.get_finding_by_hash(a.hash)
    assert sorted(rec.detected_by) == ["claude", "gemini"]
    assert "claude" in rec.evidence and "gemini" in rec.evidence


def test_session_findings_junction_links_findings_to_session(store, tmp_db):
    finding = _consensus()
    sid, _ = store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[finding],
    )
    with sqlite3.connect(str(tmp_db)) as conn:
        rows = conn.execute(
            "SELECT session_id, finding_hash FROM session_findings "
            "WHERE session_id = ?",
            (sid,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == finding.hash


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redaction_strips_secrets_before_storage(store):
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    finding = _consensus(message=f"leaks {secret} on login")
    finding.evidence = {"claude": f"sample {secret}"}
    store.store_review_session(
        task=f"audit with {secret}",
        focus="security",
        target_path="auth.py",
        validators=["claude"],
        findings=[finding],
    )
    rec = store.get_finding_by_hash(finding.hash)
    assert "abcdefghijklmnop" not in rec.message
    assert "abcdefghijklmnop" not in rec.evidence.get("claude", "")
    # Session task should also be redacted.
    with sqlite3.connect(str(store.db_path)) as conn:
        task_row = conn.execute("SELECT task FROM review_sessions").fetchone()
    assert "abcdefghijklmnop" not in task_row[0]


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def test_search_findings_uses_like_filter_and_min_score(store):
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"],
        findings=[
            _consensus(message="rate limiting on login", score=0.9),
            _consensus(message="csrf token missing", file="auth.py", line=88, score=0.4),
            _consensus(message="comment style nit", file="auth.py", line=99, score=0.1, severity="low",
                       level=ConsensusLevel.UNVERIFIED),
        ],
    )
    results = store.search_findings("rate")
    assert len(results) == 1
    assert "rate limiting" in results[0].message
    high_only = store.search_findings("", min_score=0.5)
    assert all(r.consensus_score >= 0.5 for r in high_only)


def test_findings_for_file_returns_only_that_file(store):
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"],
        findings=[
            _consensus(file="auth.py", line=42),
            _consensus(file="other.py", line=10, message="other issue"),
        ],
    )
    rows = store.findings_for_file("auth.py")
    assert len(rows) == 1
    assert rows[0].file == "auth.py"


def test_hotspots_groups_by_file_and_orders_by_count(store):
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"],
        findings=[
            _consensus(file="auth.py", line=42),
            _consensus(file="auth.py", line=88, message="other issue"),
            _consensus(file="api.py", line=10, message="api issue"),
        ],
    )
    spots = store.hotspots(days=30, limit=5)
    assert spots[0]["file"] == "auth.py"
    assert spots[0]["finding_count"] == 2
    assert spots[1]["file"] == "api.py"


def test_hotspots_respects_days_window(store):
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"],
        findings=[_consensus()],
    )
    # Move the row to 60 days ago so a 30-day query excludes it.
    with sqlite3.connect(str(store.db_path)) as conn:
        old = (
            __import__("datetime").datetime.utcnow()
            - __import__("datetime").timedelta(days=60)
        ).isoformat(timespec="seconds")
        conn.execute("UPDATE findings SET last_seen_at = ?", (old,))
        conn.commit()
    assert store.hotspots(days=30) == []
    assert len(store.hotspots(days=90)) == 1


def test_stats_reports_zero_state_cleanly(store):
    out = store.stats()
    assert out["total_findings"] == 0
    assert out["total_sessions"] == 0
    assert out["confirmed_findings"] == 0
    assert out["recurring_findings"] == 0
    assert out["flagged_files"] == 0
    assert out["last_session_at"] is None
    assert "db_path" in out


def test_stats_counts_recurring_findings(store):
    finding = _consensus()
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[finding],
    )
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[finding],
    )
    out = store.stats()
    assert out["recurring_findings"] == 1
    assert out["total_findings"] == 1


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_by_hash_removes_finding_and_junction(store, tmp_db):
    finding = _consensus()
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[finding],
    )
    removed = store.forget(hash=finding.hash)
    assert removed == 1
    assert store.get_finding_by_hash(finding.hash) is None
    with sqlite3.connect(str(tmp_db)) as conn:
        junction = conn.execute(
            "SELECT COUNT(*) FROM session_findings"
        ).fetchone()[0]
    assert junction == 0


def test_forget_by_file_removes_all_findings_for_file(store):
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"],
        findings=[
            _consensus(file="auth.py", line=42),
            _consensus(file="auth.py", line=88, message="other issue"),
            _consensus(file="other.py", line=1, message="other"),
        ],
    )
    removed = store.forget(file="auth.py")
    assert removed == 2
    assert store.findings_for_file("auth.py") == []
    assert len(store.findings_for_file("other.py")) == 1


def test_forget_requires_a_selector(store):
    with pytest.raises(ValueError):
        store.forget()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def test_finding_record_to_dict_round_trips_basic_fields(store):
    finding = _consensus()
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[finding],
    )
    rec = store.get_finding_by_hash(finding.hash)
    payload = rec.to_dict()
    assert payload["hash"] == finding.hash
    assert payload["seen_count"] == 1
    assert payload["consensus_level"] == "confirmed"
    assert payload["detected_by"] == sorted(rec.detected_by) or set(payload["detected_by"]) == set(rec.detected_by)


def test_forget_by_file_also_clears_junction_table(store, tmp_db):
    # Regression: prior implementation deleted findings first then ran a
    # subquery against the empty table, orphaning every junction row.
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"],
        findings=[
            _consensus(file="auth.py", line=42),
            _consensus(file="auth.py", line=88, message="another"),
        ],
    )
    with sqlite3.connect(str(tmp_db)) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM session_findings"
        ).fetchone()[0]
    assert before == 2

    removed = store.forget(file="auth.py")
    assert removed == 2

    with sqlite3.connect(str(tmp_db)) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM session_findings"
        ).fetchone()[0]
    assert after == 0


def test_disputed_level_does_not_demote_or_promote(store):
    # Regression: a previous level rank tied disputed with
    # needs-verification at 2, which let it incorrectly outrank
    # unverified or block real promotion.
    base = _consensus(level=ConsensusLevel.UNVERIFIED)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[base],
    )
    disputed_finding = _consensus(level=ConsensusLevel.DISPUTED)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[disputed_finding],
    )
    rec = store.get_finding_by_hash(base.hash)
    # disputed must not promote out of unverified.
    assert rec.consensus_level == "unverified"

    confirmed_finding = _consensus(level=ConsensusLevel.CONFIRMED)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[confirmed_finding],
    )
    rec = store.get_finding_by_hash(base.hash)
    assert rec.consensus_level == "confirmed"

    disputed_again = _consensus(level=ConsensusLevel.DISPUTED)
    store.store_review_session(
        task="t", focus="", target_path="auth.py",
        validators=["claude"], findings=[disputed_again],
    )
    rec = store.get_finding_by_hash(base.hash)
    # disputed must not demote a confirmed finding.
    assert rec.consensus_level == "confirmed"


def test_redaction_covers_path_and_category_columns(store, tmp_db):
    # Regression: docstring promises full-boundary redaction; previously
    # ``file``, ``category``, and ``target_path`` slipped past it.
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    finding = _consensus(
        file=f"prod/{secret}/auth.py",
        category=f"security {secret}",
        message="rate limit",
    )
    store.store_review_session(
        task="t",
        focus="",
        target_path=f"prod/{secret}/auth.py",
        validators=["claude"],
        findings=[finding],
    )
    with sqlite3.connect(str(tmp_db)) as conn:
        rows = conn.execute(
            "SELECT message, file, category FROM findings"
        ).fetchall()
        sessions = conn.execute(
            "SELECT target_path FROM review_sessions"
        ).fetchall()
    for cell in rows[0] + sessions[0]:
        if cell:
            assert "abcdefghijklmnop" not in cell


def test_review_session_record_dataclass_round_trip():
    record = ReviewSessionRecord(
        id=1,
        task="audit",
        focus="security",
        target_path="auth.py",
        validators=["claude", "gemini"],
        raw_count=5,
        merged_count=3,
        created_at="2026-04-27T00:00:00",
        duration_ms=1234,
    )
    payload = record.to_dict()
    assert payload["validators"] == ["claude", "gemini"]
    assert payload["duration_ms"] == 1234
