"""Minimal SARIF v2.1.0 emitter for Lope consensus reviews.

SARIF (Static Analysis Results Interchange Format) is the lingua franca for
GitHub code-scanning, Azure DevOps, GitLab, and CI plugins. Lope uses it as
the export envelope so a `lope review --consensus --format sarif` artifact
can be uploaded to any of those systems without translation.

Stdlib only: this module hand-builds the document with `json.dumps`. We do
not validate against the full schema — only the shape that the major
consumers actually require.

Spec reference: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .findings import ConsensusFinding
from .redaction import redact_text


SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"
TOOL_INFORMATION_URI = "https://github.com/traylinx/lope"


# SARIF defines five canonical levels. Lope's severity vocabulary is mapped
# conservatively here: information loss (critical/high collapse to error,
# low/info collapse to note) is acceptable for CI consumption since the
# original severity is also stored under ``properties.severity``.
_SEVERITY_TO_LEVEL: Dict[str, str] = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


def severity_to_sarif_level(severity: str) -> str:
    """Map a Lope severity to a SARIF ``level`` string."""

    if not severity:
        return "note"
    return _SEVERITY_TO_LEVEL.get(severity.lower(), "note")


def rule_id_for(finding: ConsensusFinding) -> str:
    """Stable rule id of the form ``lope.<category>.<severity>``."""

    category = (finding.category or "uncategorized").strip().lower() or "uncategorized"
    severity = (finding.severity or "info").strip().lower() or "info"
    return f"lope.{category}.{severity}"


def _round(value: float, ndigits: int = 3) -> float:
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return 0.0


def _build_message(finding: ConsensusFinding) -> str:
    bits = [finding.message.strip()]
    bits.append(
        "consensus {score:.2f} · agreement {a}/{t}".format(
            score=finding.consensus_score,
            a=finding.agreement_count,
            t=finding.total_validators,
        )
    )
    if finding.detected_by:
        bits.append("detected_by: " + ", ".join(finding.detected_by))
    return redact_text(" · ".join(bit for bit in bits if bit))


def _build_location(finding: ConsensusFinding) -> Optional[Dict[str, Any]]:
    if not finding.file:
        return None
    physical: Dict[str, Any] = {
        "artifactLocation": {"uri": finding.file},
    }
    if finding.line is not None:
        region: Dict[str, Any] = {"startLine": int(finding.line)}
        if finding.end_line is not None:
            region["endLine"] = int(finding.end_line)
        physical["region"] = region
    return {"physicalLocation": physical}


def finding_to_result(finding: ConsensusFinding) -> Dict[str, Any]:
    """Convert one :class:`ConsensusFinding` into a SARIF ``result`` object."""

    result: Dict[str, Any] = {
        "ruleId": rule_id_for(finding),
        "level": severity_to_sarif_level(finding.severity),
        "message": {"text": _build_message(finding)},
        "properties": {
            "detected_by": list(finding.detected_by),
            "consensus_score": _round(finding.consensus_score),
            "agreement_ratio": _round(finding.agreement_ratio),
            "agreement_count": int(finding.agreement_count),
            "total_validators": int(finding.total_validators),
            "confidence": _round(finding.confidence_max),
            "severity": finding.severity,
            "category": finding.category,
            "consensus_level": finding.consensus_level.value,
            "dissenting": list(finding.dissenting),
        },
    }
    location = _build_location(finding)
    if location is not None:
        result["locations"] = [location]
    return result


def build_rules(findings: Sequence[ConsensusFinding]) -> List[Dict[str, Any]]:
    """Build the unique-by-ruleId SARIF ``rules`` array used in the run."""

    seen: Dict[str, Dict[str, Any]] = {}
    for f in findings:
        rid = rule_id_for(f)
        if rid in seen:
            continue
        seen[rid] = {
            "id": rid,
            "name": rid,
            "shortDescription": {"text": rid},
            "defaultConfiguration": {"level": severity_to_sarif_level(f.severity)},
            "properties": {
                "severity": f.severity,
                "category": f.category,
            },
        }
    # Sort for deterministic output.
    return [seen[k] for k in sorted(seen.keys())]


def build_sarif(
    findings: Sequence[ConsensusFinding],
    *,
    tool_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a SARIF document covering ``findings`` as a single run."""

    version = tool_version or __version__
    rules = build_rules(findings)
    results = [finding_to_result(f) for f in findings]
    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "lope",
                        "informationUri": TOOL_INFORMATION_URI,
                        "version": version,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def dumps(
    findings: Sequence[ConsensusFinding],
    *,
    tool_version: Optional[str] = None,
    indent: int = 2,
) -> str:
    """Serialize a SARIF document for ``findings`` as a JSON string."""

    document = build_sarif(findings, tool_version=tool_version)
    return json.dumps(document, indent=indent, sort_keys=True)


__all__ = [
    "SARIF_SCHEMA",
    "SARIF_VERSION",
    "build_rules",
    "build_sarif",
    "dumps",
    "finding_to_result",
    "rule_id_for",
    "severity_to_sarif_level",
]
