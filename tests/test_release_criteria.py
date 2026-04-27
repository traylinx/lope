"""Phase 9 release criteria — hermetic smoke tests for v0.7.0.

Every criterion in the v0.7 sprint's "Phase 9" section has a test here
so a future bump does not silently regress what the release was
supposed to deliver. Stub validators mean these tests never call a
real CLI; the tests assert that each criterion's command path is
reachable, returns the expected shape, and stays JSON-valid where
applicable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_AUTH = REPO_ROOT / "tests" / "fixtures" / "sample_auth.py"
SAMPLE_SCENARIO = REPO_ROOT / "tests" / "fixtures" / "scenario.md"


# ---------------------------------------------------------------------------
# Version sync
# ---------------------------------------------------------------------------


def test_version_strings_in_sync_at_070():
    from lope import __version__

    assert __version__ == "0.7.0"


def test_check_version_script_passes():
    proc = subprocess.run(
        [str(REPO_ROOT / "scripts" / "check-version.sh")],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0.7.0" in proc.stdout


# ---------------------------------------------------------------------------
# Required artifacts present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relpath",
    [
        "docs/ci.md",
        "docs/makakoo.md",
        "docs/deliberation.md",
        "docs/memory.md",
        "docs/reference.md",
        "docs/ARCHITECTURE.md",
        "skills/lope-memory/SKILL.md",
        "skills/lope-deliberate/SKILL.md",
        "tests/fixtures/sample_auth.py",
        "tests/fixtures/scenario.md",
    ],
)
def test_required_artifact_exists(relpath):
    path = REPO_ROOT / relpath
    assert path.is_file(), f"missing release artifact: {relpath}"


def test_changelog_lists_v070_at_top():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = text.split("\n", 4)
    assert "0.7.0" in "\n".join(head[:4])


def test_pyproject_has_no_new_dependency():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # The contract: zero runtime dependencies.
    assert 'dependencies = []' in text


def test_install_summary_lists_new_skills():
    text = (REPO_ROOT / "install").read_text(encoding="utf-8")
    assert "/lope-memory" in text
    assert "/lope-deliberate" in text


# ---------------------------------------------------------------------------
# CLI smoke tests with stub validators
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_pool(monkeypatch):
    """Patch ``_fanout_generate`` so review commands never touch real CLIs."""

    sample_response = (
        "- [HIGH] sample_auth.py:13 — Missing rate limiting on login "
        "(confidence: 0.86)\n"
        "- [MEDIUM] sample_auth.py:21 — Token never expires "
        "(confidence: 0.62)\n"
    )

    def fake_fanout(pool, prompt, timeout):
        return [
            ("claude", sample_response, None),
            ("gemini", sample_response, None),
        ]

    import lope.cli as cli_module

    monkeypatch.setattr(cli_module, "_fanout_generate", fake_fanout)
    return fake_fanout


def _run_main(monkeypatch, *argv):
    """Invoke ``lope.cli.main()`` with ``argv`` and capture exit code."""
    import lope.cli as cli_module

    monkeypatch.setattr(sys, "argv", ["lope", *argv])
    try:
        cli_module.main()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)


def test_review_consensus_json_emits_machine_readable_payload(
    monkeypatch, capsys, stub_pool
):
    code = _run_main(
        monkeypatch,
        "review",
        str(SAMPLE_AUTH),
        "--consensus",
        "--format",
        "json",
        "--validators",
        "claude,gemini",
    )
    assert code == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert "findings" in parsed
    # The pool may resolve ``gemini`` to its registered name (e.g.
    # ``gemini-cli``); only the ``claude`` slot is guaranteed verbatim.
    assert "claude" in parsed["validators"]
    assert len(parsed["validators"]) == 2


def test_review_consensus_sarif_emits_valid_envelope(
    monkeypatch, capsys, stub_pool
):
    code = _run_main(
        monkeypatch,
        "review",
        str(SAMPLE_AUTH),
        "--consensus",
        "--format",
        "sarif",
        "--validators",
        "claude,gemini",
    )
    assert code == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["version"] == "2.1.0"
    assert parsed["runs"][0]["tool"]["driver"]["name"] == "lope"


def test_memory_stats_works_against_empty_db(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("LOPE_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.delenv("LOPE_MEMORY", raising=False)
    code = _run_main(monkeypatch, "memory", "stats")
    assert code == 0
    captured = capsys.readouterr()
    assert "Findings (total)" in captured.out
    assert "0" in captured.out


def test_brain_context_outside_makakoo_exits_2_with_actionable_error(
    monkeypatch, capsys, tmp_path
):
    # Force MAKAKOO undetectable: empty PATH, no MAKAKOO_BIN.
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("MAKAKOO_BIN", raising=False)
    monkeypatch.delenv("MAKAKOO_HOME", raising=False)
    code = _run_main(
        monkeypatch,
        "ask",
        "smoke",
        "--brain-context",
        "irrelevant",
        "--validators",
        "claude",
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "Makakoo not detected" in captured.err


def test_memory_disabled_env_makes_memory_verb_exit_nonzero(
    monkeypatch, capsys
):
    # Phase 5 chose exit 1 (generic error) for the disabled-memory path
    # vs. exit 2 (usage error) for the Makakoo-not-detected path. Lock
    # both behaviors so a refactor cannot silently flip either.
    monkeypatch.setenv("LOPE_MEMORY", "off")
    code = _run_main(monkeypatch, "memory", "stats")
    captured = capsys.readouterr()
    assert code != 0
    assert "disabled" in captured.err.lower()


def test_review_rejects_divide_plus_roles_combination(
    monkeypatch, capsys, stub_pool
):
    code = _run_main(
        monkeypatch,
        "review",
        str(SAMPLE_AUTH),
        "--consensus",
        "--divide",
        "files",
        "--roles",
        "security",
        "--validators",
        "claude",
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "cannot be combined" in captured.err


# ---------------------------------------------------------------------------
# Deliberation
# ---------------------------------------------------------------------------


def test_deliberation_quick_runs_with_stub_generate(tmp_path):
    """Direct unit test on the orchestrator (CLI smoke covered above)."""
    from lope.deliberation import get_template, run_deliberation

    def gen(name, prompt, timeout):
        if "Required section headings" in prompt:
            return (
                "## Context\nC\n## Decision\nadopt JWT\n"
                "## Consequences\nQ\n## Alternatives Considered\nA"
            )
        if "VERDICT" in prompt:
            return "VERDICT: PASS\nSEVERITY: low\n- nit"
        return f"position from {name}"

    out_dir = tmp_path / "lope-runs" / "20260427-adr"
    run = run_deliberation(
        template=get_template("adr"),
        scenario=SAMPLE_SCENARIO.read_text(encoding="utf-8"),
        validators=["claude", "gemini"],
        primary="claude",
        generate=gen,
        depth="quick",
        output_dir=out_dir,
    )
    assert (out_dir / "final" / "report.md").exists()
    assert (out_dir / "final" / "minority-report.md").exists()
    assert (out_dir / "final" / "decision-log.md").exists()
    assert (out_dir / "trace.jsonl").exists()
    assert run.depth == "quick"


# ---------------------------------------------------------------------------
# Skills coverage
# ---------------------------------------------------------------------------


def test_using_lope_skill_advertises_v07_modes():
    text = (REPO_ROOT / "skills" / "using-lope" / "SKILL.md").read_text(encoding="utf-8")
    for needle in (
        "lope memory",
        "lope deliberate",
        "--consensus",
        "--synth",
        "--brain-context",
        "--divide",
        "--roles",
        "--export agtx",
    ):
        assert needle in text, f"missing trigger in using-lope SKILL.md: {needle}"


def test_review_skill_documents_v07_flags():
    text = (REPO_ROOT / "skills" / "lope-review" / "SKILL.md").read_text(encoding="utf-8")
    for needle in ("--consensus", "--synth", "--remember", "--divide", "--roles"):
        assert needle in text, f"missing flag doc in lope-review SKILL.md: {needle}"
