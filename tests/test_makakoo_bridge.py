"""Tests for ``lope.makakoo_bridge`` — detection, brain queries, journal +
auto-memory writes, redaction guarantees, no import-time side effects.

We never call a real ``makakoo`` binary or touch the user's
``~/MAKAKOO``: every test points ``MAKAKOO_BIN`` at a stub script and
``MAKAKOO_HOME`` at a temporary directory.
"""

from __future__ import annotations

import datetime as _dt
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from lope.makakoo_bridge import (
    APPROX_CHARS_PER_TOKEN,
    DEFAULT_BRAIN_BUDGET_TOKENS,
    ENV_AUTOMEMORY,
    ENV_BIN,
    ENV_HOME,
    BrainQueryError,
    MakakooAutoMemoryDisabled,
    MakakooDetection,
    MakakooNotDetected,
    build_context_block,
    detect_makakoo,
    format_review_journal_line,
    query_brain,
    redact_for_brain,
    write_auto_memory,
    write_brain_journal,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    home = tmp_path / "brain-home"  # avoid case-collision with `makakoo` stub
    home.mkdir()
    return home


def _write_stub_makakoo(
    tmp_path: Path,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> Path:
    """Create an executable Python stub that imitates the makakoo CLI."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    bin_path = bin_dir / "makakoo"
    bin_path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys
            sys.stdout.write({stdout!r})
            sys.stderr.write({stderr!r})
            sys.exit({exit_code})
            """
        )
    )
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_path


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


def test_module_has_no_import_time_side_effects():
    # Re-import in a fresh subprocess; verify it doesn't shell out, write
    # files, or print anything. The test passes if exit is clean and
    # stdout/stderr are empty.
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; os.environ.pop('MAKAKOO_BIN', None);"
            " os.environ.pop('MAKAKOO_HOME', None);"
            " import lope.makakoo_bridge",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_default_budget_constant_matches_spec():
    assert DEFAULT_BRAIN_BUDGET_TOKENS == 1200
    assert APPROX_CHARS_PER_TOKEN == 4


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_returns_unavailable_when_no_binary(tmp_path):
    # PATH only contains a directory with no makakoo binary.
    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    detection = detect_makakoo(env)
    assert detection.available is False
    assert "Makakoo not detected" in detection.reason


def test_detect_returns_available_when_explicit_bin_exists(tmp_path):
    bin_path = _write_stub_makakoo(tmp_path)
    env = {ENV_BIN: str(bin_path), "PATH": str(tmp_path)}
    detection = detect_makakoo(env)
    assert detection.available is True
    assert detection.bin == str(bin_path)


def test_detect_explicit_bin_must_exist(tmp_path):
    fake = tmp_path / "ghost"
    detection = detect_makakoo({ENV_BIN: str(fake), "PATH": str(tmp_path)})
    assert detection.available is False
    assert "does not exist" in detection.reason


def test_detect_picks_up_path_binary(tmp_path):
    bin_path = _write_stub_makakoo(tmp_path)
    env = {"PATH": str(bin_path.parent)}
    detection = detect_makakoo(env)
    assert detection.available is True
    assert Path(detection.bin).resolve() == bin_path.resolve()


def test_detect_carries_home_through_env(tmp_path, tmp_home):
    bin_path = _write_stub_makakoo(tmp_path)
    env = {ENV_BIN: str(bin_path), ENV_HOME: str(tmp_home)}
    detection = detect_makakoo(env)
    assert detection.home == tmp_home


def test_detection_require_raises_when_unavailable():
    detection = MakakooDetection(available=False, reason="nope")
    with pytest.raises(MakakooNotDetected):
        detection.require()


def test_detection_require_home_raises_when_missing(tmp_path):
    detection = MakakooDetection(available=True, bin="x", home=None)
    with pytest.raises(MakakooNotDetected):
        detection.require_home()
    detection = MakakooDetection(available=True, bin="x", home=tmp_path / "ghost")
    with pytest.raises(MakakooNotDetected):
        detection.require_home()


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_for_brain_strips_known_secrets():
    secret = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    out = redact_for_brain(secret)
    assert "abcdefghijklmnop" not in out
    assert "Bearer <redacted>" in out


def test_redact_for_brain_handles_none_safely():
    assert redact_for_brain(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# query_brain
# ---------------------------------------------------------------------------


def test_query_brain_calls_search_with_query(tmp_path):
    bin_path = _write_stub_makakoo(
        tmp_path,
        stdout="- Decision 2026-04-12: token rotation policy\n",
    )
    env = {ENV_BIN: str(bin_path), "PATH": str(tmp_path)}
    out = query_brain("token rotation", env=env)
    assert "Decision 2026-04-12" in out


def test_query_brain_redacts_search_output(tmp_path):
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    bin_path = _write_stub_makakoo(tmp_path, stdout=f"- leaks {secret}\n")
    env = {ENV_BIN: str(bin_path), "PATH": str(tmp_path)}
    out = query_brain("anything", env=env)
    assert "abcdefghijklmnop" not in out
    assert "Bearer <redacted>" in out


def test_query_brain_returns_empty_string_when_no_matches(tmp_path):
    bin_path = _write_stub_makakoo(tmp_path, stdout="")
    env = {ENV_BIN: str(bin_path), "PATH": str(tmp_path)}
    assert query_brain("missing topic", env=env) == ""


def test_query_brain_trims_to_budget(tmp_path):
    big = ("- bullet line content\n" * 500)  # ~10 KB
    bin_path = _write_stub_makakoo(tmp_path, stdout=big)
    env = {ENV_BIN: str(bin_path), "PATH": str(tmp_path)}
    out = query_brain("verbose", env=env, budget_tokens=100)
    assert "[…truncated to fit context budget]" in out
    assert len(out) <= 100 * APPROX_CHARS_PER_TOKEN + 200  # allow truncation suffix


def test_query_brain_raises_on_non_zero_exit(tmp_path):
    bin_path = _write_stub_makakoo(
        tmp_path, stdout="", stderr="boom: db locked", exit_code=2
    )
    env = {ENV_BIN: str(bin_path), "PATH": str(tmp_path)}
    with pytest.raises(BrainQueryError) as exc:
        query_brain("anything", env=env)
    assert "db locked" in str(exc.value)


def test_query_brain_raises_when_makakoo_missing(tmp_path):
    env = {"PATH": str(tmp_path)}  # no makakoo on PATH
    with pytest.raises(MakakooNotDetected):
        query_brain("anything", env=env)


def test_query_brain_rejects_empty_query(tmp_path):
    bin_path = _write_stub_makakoo(tmp_path)
    env = {ENV_BIN: str(bin_path)}
    with pytest.raises(ValueError):
        query_brain("", env=env)


# ---------------------------------------------------------------------------
# build_context_block
# ---------------------------------------------------------------------------


def test_build_context_block_wraps_with_markers():
    block = build_context_block("auth", "- decision X\n- decision Y")
    assert block.startswith("<<< Makakoo Brain context")
    assert "End Makakoo Brain context" in block
    assert "decision X" in block


def test_build_context_block_handles_empty_brain_text():
    block = build_context_block("auth", "")
    assert "(no Makakoo Brain matches" in block


def test_build_context_block_redacts_query_and_body():
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    block = build_context_block(f"check {secret}", f"- prior {secret}")
    assert "abcdefghijklmnop" not in block


# ---------------------------------------------------------------------------
# write_brain_journal
# ---------------------------------------------------------------------------


def test_write_brain_journal_creates_today_file_with_bullet(tmp_home):
    env = {ENV_HOME: str(tmp_home), ENV_BIN: "/usr/bin/true"}
    # Force detection.available by passing a real binary path.
    target = write_brain_journal(
        "[[Lope]] consensus review of `auth.py`: 4 merged",
        env=env,
        now=_dt.datetime(2026, 4, 27, 0, 0, 0),
    )
    assert target.name == "2026_04_27.md"
    contents = target.read_text()
    assert "- [[Lope]]" in contents
    assert "[[Lope]]" in contents


def test_write_brain_journal_appends_without_overwriting(tmp_home):
    env = {ENV_HOME: str(tmp_home), ENV_BIN: "/usr/bin/true"}
    write_brain_journal("first entry", env=env, now=_dt.datetime(2026, 4, 27))
    target = write_brain_journal(
        "[[Lope]] second entry", env=env, now=_dt.datetime(2026, 4, 27)
    )
    contents = target.read_text()
    assert "first entry" in contents
    assert "second entry" in contents


def test_write_brain_journal_forces_outliner_prefix(tmp_home):
    env = {ENV_HOME: str(tmp_home), ENV_BIN: "/usr/bin/true"}
    target = write_brain_journal(
        "raw line without bullet",
        env=env,
        now=_dt.datetime(2026, 4, 27),
    )
    contents = target.read_text()
    assert "- raw line without bullet" in contents


def test_write_brain_journal_redacts_secrets(tmp_home):
    env = {ENV_HOME: str(tmp_home), ENV_BIN: "/usr/bin/true"}
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    target = write_brain_journal(
        f"- leaks {secret}", env=env, now=_dt.datetime(2026, 4, 27)
    )
    contents = target.read_text()
    assert "abcdefghijklmnop" not in contents


def test_write_brain_journal_requires_home(tmp_path):
    env = {ENV_BIN: "/usr/bin/true"}
    with pytest.raises(MakakooNotDetected):
        write_brain_journal("anything", env=env)


def test_write_brain_journal_rejects_empty_markdown(tmp_home):
    env = {ENV_HOME: str(tmp_home), ENV_BIN: "/usr/bin/true"}
    with pytest.raises(ValueError):
        write_brain_journal("", env=env)


# ---------------------------------------------------------------------------
# write_auto_memory
# ---------------------------------------------------------------------------


def test_write_auto_memory_requires_env_opt_in(tmp_home):
    env = {ENV_HOME: str(tmp_home), ENV_BIN: "/usr/bin/true"}
    with pytest.raises(MakakooAutoMemoryDisabled):
        write_auto_memory("review-x", "- some lesson", env=env)


def test_write_auto_memory_writes_when_enabled(tmp_home):
    env = {
        ENV_HOME: str(tmp_home),
        ENV_BIN: "/usr/bin/true",
        ENV_AUTOMEMORY: "1",
    }
    target = write_auto_memory("review-auth", "- avoid raw bearer logs", env=env)
    assert target.name == "lope-review-auth.md"
    assert target.read_text().startswith("- avoid raw bearer logs")


def test_write_auto_memory_redacts_secrets(tmp_home):
    env = {
        ENV_HOME: str(tmp_home),
        ENV_BIN: "/usr/bin/true",
        ENV_AUTOMEMORY: "1",
    }
    target = write_auto_memory(
        "review-auth",
        "- token: Bearer abcdefghijklmnopqrstuvwxyz123456",
        env=env,
    )
    assert "abcdefghijklmnop" not in target.read_text()


def test_write_auto_memory_sanitizes_filename(tmp_home):
    env = {
        ENV_HOME: str(tmp_home),
        ENV_BIN: "/usr/bin/true",
        ENV_AUTOMEMORY: "yes",
    }
    target = write_auto_memory(
        "../../etc/passwd",
        "- test",
        env=env,
    )
    # path traversal sanitized; the slug starts with the safe characters only
    assert target.name == "lope-etc-passwd.md"
    assert target.parent == tmp_home / "data" / "auto-memory"


def test_write_auto_memory_rejects_empty_name(tmp_home):
    env = {
        ENV_HOME: str(tmp_home),
        ENV_BIN: "/usr/bin/true",
        ENV_AUTOMEMORY: "1",
    }
    with pytest.raises(ValueError):
        write_auto_memory("---", "- body", env=env)


# ---------------------------------------------------------------------------
# format_review_journal_line
# ---------------------------------------------------------------------------


def test_format_review_journal_line_includes_wikilinks_and_top_finding():
    line = format_review_journal_line(
        target_path="auth.py",
        merged_count=4,
        confirmed_count=1,
        top_finding={
            "file": "auth.py",
            "line": 42,
            "agreement": "3/3 validators",
            "score": 0.86,
            "message": "Missing rate limiting",
        },
        memory_hash="abc123",
    )
    assert "[[Lope]]" in line
    assert "[[Makakoo OS]]" in line
    assert "auth.py:42" in line
    assert "score 0.86" in line
    assert "lope:abc123" in line


def test_format_review_journal_line_handles_missing_top_finding():
    line = format_review_journal_line(
        target_path="auth.py",
        merged_count=0,
        confirmed_count=0,
    )
    assert "[[Lope]]" in line
    assert "0 merged" in line
