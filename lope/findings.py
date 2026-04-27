"""Structured findings for Lope — parser, dedup, and consensus scoring.

This module is the v0.7 core for turning raw validator critique prose into
ranked, deduplicated, evidence-tracked findings the rest of Lope can act on.
It is stdlib only; no IO, no persistence, no command surface — just pure
data transformations so review/synth/memory/exporters/deliberation can share
one consistent finding model.

The shapes are intentionally split into three layers:

* :class:`Finding` is one validator's claim about one issue.
* :class:`MergedFinding` is the deduplicated cross-validator view.
* :class:`ConsensusFinding` adds agreement metrics and a consensus level.

Parsing accepts structured bullets, severity-prefix lines, category-prefix
lines, JSON arrays, and a permissive bullet fallback. All durable text passes
through :func:`lope.redaction.redact_text` before storage so secrets never
reach memory, exports, or formatted output.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .redaction import redact_text


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

SEVERITY_RANK: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}

# Maps any incoming severity word to one of the canonical severities above.
# Keys are lowercase. "warning" → medium and "nit" → low are explicit per the
# v0.7 sprint contract.
SEVERITY_SYNONYMS: Dict[str, str] = {
    "blocker": "critical",
    "critical": "critical",
    "severe": "critical",
    "fatal": "critical",
    "high": "high",
    "error": "high",
    "major": "high",
    "important": "high",
    "warning": "medium",
    "warn": "medium",
    "medium": "medium",
    "moderate": "medium",
    "low": "low",
    "minor": "low",
    "nit": "low",
    "nitpick": "low",
    "info": "info",
    "informational": "info",
    "note": "info",
    "notice": "info",
    "trivial": "info",
}

# Canonical category set. The right-hand side is what we store; left-hand
# values are accepted spellings.
CATEGORY_CANONICAL: Dict[str, str] = {
    "security": "security",
    "sec": "security",
    "auth": "security",
    "correctness": "correctness",
    "bug": "correctness",
    "logic": "correctness",
    "perf": "perf",
    "performance": "perf",
    "speed": "perf",
    "tests": "tests",
    "test": "tests",
    "testing": "tests",
    "ux": "ux",
    "design": "ux",
    "docs": "docs",
    "doc": "docs",
    "documentation": "docs",
    "ops": "ops",
    "operations": "ops",
    "ci": "ops",
    "infra": "ops",
}

# Severity family used by the message-only dedup rule; critical+high collapse
# into one group, info+low into another, medium stands alone.
SEVERITY_FAMILY: Dict[str, str] = {
    "critical": "high",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "low",
}

CONSENSUS_CRITICAL_OR_HIGH = frozenset({"critical", "high"})

EVIDENCE_LIMIT = 240


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ConsensusLevel(str, Enum):
    """Bucketed agreement rating for a merged finding."""

    CONFIRMED = "confirmed"
    LIKELY = "likely"
    NEEDS_VERIFICATION = "needs-verification"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"


@dataclass
class Finding:
    """One validator's claim about one issue."""

    message: str
    validator: str
    file: Optional[str] = None
    line: Optional[int] = None
    end_line: Optional[int] = None
    severity: str = "info"
    category: Optional[str] = None
    confidence: float = 0.5
    evidence: str = ""
    raw: str = ""

    def __post_init__(self) -> None:
        self.message = redact_text(self.message).strip()
        self.validator = (self.validator or "").strip() or "unknown"
        self.severity = _normalize_severity(self.severity)
        self.category = _normalize_category(self.category)
        self.confidence = _clamp_confidence(self.confidence)
        self.evidence = redact_text(self.evidence).strip()
        self.raw = redact_text(self.raw).strip()
        self.line = _coerce_int(self.line)
        self.end_line = _coerce_int(self.end_line)
        if self.file is not None:
            self.file = str(self.file).strip() or None

    @property
    def hash(self) -> str:
        key = _canonical_key(self.file, self.line, self.message)
        return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "validator": self.validator,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "severity": self.severity,
            "category": self.category,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "raw": self.raw,
        }


@dataclass
class MergedFinding:
    """A cross-validator view of one issue after deduplication.

    The instance is the canonical statement; per-validator detail (evidence
    quotes, confidence votes, raw lines) is preserved alongside it so
    formatters can show provenance without re-parsing.
    """

    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    end_line: Optional[int] = None
    severity: str = "info"
    category: Optional[str] = None
    detected_by: List[str] = field(default_factory=list)
    evidence: Dict[str, str] = field(default_factory=dict)
    confidences: List[float] = field(default_factory=list)
    raw_messages: List[str] = field(default_factory=list)

    @property
    def confidence_max(self) -> float:
        return max(self.confidences) if self.confidences else 0.0

    @property
    def confidence_avg(self) -> float:
        if not self.confidences:
            return 0.0
        return sum(self.confidences) / len(self.confidences)

    @property
    def hash(self) -> str:
        key = _canonical_key(self.file, self.line, self.message)
        return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "severity": self.severity,
            "category": self.category,
            "detected_by": list(self.detected_by),
            "evidence": dict(self.evidence),
            "confidence_max": round(self.confidence_max, 3),
            "confidence_avg": round(self.confidence_avg, 3),
        }


@dataclass
class ConsensusFinding:
    """Merged finding plus agreement metrics and a consensus level."""

    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    end_line: Optional[int] = None
    severity: str = "info"
    category: Optional[str] = None
    detected_by: List[str] = field(default_factory=list)
    evidence: Dict[str, str] = field(default_factory=dict)
    confidence_max: float = 0.0
    confidence_avg: float = 0.0
    agreement_count: int = 0
    total_validators: int = 0
    agreement_ratio: float = 0.0
    consensus_score: float = 0.0
    consensus_level: ConsensusLevel = ConsensusLevel.UNVERIFIED
    dissenting: List[str] = field(default_factory=list)

    @property
    def hash(self) -> str:
        key = _canonical_key(self.file, self.line, self.message)
        return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "severity": self.severity,
            "category": self.category,
            "detected_by": list(self.detected_by),
            "evidence": dict(self.evidence),
            "confidence_max": round(self.confidence_max, 3),
            "confidence_avg": round(self.confidence_avg, 3),
            "agreement_count": self.agreement_count,
            "total_validators": self.total_validators,
            "agreement_ratio": round(self.agreement_ratio, 3),
            "consensus_score": round(self.consensus_score, 3),
            "consensus_level": self.consensus_level.value,
            "dissenting": list(self.dissenting),
        }


@dataclass
class FindingParseResult:
    """Result of parsing one validator's response.

    Carries the parsed findings plus a hint about which extractor produced
    them so callers can report parse coverage in fallback messages.
    """

    findings: List[Finding] = field(default_factory=list)
    method: str = "empty"  # json | structured | fallback | mixed | empty
    parsed_count: int = 0
    fallback_used: bool = False
    raw_input: str = ""

    def __iter__(self):
        return iter(self.findings)

    def __len__(self) -> int:
        return len(self.findings)

    def __bool__(self) -> bool:
        return bool(self.findings)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_severity(value: Any) -> str:
    if value is None:
        return "info"
    key = str(value).strip().lower()
    if not key:
        return "info"
    return SEVERITY_SYNONYMS.get(key, "info")


def _normalize_category(value: Any) -> Optional[str]:
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return CATEGORY_CANONICAL.get(key, key)


def _clamp_confidence(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _normalize_message(message: str) -> str:
    """Lowercased, punctuation-stripped, single-spaced form for fuzzy matching."""
    if not message:
        return ""
    text = message.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _canonical_key(file: Optional[str], line: Optional[int], message: str) -> str:
    file_part = (file or "").strip().lower()
    line_part = "" if line is None else str(line)
    return f"{file_part}|{line_part}|{_normalize_message(message)}"


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_message(a), _normalize_message(b)).ratio()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_BULLET_PREFIX_RE = re.compile(r"^([\-\*•]\s+)")
_BRACKET_SEVERITY_RE = re.compile(r"^\[(?P<sev>[A-Za-z]+)\]\s+(?P<rest>.+)$")
_PREFIX_COLON_RE = re.compile(r"^(?P<word>[A-Za-z][A-Za-z\-_]*)\s*:\s+(?P<rest>.+)$")
_PREFIX_SPACE_RE = re.compile(r"^(?P<word>[A-Za-z][A-Za-z\-_]*)\s+(?P<rest>.+)$")
_LOCATION_AT_START_RE = re.compile(
    r"^(?P<file>(?:[\w\-]+/)*[\w\-]+\.[A-Za-z][A-Za-z0-9_]*)"
    r"(?::(?P<line>\d+))?"
    r"(?::(?P<end>\d+))?"
    r"(?P<sep>\s*[—–\-:]\s*|\s+)"
)
_CONFIDENCE_TAIL_RE = re.compile(
    r"\(\s*confidence\s*[:=]\s*(?P<conf>[0-9]*\.?[0-9]+)\s*\)\s*$",
    re.IGNORECASE,
)
_JSON_DECODER = json.JSONDecoder()


def parse_findings(
    text: str,
    validator_name: str,
    source_file: Optional[str] = None,
) -> FindingParseResult:
    """Parse a validator response into a :class:`FindingParseResult`.

    Tries JSON first, then per-line structured patterns, then a permissive
    bullet fallback. Output is always redaction-clean: secrets in raw lines
    are scrubbed before any finding is constructed.
    """

    if not text or not str(text).strip():
        return FindingParseResult(method="empty")

    redacted = redact_text(text)
    validator = (validator_name or "").strip() or "unknown"

    json_findings = _try_parse_json(redacted, validator, source_file)
    if json_findings:
        return FindingParseResult(
            findings=json_findings,
            method="json",
            parsed_count=len(json_findings),
            raw_input=redacted,
        )

    structured: List[Finding] = []
    fallback: List[Finding] = []

    for line in redacted.splitlines():
        if not line.strip():
            continue
        finding, kind = _parse_line(line, validator, source_file)
        if finding is None:
            continue
        if kind == "structured":
            structured.append(finding)
        else:
            fallback.append(finding)

    findings = structured + fallback
    if not findings:
        return FindingParseResult(method="empty", raw_input=redacted)

    if structured and fallback:
        method = "mixed"
    elif structured:
        method = "structured"
    else:
        method = "fallback"

    return FindingParseResult(
        findings=findings,
        method=method,
        parsed_count=len(findings),
        fallback_used=bool(fallback),
        raw_input=redacted,
    )


def _try_parse_json(
    text: str,
    validator: str,
    source_file: Optional[str],
) -> List[Finding]:
    candidates: List[Any] = []

    stripped = text.strip()
    try:
        candidates.append(json.loads(stripped))
    except (json.JSONDecodeError, ValueError):
        pass

    if not candidates:
        # Walk every "[" or "{" in document order and let the JSON decoder
        # tell us where the first valid object ends. This is robust against
        # prose that prefixes the payload with bracketed severity tags such
        # as ``- [HIGH] auth.py:42`` followed by a real JSON array.
        for index, char in enumerate(stripped):
            if char not in "[{":
                continue
            try:
                obj, _ = _JSON_DECODER.raw_decode(stripped[index:])
            except json.JSONDecodeError:
                continue
            candidates.append(obj)
            break

    for candidate in candidates:
        items: List[Any] = []
        if isinstance(candidate, list):
            items = candidate
        elif isinstance(candidate, dict):
            for key in ("findings", "results", "issues"):
                value = candidate.get(key)
                if isinstance(value, list):
                    items = value
                    break
        if not items:
            continue
        findings = [
            _finding_from_dict(item, validator, source_file)
            for item in items
            if isinstance(item, dict)
        ]
        findings = [f for f in findings if f is not None]
        if findings:
            return findings

    return []


def _finding_from_dict(
    item: Dict[str, Any],
    validator: str,
    source_file: Optional[str],
) -> Optional[Finding]:
    message = item.get("message") or item.get("title") or item.get("description") or ""
    if not message and item.get("text"):
        message = item.get("text")
    if not message:
        return None

    file = item.get("file") or item.get("path") or source_file
    line = item.get("line") or item.get("start_line") or item.get("startLine")
    end_line = item.get("end_line") or item.get("endLine")
    severity = item.get("severity") or item.get("level") or "info"
    category = item.get("category") or item.get("rule") or item.get("kind")
    confidence = item.get("confidence")
    if confidence is None:
        confidence = item.get("score", 0.5)
    evidence = item.get("evidence") or item.get("snippet") or ""

    try:
        raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        raw = str(item)

    return Finding(
        message=str(message),
        validator=validator,
        file=str(file) if file else None,
        line=line,
        end_line=end_line,
        severity=str(severity),
        category=str(category) if category else None,
        confidence=confidence if confidence is not None else 0.5,
        evidence=str(evidence),
        raw=raw,
    )


def _parse_line(
    line: str,
    validator: str,
    source_file: Optional[str],
) -> Tuple[Optional[Finding], str]:
    """Return (finding, kind) where kind ∈ {structured, fallback}."""

    stripped = line.strip()
    if not stripped:
        return None, ""

    body = stripped
    is_bullet = False
    bullet_match = _BULLET_PREFIX_RE.match(stripped)
    if bullet_match:
        is_bullet = True
        body = stripped[bullet_match.end():]

    bracket_match = _BRACKET_SEVERITY_RE.match(body)
    if bracket_match:
        sev_word = bracket_match.group("sev").lower()
        if sev_word in SEVERITY_SYNONYMS:
            finding = _build_finding(
                severity=sev_word,
                category=None,
                rest=bracket_match.group("rest"),
                validator=validator,
                raw=stripped,
                source_file=source_file,
                require_message=True,
            )
            if finding is not None:
                return finding, "structured"

    colon_match = _PREFIX_COLON_RE.match(body)
    if colon_match:
        word = colon_match.group("word").lower()
        rest = colon_match.group("rest")
        if word in SEVERITY_SYNONYMS:
            finding = _build_finding(
                severity=word,
                category=None,
                rest=rest,
                validator=validator,
                raw=stripped,
                source_file=source_file,
                require_message=True,
            )
            if finding is not None:
                return finding, "structured"
        elif word in CATEGORY_CANONICAL:
            finding = _build_finding(
                severity="info",
                category=word,
                rest=rest,
                validator=validator,
                raw=stripped,
                source_file=source_file,
                require_message=True,
            )
            if finding is not None:
                return finding, "structured"

    space_match = _PREFIX_SPACE_RE.match(body)
    if space_match:
        word = space_match.group("word").lower()
        rest = space_match.group("rest")
        if word in SEVERITY_SYNONYMS:
            finding = _build_finding(
                severity=word,
                category=None,
                rest=rest,
                validator=validator,
                raw=stripped,
                source_file=source_file,
                require_location=True,
            )
            if finding is not None:
                return finding, "structured"
        elif word in CATEGORY_CANONICAL:
            finding = _build_finding(
                severity="info",
                category=word,
                rest=rest,
                validator=validator,
                raw=stripped,
                source_file=source_file,
                require_location=True,
            )
            if finding is not None:
                return finding, "structured"

    if is_bullet:
        finding = _build_finding(
            severity="info",
            category=None,
            rest=body,
            validator=validator,
            raw=stripped,
            source_file=source_file,
            require_message=True,
        )
        if finding is not None:
            return finding, "fallback"

    return None, ""


def _build_finding(
    *,
    severity: str,
    category: Optional[str],
    rest: str,
    validator: str,
    raw: str,
    source_file: Optional[str],
    require_location: bool = False,
    require_message: bool = False,
) -> Optional[Finding]:
    rest = rest.strip()
    if not rest:
        return None

    confidence: Optional[float] = None
    conf_match = _CONFIDENCE_TAIL_RE.search(rest)
    if conf_match:
        try:
            confidence = float(conf_match.group("conf"))
        except ValueError:
            confidence = None
        rest = rest[: conf_match.start()].rstrip()
        if rest.endswith((",", ";")):
            rest = rest[:-1].rstrip()

    file: Optional[str] = None
    line: Optional[int] = None
    end_line: Optional[int] = None
    message = rest

    loc_match = _LOCATION_AT_START_RE.match(rest)
    if loc_match:
        file = loc_match.group("file")
        line = _coerce_int(loc_match.group("line"))
        end_line = _coerce_int(loc_match.group("end"))
        message = rest[loc_match.end():].strip()
        if not message:
            message = rest.strip()
            file = None
            line = None
            end_line = None

    if require_location and not file:
        return None
    if require_message and not message:
        return None

    if file is None and source_file:
        file = source_file

    evidence = raw[:EVIDENCE_LIMIT]

    return Finding(
        message=message,
        validator=validator,
        file=file,
        line=line,
        end_line=end_line,
        severity=severity,
        category=category,
        confidence=confidence if confidence is not None else 0.5,
        evidence=evidence,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def merge_findings(
    findings: Iterable[Finding],
    total_validators: int = 0,
    similarity_threshold: float = 0.85,
) -> List[MergedFinding]:
    """Merge raw findings into a deduplicated list.

    The three rules are applied in order: exact normalized key, same
    file/line ±2 with high paraphrase similarity, and message-only
    similarity within the same severity family.

    ``total_validators`` is reserved for callers that want consensus rules
    consistent across an empty fan-out; it is not used here directly but
    accepted for symmetry with :func:`score_consensus`.
    """

    del total_validators  # accepted for API symmetry, no current effect

    merged: List[MergedFinding] = []

    for finding in findings:
        if finding is None:
            continue
        idx = _find_merge_target(merged, finding, similarity_threshold)
        if idx is None:
            merged.append(_initialize_merged(finding))
        else:
            _merge_into(merged[idx], finding)

    merged.sort(
        key=lambda m: (
            -SEVERITY_RANK.get(m.severity, 0),
            m.file or "",
            m.line if m.line is not None else 10**9,
            m.message,
        )
    )
    return merged


def _initialize_merged(finding: Finding) -> MergedFinding:
    snippet = (finding.evidence or finding.raw or finding.message)[:EVIDENCE_LIMIT]
    return MergedFinding(
        message=finding.message,
        file=finding.file,
        line=finding.line,
        end_line=finding.end_line,
        severity=finding.severity,
        category=finding.category,
        detected_by=[finding.validator],
        evidence={finding.validator: snippet},
        confidences=[finding.confidence],
        raw_messages=[finding.message],
    )


def _merge_into(target: MergedFinding, finding: Finding) -> None:
    is_new_validator = finding.validator not in target.detected_by
    if is_new_validator:
        target.detected_by.append(finding.validator)

    target.confidences.append(finding.confidence)
    target.raw_messages.append(finding.message)

    if SEVERITY_RANK.get(finding.severity, 0) > SEVERITY_RANK.get(target.severity, 0):
        target.severity = finding.severity
        target.message = finding.message

    if target.category is None and finding.category is not None:
        target.category = finding.category

    if target.file is None and finding.file is not None:
        target.file = finding.file
    if target.line is None and finding.line is not None:
        target.line = finding.line
    if target.end_line is None and finding.end_line is not None:
        target.end_line = finding.end_line

    snippet = (finding.evidence or finding.raw or finding.message)[:EVIDENCE_LIMIT]
    if is_new_validator:
        target.evidence[finding.validator] = snippet
    else:
        existing = target.evidence.get(finding.validator, "")
        if len(snippet) > len(existing):
            target.evidence[finding.validator] = snippet


def _find_merge_target(
    merged: Sequence[MergedFinding],
    finding: Finding,
    similarity_threshold: float,
) -> Optional[int]:
    norm_msg = _normalize_message(finding.message)

    for idx, existing in enumerate(merged):
        # Rule 1: exact normalized key.
        if (
            (finding.file or "") == (existing.file or "")
            and finding.line == existing.line
            and norm_msg == _normalize_message(existing.message)
            and norm_msg
        ):
            return idx

        # Rule 2: same file, line within ±2, paraphrase similarity ≥ 0.78.
        if (
            finding.file
            and existing.file
            and finding.file == existing.file
            and finding.line is not None
            and existing.line is not None
            and abs(finding.line - existing.line) <= 2
            and _similarity(finding.message, existing.message) >= 0.78
        ):
            return idx

    # Rule 3: message-only similarity ≥ similarity_threshold (default 0.85,
    # relaxed to 0.90 in the spec for cross-file matches) within the same
    # severity family. Use the higher of the two thresholds as a guardrail.
    cross_file_threshold = max(similarity_threshold, 0.90)
    for idx, existing in enumerate(merged):
        if finding.file and existing.file and finding.file != existing.file:
            if _similarity(finding.message, existing.message) >= cross_file_threshold:
                if SEVERITY_FAMILY.get(finding.severity, "low") == SEVERITY_FAMILY.get(
                    existing.severity, "low"
                ):
                    return idx
            continue
        # Same file (or one side missing): looser threshold OK.
        if _similarity(finding.message, existing.message) >= similarity_threshold:
            if SEVERITY_FAMILY.get(finding.severity, "low") == SEVERITY_FAMILY.get(
                existing.severity, "low"
            ):
                return idx

    return None


# ---------------------------------------------------------------------------
# Consensus scoring
# ---------------------------------------------------------------------------


def score_consensus(
    merged: Sequence[MergedFinding],
    all_validator_names: Sequence[str],
) -> List[ConsensusFinding]:
    """Score merged findings against the full validator roster.

    ``all_validator_names`` is the list of validators that ran on the artifact
    (including any whose detection list is empty). The agreement ratio is
    computed against this denominator so a missed finding is treated as
    dissent rather than absence.
    """

    roster = list(dict.fromkeys(all_validator_names or []))
    total = max(len(roster), 1)
    results: List[ConsensusFinding] = []

    for entry in merged:
        agreement_count = len(entry.detected_by)
        agreement_ratio = agreement_count / total
        confidence_max = entry.confidence_max
        consensus_score = agreement_ratio * confidence_max
        level = _classify_consensus_level(
            agreement_ratio=agreement_ratio,
            confidence=confidence_max,
            severity=entry.severity,
        )
        dissenting = sorted(set(roster) - set(entry.detected_by))

        results.append(
            ConsensusFinding(
                message=entry.message,
                file=entry.file,
                line=entry.line,
                end_line=entry.end_line,
                severity=entry.severity,
                category=entry.category,
                detected_by=list(entry.detected_by),
                evidence=dict(entry.evidence),
                confidence_max=confidence_max,
                confidence_avg=entry.confidence_avg,
                agreement_count=agreement_count,
                total_validators=total,
                agreement_ratio=agreement_ratio,
                consensus_score=consensus_score,
                consensus_level=level,
                dissenting=dissenting,
            )
        )

    results.sort(
        key=lambda r: (
            -SEVERITY_RANK.get(r.severity, 0),
            -r.consensus_score,
            r.file or "",
            r.line if r.line is not None else 10**9,
            r.message,
        )
    )
    return results


def _classify_consensus_level(
    *,
    agreement_ratio: float,
    confidence: float,
    severity: str,
) -> ConsensusLevel:
    # ``DISPUTED`` is intentionally not produced here in v0.7. The sprint
    # contract reserves that level for a future phase that parses explicit
    # negation between validator outputs. Until that lands, we keep the enum
    # value, heading mapping, and ``_LEVEL_ORDER`` slot wired so the upgrade
    # can flip on without touching every formatter and consumer.
    if agreement_ratio >= 0.60 and confidence >= 0.65:
        return ConsensusLevel.CONFIRMED
    if agreement_ratio >= 0.40 and confidence >= 0.55:
        return ConsensusLevel.LIKELY
    if agreement_ratio >= 0.20 or severity in CONSENSUS_CRITICAL_OR_HIGH:
        return ConsensusLevel.NEEDS_VERIFICATION
    return ConsensusLevel.UNVERIFIED


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


_LEVEL_HEADINGS: Dict[ConsensusLevel, str] = {
    ConsensusLevel.CONFIRMED: "CONFIRMED",
    ConsensusLevel.LIKELY: "LIKELY",
    ConsensusLevel.NEEDS_VERIFICATION: "NEEDS VERIFICATION",
    ConsensusLevel.DISPUTED: "DISPUTED",
    ConsensusLevel.UNVERIFIED: "UNVERIFIED",
}

_LEVEL_ORDER: Tuple[ConsensusLevel, ...] = (
    ConsensusLevel.CONFIRMED,
    ConsensusLevel.LIKELY,
    ConsensusLevel.NEEDS_VERIFICATION,
    ConsensusLevel.DISPUTED,
    ConsensusLevel.UNVERIFIED,
)


def format_consensus_markdown(
    scored: Sequence[ConsensusFinding],
    *,
    title: Optional[str] = None,
) -> str:
    """Render a redaction-clean consensus report as Markdown."""

    if not scored:
        return "No consensus findings.\n"

    by_level: Dict[ConsensusLevel, List[ConsensusFinding]] = {}
    for entry in scored:
        by_level.setdefault(entry.consensus_level, []).append(entry)

    lines: List[str] = []
    if title:
        lines.append(f"# {title.strip()}")
        lines.append("")

    for level in _LEVEL_ORDER:
        items = by_level.get(level)
        if not items:
            continue
        lines.append(f"## {_LEVEL_HEADINGS[level]}")
        for item in items:
            location = ""
            if item.file:
                location = item.file
                if item.line is not None:
                    location += f":{item.line}"
                location = " " + location
            sev = item.severity.upper()
            lines.append(f"- [{sev}]{location} {item.message}".rstrip())
            lines.append(
                "  consensus: {score:.2f} · agreement: {a}/{t} · "
                "detected_by: {by}".format(
                    score=item.consensus_score,
                    a=item.agreement_count,
                    t=item.total_validators,
                    by=", ".join(item.detected_by) if item.detected_by else "—",
                )
            )
            if item.evidence:
                lines.append("  evidence:")
                for validator, quote in sorted(item.evidence.items()):
                    snippet = redact_text(quote).strip()
                    if not snippet:
                        continue
                    snippet = snippet.splitlines()[0][:160]
                    lines.append(f"    {validator}: {snippet}")
        lines.append("")

    rendered = "\n".join(lines).rstrip() + "\n"
    return redact_text(rendered)


__all__ = [
    "ConsensusFinding",
    "ConsensusLevel",
    "Finding",
    "FindingParseResult",
    "MergedFinding",
    "format_consensus_markdown",
    "merge_findings",
    "parse_findings",
    "score_consensus",
]
