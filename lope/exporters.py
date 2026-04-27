"""Ecosystem exporters for Lope (v0.7).

Thin wrappers around the Phase 3 review renderers so callers can address
exports through one consistent module instead of stitching together
``lope.review`` and ``lope.sarif`` themselves.

Stdlib only.
"""

from __future__ import annotations

from typing import Any


def export_markdown_pr(report: Any, *, include_raw: bool = False) -> str:
    """Render a consensus :class:`~lope.review.ReviewReport` as a PR comment.

    Thin wrapper around :func:`lope.review.render_report` so callers
    have a single entry point for "I want every export shape lope can
    produce". The Phase 3 renderer already redacts its output.
    """

    from .review import render_report

    return render_report(report, output_format="markdown-pr", include_raw=include_raw)


def export_sarif(report: Any) -> str:
    """Render a consensus report as SARIF v2.1.0 JSON."""

    from .review import render_report

    return render_report(report, output_format="sarif")


__all__ = [
    "export_markdown_pr",
    "export_sarif",
]
