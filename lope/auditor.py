"""
Auditor — post-mortem scorecard + journal writer for Lope.

After a PhaseExecutor finishes (or escalates), pass the ExecutionReport
through the Auditor to:

  1. Render a detailed scorecard for stdout/CLI consumption
  2. Append a bullet-point entry to a journal file so the sprint outcome
     is persisted for later review

Kept trivial: no YAML, no API calls, no side-effects beyond appending
to one file. Safe to run in tests — journal_dir is injectable.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import List, Optional

from .models import (
    ExecutionReport,
    PhaseVerdict,
    VerdictStatus,
)

log = logging.getLogger("lope.auditor")


class Auditor:
    """Scorecard + journal writer for a completed sprint run."""

    def __init__(self, journal_dir: Optional[str] = None):
        # Journal lives under LOPE_HOME/journal/ by default
        if journal_dir is None:
            lope_home = os.environ.get(
                "LOPE_HOME", os.path.expanduser("~/.lope")
            )
            journal_dir = os.path.join(lope_home, "journal")
        self._journal_dir = Path(journal_dir)

    # ─── Scorecard ──────────────────────────────────────────────

    def scorecard(self, report: ExecutionReport) -> str:
        """Render a detailed scorecard — more verbose than ExecutionReport.scorecard.

        Layout:
            Sprint: <title>
            Phases: N  (PASS=x, NEEDS_FIX=y, FAIL=z)
            Total duration: X.Xs
            Avg confidence: 0.XX
            ---
            P1 <name>: <status> conf=<c> dur=<d>s
              files: ...
              tests: ...
            ---
            Overall: OK | ESCALATED
            Error: <error>  (if any)
        """
        doc = report.sprint_doc
        lines: List[str] = []
        lines.append(f"Sprint: {doc.title}")
        total_phases = len(doc.phases)
        pass_count = report.count(VerdictStatus.PASS)
        fix_count = report.count(VerdictStatus.NEEDS_FIX)
        fail_count = report.count(VerdictStatus.FAIL)
        infra_count = report.count(VerdictStatus.INFRA_ERROR)
        lines.append(
            f"Phases: {total_phases}  "
            f"(PASS={pass_count}, NEEDS_FIX={fix_count}, "
            f"FAIL={fail_count}, INFRA={infra_count})"
        )
        lines.append(f"Total duration: {report.total_duration_seconds:.1f}s")
        lines.append(f"Avg confidence: {report.confidence_average():.2f}")
        lines.append("---")

        for phase in sorted(doc.phases, key=lambda p: p.index):
            v = phase.verdict
            if v is None:
                lines.append(f"P{phase.index} {phase.name}: (not run)")
                continue
            lines.append(
                f"P{phase.index} {phase.name}: {v.status.value} "
                f"conf={v.confidence:.2f} dur={v.duration_seconds:.0f}s"
            )
            if phase.files:
                lines.append(f"  files: {', '.join(phase.files)}")
            if phase.tests:
                lines.append(f"  tests: {', '.join(phase.tests)}")
            if v.rationale:
                rat = v.rationale.splitlines()[0].strip()[:160]
                lines.append(f"  rationale: {rat}")
            if v.required_fixes:
                lines.append(f"  fixes: {len(v.required_fixes)} item(s)")

        lines.append("---")
        lines.append("Overall: " + ("OK" if report.ok else "ESCALATED"))
        if report.error:
            lines.append(f"Error: {report.error}")
        return "\n".join(lines)

    # ─── Journal writer ─────────────────────────────────────────

    def write_journal(
        self,
        report: ExecutionReport,
        date: Optional[dt.date] = None,
    ) -> Path:
        """Append bullet-point summary to today's Brain journal.

        Creates the file if missing (with no pre-existing bullets), appends
        otherwise. Returns the Path that was written. Idempotent in the
        sense that calling twice will append twice — dedupe is the caller's
        responsibility (real sprints only run once).
        """
        when = date or dt.date.today()
        filename = f"{when.year:04d}_{when.month:02d}_{when.day:02d}.md"
        path = self._journal_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        bullets = self._render_journal_bullets(report)
        existed = path.exists()
        with path.open("a") as f:
            # If the file had content before and didn't end in a newline,
            # ensure we start on a new line
            if existed:
                f.write("\n" if not _file_ends_with_newline(path) else "")
            f.write("\n".join(bullets))
            f.write("\n")
        log.info(f"[auditor] wrote {len(bullets)} bullet(s) to {path}")
        return path

    def _render_journal_bullets(self, report: ExecutionReport) -> List[str]:
        doc = report.sprint_doc
        title_tag = doc.title.replace(" ", "-")
        pass_count = report.count(VerdictStatus.PASS)
        total = len(doc.phases)

        bullets: List[str] = []
        status_word = "COMPLETE" if report.ok else "ESCALATED"
        header = (
            f"- **{doc.title} {status_word}** — "
            f"{pass_count}/{total} phases PASS, "
            f"avg confidence {report.confidence_average():.2f}, "
            f"{report.total_duration_seconds:.0f}s total. "
            f"[[{title_tag}]] [[lope]] [[sprint-{status_word.lower()}]]"
        )
        bullets.append(header)

        for phase in sorted(doc.phases, key=lambda p: p.index):
            v = phase.verdict
            if v is None:
                bullets.append(
                    f"  - P{phase.index} {phase.name}: (not run)"
                )
                continue
            line = (
                f"  - P{phase.index} {phase.name}: {v.status.value} "
                f"conf={v.confidence:.2f} {v.duration_seconds:.0f}s"
            )
            bullets.append(line)

        if report.error:
            bullets.append(f"  - Escalation: {report.error[:200]}")

        return bullets


# ─── Helpers ─────────────────────────────────────────────────────


def _file_ends_with_newline(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return True
            f.seek(-1, os.SEEK_END)
            return f.read(1) == b"\n"
    except OSError:
        return True
