"""Consensus review orchestration for ``lope review --consensus``.

This module is the v0.7 review-specific brain stem. It owns the path from a
file (or, in later phases, a directory or diff) to a fully scored
:class:`~lope.findings.ConsensusFinding` report, and renders the report in
whichever output format the caller asked for. Keeping this logic out of
``cli.py`` lets the consensus pipeline be unit-tested with monkeypatched
fan-out and reused by future verbs (memory, deliberation, divide/role).

Stdlib only: redaction, dedup, scoring, and SARIF emission already live in
sibling modules. The fan-out callable is injected so tests can swap in
deterministic stubs without touching the real validator subsystem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .findings import (
    ConsensusFinding,
    Finding,
    FindingParseResult,
    MergedFinding,
    format_consensus_markdown,
    merge_findings,
    parse_findings,
    score_consensus,
)
from .redaction import redact_text


FanoutResult = Tuple[str, str, Optional[str]]
FanoutFn = Callable[[Any, str, int], List[FanoutResult]]


SUPPORTED_FORMATS: Tuple[str, ...] = (
    "text",
    "json",
    "markdown",
    "markdown-pr",
    "sarif",
)


DEFAULT_FOCUS = (
    "Review this file. Identify bugs, code-smells, design issues, "
    "and concrete improvements. Be specific with line references."
)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class ReviewInput:
    """The raw materials a review run needs."""

    target: str
    content: str
    focus: str = ""
    source_label: Optional[str] = None


@dataclass
class ReviewReport:
    """Outcome of one consensus review run.

    ``raw_results`` is kept on the report so renderers can show per-validator
    output when ``--include-raw`` is set or when parsing falls back. All text
    in this object is redaction-clean — the orchestrator scrubs before any
    field is populated, so renderers do not need to redact again.
    """

    target: str
    focus: str
    validators: List[str]
    raw_results: List[Dict[str, Optional[str]]]
    parse_methods: Dict[str, str]
    findings: List[Finding]
    merged: List[MergedFinding]
    scored: List[ConsensusFinding]
    errors: List[Dict[str, str]]
    raw_count: int
    merged_count: int
    fallback: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "focus": self.focus,
            "validators": list(self.validators),
            "raw_count": self.raw_count,
            "merged_count": self.merged_count,
            "fallback": self.fallback,
            "errors": list(self.errors),
            "parse_methods": dict(self.parse_methods),
            "findings": [s.to_dict() for s in self.scored],
            "raw_results": list(self.raw_results),
        }


# ---------------------------------------------------------------------------
# Prompt + parsing helpers
# ---------------------------------------------------------------------------


def build_review_prompt(review: ReviewInput) -> str:
    """Build the validator prompt used by structured review."""

    focus = (review.focus or "").strip() or DEFAULT_FOCUS
    label = review.source_label or review.target
    return (
        f"{focus}\n\n"
        f"File: {label}\n"
        f"```\n{review.content}\n```\n\n"
        "Return your review as plain prose. No VERDICT block needed.\n"
        "When practical, format issues as bullet lines like\n"
        "`- [HIGH] <file>:<line> — <message> (confidence: 0.85)`\n"
        "so consensus tooling can dedupe and rank them."
    )


def parse_responses(
    raw_results: Sequence[FanoutResult],
    source_file: Optional[str] = None,
) -> Tuple[List[Finding], Dict[str, FindingParseResult], List[Dict[str, str]]]:
    """Run :func:`parse_findings` over every successful fan-out response."""

    findings: List[Finding] = []
    parse_results: Dict[str, FindingParseResult] = {}
    errors: List[Dict[str, str]] = []

    for name, answer, error in raw_results:
        if error:
            errors.append(
                {"validator": name, "error": redact_text(error).strip()}
            )
            continue
        # Defensive try/except: a parser regression should isolate to one
        # validator instead of taking out the whole review run. Lope's
        # core promise is that one bad CLI never blanks an ensemble.
        try:
            result = parse_findings(answer or "", name, source_file=source_file)
        except Exception as exc:  # pragma: no cover — defensive guard
            errors.append(
                {
                    "validator": name,
                    "error": redact_text(f"parse_findings raised {type(exc).__name__}: {exc}").strip(),
                }
            )
            continue
        parse_results[name] = result
        findings.extend(result.findings)

    return findings, parse_results, errors


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _default_fanout(pool: Any, prompt: str, timeout: int) -> List[FanoutResult]:
    """Lazy bridge to ``cli._fanout_generate`` to dodge circular imports."""

    from .cli import _fanout_generate

    return _fanout_generate(pool, prompt, timeout)


def run_consensus_review(
    *,
    target: str,
    content: str,
    validators: Sequence[str],
    focus: str = "",
    pool: Any = None,
    timeout: int = 120,
    similarity: float = 0.85,
    min_consensus: float = 0.0,
    fanout: Optional[FanoutFn] = None,
    source_label: Optional[str] = None,
) -> ReviewReport:
    """Run the consensus pipeline end-to-end and return a :class:`ReviewReport`.

    ``validators`` is the roster used for consensus scoring. It should be
    the list of validators the caller intends to fan out to, regardless of
    whether each one ultimately answered. Empty answers and errors still
    count toward dissent.
    """

    fanout_fn = fanout or _default_fanout

    review_input = ReviewInput(
        target=target,
        content=content,
        focus=focus,
        source_label=source_label or target,
    )
    prompt = build_review_prompt(review_input)

    raw = list(fanout_fn(pool, prompt, timeout))

    raw_results: List[Dict[str, Optional[str]]] = []
    for name, answer, error in raw:
        raw_results.append(
            {
                "validator": name,
                "answer": redact_text(answer or "").rstrip(),
                "error": redact_text(error).strip() if error else None,
            }
        )

    findings, parse_results, errors = parse_responses(raw, source_file=target)

    merged = merge_findings(findings, similarity_threshold=similarity)
    scored = score_consensus(merged, list(validators))

    if min_consensus > 0:
        scored = [s for s in scored if s.consensus_score >= min_consensus]

    parse_methods = {name: result.method for name, result in parse_results.items()}

    return ReviewReport(
        target=target,
        focus=(focus or "").strip() or DEFAULT_FOCUS,
        validators=list(validators),
        raw_results=raw_results,
        parse_methods=parse_methods,
        findings=findings,
        merged=merged,
        scored=scored,
        errors=errors,
        raw_count=len(findings),
        merged_count=len(merged),
        fallback=len(findings) == 0,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_report(
    report: ReviewReport,
    output_format: str = "text",
    *,
    include_raw: bool = False,
) -> str:
    """Render ``report`` in one of the supported formats."""

    fmt = (output_format or "text").lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unknown output format: {output_format!r}. "
            f"Choose one of: {', '.join(SUPPORTED_FORMATS)}"
        )

    if fmt == "json":
        return _render_json(report)
    if fmt == "sarif":
        return _render_sarif(report)
    if fmt == "markdown":
        return _render_markdown(report, include_raw=include_raw)
    if fmt == "markdown-pr":
        return _render_markdown_pr(report, include_raw=include_raw)
    return _render_text(report, include_raw=include_raw)


def _render_text(report: ReviewReport, *, include_raw: bool = False) -> str:
    lines: List[str] = []
    lines.append(f"Lope consensus review: {report.target}")
    lines.append(f"Validators: {', '.join(report.validators) or '—'}")
    lines.append(f"Findings: {report.raw_count} raw → {report.merged_count} merged")
    if report.errors:
        lines.append(f"Errors: {len(report.errors)} validator(s) failed")
    lines.append("")

    if report.fallback:
        lines.append(
            "No structured findings parsed; use --include-raw to inspect responses."
        )
        lines.append("")
        lines.extend(_format_raw_blocks(report.raw_results))
        return "\n".join(lines).rstrip() + "\n"

    body = format_consensus_markdown(report.scored).rstrip()
    lines.append(body)

    if include_raw and report.raw_results:
        lines.append("")
        lines.append("## Raw responses")
        lines.extend(_format_raw_blocks(report.raw_results))

    if report.errors:
        lines.append("")
        lines.append("## Errors")
        for entry in report.errors:
            lines.append(f"- {entry['validator']}: {entry['error']}")

    return "\n".join(lines).rstrip() + "\n"


def _format_raw_blocks(raw_results: Sequence[Dict[str, Optional[str]]]) -> List[str]:
    out: List[str] = []
    for entry in raw_results:
        name = entry.get("validator") or "?"
        out.append(f"━━━ {name} ━━━")
        if entry.get("error"):
            out.append(f"[ERROR] {entry['error']}")
        elif entry.get("answer"):
            out.append(str(entry["answer"]).rstrip())
        else:
            out.append("[empty response]")
        out.append("")
    return out


def _render_json(report: ReviewReport) -> str:
    payload = {
        "target": report.target,
        "focus": report.focus,
        "validators": list(report.validators),
        "raw_count": report.raw_count,
        "merged_count": report.merged_count,
        "fallback": report.fallback,
        "parse_methods": dict(report.parse_methods),
        "findings": [s.to_dict() for s in report.scored],
        "errors": list(report.errors),
        "raw_results": list(report.raw_results),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _render_sarif(report: ReviewReport) -> str:
    from . import sarif

    return sarif.dumps(report.scored) + "\n"


def _render_markdown(report: ReviewReport, *, include_raw: bool = False) -> str:
    lines = [
        f"# Lope consensus review: {report.target}",
        "",
        f"- **Validators:** {', '.join(report.validators) or '—'}",
        f"- **Focus:** {report.focus}",
        f"- **Findings:** {report.raw_count} raw → {report.merged_count} merged",
    ]
    if report.errors:
        lines.append(f"- **Errors:** {len(report.errors)}")
    lines.append("")

    if report.fallback:
        lines.append(
            "> No structured findings parsed; use `--include-raw` to inspect responses."
        )
        lines.append("")
        for entry in report.raw_results:
            name = entry.get("validator") or "?"
            lines.append(f"## {name}")
            if entry.get("error"):
                lines.append(f"`[ERROR]` {entry['error']}")
            elif entry.get("answer"):
                lines.append("```")
                lines.append(str(entry["answer"]).rstrip())
                lines.append("```")
            else:
                lines.append("_empty response_")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    lines.append(format_consensus_markdown(report.scored).rstrip())

    if include_raw and report.raw_results:
        lines.append("")
        lines.append("## Raw responses")
        for entry in report.raw_results:
            name = entry.get("validator") or "?"
            lines.append(f"### {name}")
            if entry.get("error"):
                lines.append(f"`[ERROR]` {entry['error']}")
            elif entry.get("answer"):
                lines.append("```")
                lines.append(str(entry["answer"]).rstrip())
                lines.append("```")
            else:
                lines.append("_empty response_")

    if report.errors:
        lines.append("")
        lines.append("## Validator errors")
        for entry in report.errors:
            lines.append(f"- **{entry['validator']}**: {entry['error']}")

    return "\n".join(lines).rstrip() + "\n"


def _render_markdown_pr(report: ReviewReport, *, include_raw: bool = False) -> str:
    """Compact GitHub PR comment body.

    PR comments stay terse by default; ``include_raw`` opts into appending
    a collapsible details block per validator. Without the flag, the
    fallback message tells reviewers how to surface raw responses locally.
    """

    if report.fallback:
        head = (
            "## 🔍 Lope consensus review\n\n"
            f"Target: `{report.target}`\n\n"
            f"**0 structured findings** across "
            f"{len(report.validators)} validators "
            f"({', '.join(report.validators) or '—'}).\n\n"
        )
        if include_raw:
            return head + _markdown_pr_raw_details(report)
        return head + (
            "_No structured findings parsed; raw responses suppressed in the PR view. "
            "Re-run locally with `--include-raw`._\n"
        )

    lines = [
        "## 🔍 Lope consensus review",
        "",
        f"Target: `{report.target}`",
        "",
        f"**{report.merged_count} merged findings** "
        f"({report.raw_count} raw) across "
        f"{len(report.validators)} validators "
        f"({', '.join(report.validators) or '—'})",
        "",
    ]

    if report.scored:
        lines.append("| Severity | Location | Issue | Consensus | Detected by |")
        lines.append("|---|---|---|---|---|")
        for f in report.scored:
            location = ""
            if f.file:
                location = f.file
                if f.line is not None:
                    location += f":{f.line}"
            location_cell = f"`{location}`" if location else "—"
            issue = redact_text(f.message).replace("|", r"\|").splitlines()[0][:120]
            detected_by = ", ".join(f.detected_by) or "—"
            lines.append(
                f"| {f.severity.upper()} | {location_cell} | {issue} | "
                f"{f.consensus_score:.2f} ({f.agreement_count}/{f.total_validators}) | "
                f"{detected_by} |"
            )

    if report.errors:
        lines.append("")
        names = ", ".join(e["validator"] for e in report.errors)
        lines.append(f"_{len(report.errors)} validator(s) errored: {names}_")

    if include_raw and report.raw_results:
        lines.append("")
        lines.append(_markdown_pr_raw_details(report).rstrip())

    return "\n".join(lines).rstrip() + "\n"


def _markdown_pr_raw_details(report: ReviewReport) -> str:
    """Collapsible per-validator detail blocks for PR comments."""

    chunks: List[str] = []
    for entry in report.raw_results:
        name = entry.get("validator") or "?"
        chunks.append(f"<details><summary>raw response — {name}</summary>\n")
        chunks.append("")
        if entry.get("error"):
            chunks.append(f"`[ERROR]` {entry['error']}")
        elif entry.get("answer"):
            chunks.append("```")
            chunks.append(str(entry["answer"]).rstrip())
            chunks.append("```")
        else:
            chunks.append("_empty response_")
        chunks.append("")
        chunks.append("</details>")
        chunks.append("")
    return "\n".join(chunks)


__all__ = [
    "DEFAULT_FOCUS",
    "FanoutFn",
    "ReviewInput",
    "ReviewReport",
    "SUPPORTED_FORMATS",
    "build_review_prompt",
    "parse_responses",
    "render_report",
    "run_consensus_review",
]
