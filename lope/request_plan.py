"""Deterministic byte-safe request admission and semantic chunk planning.

Token counts are advisory only. Every hard decision in this module uses UTF-8
bytes, declared transport capacity, configured call limits, and chunk count.
The planner is pure: it launches no validators and mutates no run state.
"""

from __future__ import annotations

import ast
import hashlib
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .processes import ARGV_POLICY_LIMIT
from .runtime import (
    DEFAULT_MAX_CHUNKS,
    DEFAULT_MAX_EXTERNAL_CALLS,
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MODEL_CALL_TIMEOUT_SECONDS,
    InputProfile,
)


SCHEMA_VERSION = 1
DEFAULT_DIRECT_PROMPT_BYTES = 96 * 1024
DEFAULT_CHUNK_BYTES = 64 * 1024
DEFAULT_STDIN_PROMPT_BYTES = 1024 * 1024
DEFAULT_FILE_PROMPT_BYTES = 1024 * 1024
DEFAULT_HTTP_PROMPT_BYTES = 1024 * 1024
PROMPT_WRAPPER_RESERVE_BYTES = 4 * 1024


class PlanAction(str, Enum):
    DIRECT = "direct"
    CHUNK = "chunk"
    REJECT = "reject"


@dataclass(frozen=True)
class SemanticUnit:
    label: str
    content: str
    start_line: int = 1
    end_line: int = 1
    kind: str = "text"


@dataclass(frozen=True)
class RequestChunk:
    index: int
    content: str
    labels: Tuple[str, ...]
    start_line: int
    end_line: int
    kind: str
    overlap_bytes: int = 0

    @property
    def profile(self) -> InputProfile:
        return InputProfile.from_text(self.content)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        joined = ", ".join(self.labels)
        return joined or f"chunk-{self.index + 1}"

    def to_dict(self, *, include_content: bool = False) -> Dict[str, Any]:
        profile = self.profile
        data: Dict[str, Any] = {
            "index": self.index,
            "label": self.label,
            "labels": list(self.labels),
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "utf8_bytes": profile.utf8_bytes,
            "lines": profile.lines,
            "estimated_tokens": profile.estimated_tokens,
            "estimated_tokens_label": "approximate (ceil UTF-8 bytes / 3)",
            "overlap_bytes": self.overlap_bytes,
            "sha256": self.digest,
        }
        if include_content:
            data["content"] = self.content
        return data


@dataclass(frozen=True)
class TransportPlan:
    validator: str
    transport: str
    max_prompt_bytes: int
    direct_safe: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validator": self.validator,
            "transport": self.transport,
            "max_prompt_bytes": self.max_prompt_bytes,
            "direct_safe": self.direct_safe,
        }


@dataclass
class RequestPlan:
    mode: str
    action: PlanAction
    reason: str
    profile: InputProfile
    transports: List[TransportPlan]
    chunks: List[RequestChunk] = field(default_factory=list)
    planned_calls: int = 0
    maximum_concurrency: int = 1
    nominal_wall_ceiling_seconds: float = 0.0
    max_chunks: int = DEFAULT_MAX_CHUNKS
    max_calls: int = DEFAULT_MAX_EXTERNAL_CALLS
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES
    required_chunks: int = 0
    required_calls: int = 0
    forecast_input_bytes: int = 0
    mitigation: str = ""
    policy: str = "auto"
    schema_version: int = SCHEMA_VERSION

    @property
    def accepted(self) -> bool:
        return self.action != PlanAction.REJECT

    def to_dict(self, *, include_content: bool = False) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "action": self.action.value,
            "reason": self.reason,
            "policy": self.policy,
            "input": {
                "utf8_bytes": self.profile.utf8_bytes,
                "lines": self.profile.lines,
                "estimated_tokens": self.profile.estimated_tokens,
                "estimated_tokens_label": "approximate (ceil UTF-8 bytes / 3)",
            },
            "transports": [item.to_dict() for item in self.transports],
            "chunks": [item.to_dict(include_content=include_content) for item in self.chunks],
            "required_chunks": self.required_chunks,
            "planned_calls": self.planned_calls,
            "required_calls": self.required_calls,
            "maximum_concurrency": self.maximum_concurrency,
            "nominal_wall_ceiling_seconds": self.nominal_wall_ceiling_seconds,
            "forecast_input_bytes": self.forecast_input_bytes,
            "limits": {
                "max_chunks": self.max_chunks,
                "max_calls": self.max_calls,
                "max_input_bytes": self.max_input_bytes,
            },
            "mitigation": self.mitigation or None,
        }


class RequestRejected(RuntimeError):
    def __init__(self, plan: RequestPlan):
        self.plan = plan
        super().__init__(plan.reason + (f"; {plan.mitigation}" if plan.mitigation else ""))


def _validator_name(validator: Any) -> str:
    if isinstance(validator, str):
        return validator
    if isinstance(validator, dict):
        return str(validator.get("name") or "validator")
    return str(getattr(validator, "name", "") or validator.__class__.__name__)


def transport_for_validator(validator: Any) -> TransportPlan:
    """Return declared transport safety without exposing prompt content."""

    name = _validator_name(validator)
    if isinstance(validator, dict):
        transport = str(validator.get("transport") or "stdin")
        cap = int(validator.get("max_prompt_bytes") or 0)
    else:
        class_name = validator.__class__.__name__ if not isinstance(validator, str) else ""
        if class_name in ("OpencodeValidator", "GeminiCliValidator"):
            transport = "argv"
        elif class_name == "AiderValidator":
            transport = "file"
        elif class_name == "GenericHttpValidator":
            transport = "http"
        elif class_name == "GenericSubprocessValidator":
            transport = "stdin" if bool(getattr(validator, "_stdin", False)) else "argv"
        else:
            transport = "stdin"
        cap = int(getattr(validator, "_max_prompt_bytes", 0) or 0)

    defaults = {
        "argv": ARGV_POLICY_LIMIT - PROMPT_WRAPPER_RESERVE_BYTES,
        "stdin": DEFAULT_STDIN_PROMPT_BYTES,
        "file": DEFAULT_FILE_PROMPT_BYTES,
        "http": DEFAULT_HTTP_PROMPT_BYTES,
    }
    if transport not in defaults:
        transport = "stdin"
    if cap <= 0:
        cap = defaults[transport]
    if transport == "argv":
        cap = min(cap, defaults["argv"])
    return TransportPlan(name, transport, cap, True)


def _line_end(start_line: int, text: str) -> int:
    count = text.count("\n") + (0 if text.endswith("\n") else 1)
    return start_line + max(1, count) - 1


def _units_from_boundaries(
    text: str,
    boundaries: Iterable[int],
    *,
    label: str,
    kind: str,
) -> List[SemanticUnit]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return [SemanticUnit(label, "", 1, 1, kind)]
    starts = sorted({max(1, min(int(value), len(lines))) for value in boundaries} | {1})
    units: List[SemanticUnit] = []
    for index, start in enumerate(starts):
        end_exclusive = starts[index + 1] if index + 1 < len(starts) else len(lines) + 1
        content = "".join(lines[start - 1:end_exclusive - 1])
        if content:
            units.append(
                SemanticUnit(
                    label=f"{label} lines {start}-{end_exclusive - 1}",
                    content=content,
                    start_line=start,
                    end_line=end_exclusive - 1,
                    kind=kind,
                )
            )
    return units


def _diff_units(text: str, label: str) -> List[SemanticUnit]:
    boundaries = []
    for index, line in enumerate(text.splitlines(keepends=True), start=1):
        if line.startswith("diff --git ") or line.startswith("@@ "):
            boundaries.append(index)
    return _units_from_boundaries(text, boundaries, label=label, kind="diff")


def _python_units(text: str, label: str) -> List[SemanticUnit]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    meaningful = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
    )
    starts = [
        int(getattr(node, "lineno", 0))
        for node in tree.body
        if isinstance(node, meaningful) and getattr(node, "lineno", 0)
    ]
    if not starts:
        return []
    return _units_from_boundaries(text, starts, label=label, kind="python")


def _markdown_units(text: str, label: str) -> List[SemanticUnit]:
    lines = text.splitlines(keepends=True)
    boundaries = []
    in_fence = False
    saw_structure = False
    for index, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            saw_structure = True
            continue
        if not in_fence and stripped.startswith("#") and stripped.lstrip("#").startswith(" "):
            boundaries.append(index)
            saw_structure = True
    if not saw_structure:
        return []
    return _units_from_boundaries(text, boundaries, label=label, kind="markdown")


def _paragraph_units(text: str, label: str) -> List[SemanticUnit]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return [SemanticUnit(label, "", 1, 1, "text")]
    units: List[SemanticUnit] = []
    current: List[str] = []
    start = 1
    for index, line in enumerate(lines, start=1):
        if not current:
            start = index
        current.append(line)
        if not line.strip():
            content = "".join(current)
            units.append(SemanticUnit(
                f"{label} lines {start}-{index}", content, start, index, "paragraph"
            ))
            current = []
    if current:
        content = "".join(current)
        units.append(SemanticUnit(
            f"{label} lines {start}-{len(lines)}", content, start, len(lines), "paragraph"
        ))
    return units


def semantic_units(text: str, *, source_label: str = "input", kind: str = "auto") -> List[SemanticUnit]:
    """Split into deterministic syntax-aware units without enforcing size."""

    value = text or ""
    selected = kind
    if selected == "auto":
        if "diff --git " in value or "\n@@ -" in value:
            selected = "diff"
        elif source_label.lower().endswith(".py"):
            selected = "python"
        elif any(
            line.lstrip().startswith(("```", "~~~"))
            or (
                line.lstrip().startswith("#")
                and line.lstrip().lstrip("#").startswith(" ")
            )
            for line in value.splitlines()
        ):
            selected = "markdown"
        else:
            selected = "python"
    if selected == "diff":
        return _diff_units(value, source_label)
    if selected == "python":
        units = _python_units(value, source_label)
        if units:
            return units
    if selected == "markdown":
        units = _markdown_units(value, source_label)
        if units:
            return units
    return _paragraph_units(value, source_label)


def _split_utf8(text: str, max_bytes: int) -> List[str]:
    if max_bytes < 4:
        raise ValueError("max_bytes must be at least 4")
    raw = text.encode("utf-8")
    parts: List[str] = []
    offset = 0
    while offset < len(raw):
        end = min(len(raw), offset + max_bytes)
        if end < len(raw):
            newline = raw.rfind(b"\n", offset, end)
            if newline >= offset:
                end = newline + 1
        while end > offset:
            try:
                part = raw[offset:end].decode("utf-8")
                break
            except UnicodeDecodeError:
                end -= 1
        if end <= offset:
            raise ValueError("unable to split UTF-8 input within byte limit")
        parts.append(part)
        offset = end
    return parts or [""]


def _explode_units(units: Sequence[SemanticUnit], max_bytes: int) -> List[SemanticUnit]:
    out: List[SemanticUnit] = []
    for unit in units:
        if len(unit.content.encode("utf-8")) <= max_bytes:
            out.append(unit)
            continue
        line_cursor = unit.start_line
        for index, part in enumerate(_split_utf8(unit.content, max_bytes)):
            end_line = _line_end(line_cursor, part)
            out.append(SemanticUnit(
                label=f"{unit.label} part {index + 1}",
                content=part,
                start_line=line_cursor,
                end_line=end_line,
                kind=unit.kind,
            ))
            line_cursor += part.count("\n")
    return out


def pack_units(
    units: Sequence[SemanticUnit],
    *,
    max_bytes: int = DEFAULT_CHUNK_BYTES,
    overlap_lines: int = 0,
) -> List[RequestChunk]:
    """Pack adjacent semantic units and split every overlong unit byte-safely."""

    if max_bytes < 4:
        raise ValueError("max_bytes must be at least 4")
    exploded = _explode_units(units, max_bytes)
    groups: List[List[SemanticUnit]] = []
    current: List[SemanticUnit] = []
    current_bytes = 0
    for unit in exploded:
        size = len(unit.content.encode("utf-8"))
        if current and current_bytes + size > max_bytes:
            groups.append(current)
            current = []
            current_bytes = 0
        current.append(unit)
        current_bytes += size
    if current:
        groups.append(current)

    chunks: List[RequestChunk] = []
    for group in groups:
        content = "".join(unit.content for unit in group)
        labels = tuple(dict.fromkeys(unit.label for unit in group))
        chunks.append(RequestChunk(
            index=len(chunks),
            content=content,
            labels=labels,
            start_line=group[0].start_line,
            end_line=group[-1].end_line,
            kind=group[0].kind if all(u.kind == group[0].kind for u in group) else "mixed",
        ))

    if overlap_lines > 0 and len(chunks) > 1:
        with_overlap: List[RequestChunk] = [chunks[0]]
        for chunk in chunks[1:]:
            previous_lines = with_overlap[-1].content.splitlines(keepends=True)
            prefix = "".join(previous_lines[-overlap_lines:])
            while prefix and len((prefix + chunk.content).encode("utf-8")) > max_bytes:
                prefix_lines = prefix.splitlines(keepends=True)
                prefix = "".join(prefix_lines[1:]) if len(prefix_lines) > 1 else ""
            with_overlap.append(RequestChunk(
                index=chunk.index,
                content=prefix + chunk.content,
                labels=chunk.labels,
                start_line=max(1, chunk.start_line - prefix.count("\n")),
                end_line=chunk.end_line,
                kind=chunk.kind,
                overlap_bytes=len(prefix.encode("utf-8")),
            ))
        chunks = with_overlap

    # Exact duplicates can arise from repeated symlink targets or overlap-only
    # boundaries. Preserve first occurrence and reindex deterministically.
    unique: List[RequestChunk] = []
    seen = set()
    for chunk in chunks:
        key = (chunk.digest, chunk.labels, chunk.start_line, chunk.end_line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(RequestChunk(
            index=len(unique),
            content=chunk.content,
            labels=chunk.labels,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            kind=chunk.kind,
            overlap_bytes=chunk.overlap_bytes,
        ))
    return unique


def _reject(
    plan: RequestPlan,
    reason: str,
    mitigation: str,
) -> RequestPlan:
    plan.action = PlanAction.REJECT
    plan.reason = reason
    plan.mitigation = mitigation
    return plan


def plan_request(
    text: str,
    *,
    mode: str,
    validators: Sequence[Any],
    policy: str = "auto",
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    max_calls: int = DEFAULT_MAX_EXTERNAL_CALLS,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    per_call_timeout: float = DEFAULT_MODEL_CALL_TIMEOUT_SECONDS,
    parallel: bool = True,
    allow_chunk: bool = True,
    source_label: str = "input",
    kind: str = "auto",
    overlap_lines: int = 0,
    extra_calls: int = 0,
    chunk_extra_calls: Optional[int] = None,
    units: Optional[Sequence[SemanticUnit]] = None,
) -> RequestPlan:
    """Return direct/chunk/reject admission with exact call/byte forecasts."""

    if policy not in ("auto", "direct", "chunk"):
        raise ValueError("policy must be auto, direct, or chunk")
    if max_chunks <= 0 or max_calls <= 0 or max_input_bytes <= 0:
        raise ValueError("request limits must be positive")
    profile = InputProfile.from_text(text)
    transports = [transport_for_validator(item) for item in validators]
    validator_count = len(transports)
    direct_limit = min(
        [DEFAULT_DIRECT_PROMPT_BYTES]
        + [max(4, item.max_prompt_bytes) for item in transports]
    )
    maximum_concurrency = validator_count if parallel else min(1, validator_count)
    direct_calls = validator_count + max(0, extra_calls)
    plan = RequestPlan(
        mode=mode,
        action=PlanAction.DIRECT,
        reason=f"input fits direct byte ceiling {direct_limit}",
        profile=profile,
        transports=[TransportPlan(
            item.validator,
            item.transport,
            item.max_prompt_bytes,
            profile.utf8_bytes <= min(DEFAULT_DIRECT_PROMPT_BYTES, item.max_prompt_bytes),
        ) for item in transports],
        planned_calls=direct_calls,
        required_calls=direct_calls,
        maximum_concurrency=maximum_concurrency,
        nominal_wall_ceiling_seconds=(
            per_call_timeout if parallel and validator_count else per_call_timeout * validator_count
        ) + max(0, extra_calls) * per_call_timeout,
        max_chunks=max_chunks,
        max_calls=max_calls,
        max_input_bytes=max_input_bytes,
        required_chunks=1,
        forecast_input_bytes=profile.utf8_bytes * validator_count,
        policy=policy,
    )

    direct_safe = profile.utf8_bytes <= direct_limit
    if policy != "chunk" and direct_safe:
        if direct_calls > max_calls:
            return _reject(
                plan,
                f"direct plan needs {direct_calls} calls but max is {max_calls}",
                f"raise --max-calls to at least {direct_calls} or reduce validators",
            )
        if plan.forecast_input_bytes > max_input_bytes:
            return _reject(
                plan,
                f"direct plan accounts {plan.forecast_input_bytes} input bytes but max is {max_input_bytes}",
                f"raise LOPE_MAX_INPUT_BYTES to at least {plan.forecast_input_bytes} or reduce input",
            )
        return plan

    if policy == "direct":
        return _reject(
            plan,
            f"input is {profile.utf8_bytes} bytes; direct ceiling is {direct_limit}",
            "use --request-policy chunk, reduce the input, or select a file/stdin-capable provider",
        )
    if not allow_chunk:
        return _reject(
            plan,
            f"{mode} cannot losslessly shape {profile.utf8_bytes} bytes into a direct request",
            "provide a smaller evidence brief or explicitly reduce the source context",
        )

    # Chunk executors add provenance/task/review instructions around each
    # content unit. Reserve that wrapper before packing so even a provider
    # with a tiny declared prompt cap cannot receive an over-limit request.
    effective_chunk_bytes = min(
        max_chunk_bytes,
        max(4, direct_limit - PROMPT_WRAPPER_RESERVE_BYTES),
    )
    semantic = list(units) if units is not None else semantic_units(
        text, source_label=source_label, kind=kind
    )
    chunks = pack_units(
        semantic,
        max_bytes=effective_chunk_bytes,
        overlap_lines=max(0, overlap_lines),
    )
    required_chunks = max(1, len(chunks))
    effective_chunk_extra = (
        max(0, extra_calls)
        if chunk_extra_calls is None else max(0, chunk_extra_calls)
    )
    required_calls = required_chunks * validator_count + effective_chunk_extra
    forecast_input = sum(
        chunk.profile.utf8_bytes + PROMPT_WRAPPER_RESERVE_BYTES
        for chunk in chunks
    ) * validator_count
    nominal = required_chunks * (
        per_call_timeout if parallel else per_call_timeout * validator_count
    ) + effective_chunk_extra * per_call_timeout
    plan.action = PlanAction.CHUNK
    plan.reason = (
        "chunk policy forced" if policy == "chunk" else
        f"input exceeds direct byte ceiling {direct_limit}"
    )
    plan.chunks = chunks
    plan.required_chunks = required_chunks
    plan.planned_calls = required_calls
    plan.required_calls = required_calls
    plan.forecast_input_bytes = forecast_input
    plan.nominal_wall_ceiling_seconds = nominal

    if required_chunks > max_chunks:
        return _reject(
            plan,
            f"request needs {required_chunks} chunks but max is {max_chunks}",
            f"use --max-chunks {required_chunks} --max-calls {required_calls}; "
            f"nominal call ceiling {nominal:g}s",
        )
    if required_calls > max_calls:
        return _reject(
            plan,
            f"request needs {required_calls} calls but max is {max_calls}",
            f"use --max-calls {required_calls}, reduce validators, or increase chunk size",
        )
    if forecast_input > max_input_bytes:
        return _reject(
            plan,
            f"request accounts {forecast_input} input bytes but max is {max_input_bytes}",
            f"raise LOPE_MAX_INPUT_BYTES to at least {forecast_input} or reduce input",
        )
    return plan


def nominal_token_estimate(utf8_bytes: int) -> int:
    """Public conservative estimate; never use as a hard boundary."""

    return int(math.ceil(max(0, int(utf8_bytes)) / 3.0))


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DEFAULT_DIRECT_PROMPT_BYTES",
    "PROMPT_WRAPPER_RESERVE_BYTES",
    "PlanAction",
    "RequestChunk",
    "RequestPlan",
    "RequestRejected",
    "SemanticUnit",
    "TransportPlan",
    "nominal_token_estimate",
    "pack_units",
    "plan_request",
    "semantic_units",
    "transport_for_validator",
]
