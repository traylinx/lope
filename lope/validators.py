"""
Validator backends for Lope — abstract interface, 5 CLI validators, 2 pool strategies.

Concrete validators (all share the ---VERDICT---...---END--- block contract):
  - OpencodeValidator: `opencode run --format json` subprocess wrapper
  - GeminiCliValidator: `gemini --prompt ... --output-format json`
  - ClaudeCodeValidator: `claude --print "<prompt>"`
  - CodexValidator: `codex exec --quiet "<prompt>"`
  - AiderValidator: `aider --message "<prompt>" --no-git --no-auto-commits --yes`
  - StubValidator: deterministic canned response, for tests

Pool strategies:
  - ValidatorPool: sequential fallback chain (primary first, skip on INFRA_ERROR)
  - EnsemblePool: parallel ThreadPoolExecutor + majority-vote synthesis

The ---VERDICT---...---END--- parser tries JSON first, falls back to YAML-ish
regex for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod
from typing import List, Optional

from .models import (
    PhaseVerdict,
    ValidatorResult,
    VerdictStatus,
)

log = logging.getLogger("lope.validators")


DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("LOPE_TIMEOUT", "480"))
MIN_CONFIDENCE_FOR_PASS = 0.7
DEFAULT_OPENCODE_BIN = os.environ.get(
    "OPENCODE_BIN", "opencode"
)


# ─── Formal VERDICT schema ──────────────────────────────────────
#
# The VERDICT block is lope's contract with every validator (opencode,
# gemini-cli, future plugins). Historically it was parsed via regex on a
# YAML-ish text body. The regex is tolerant but silently loses information
# when validators drift their output format.
#
# The schema below is the single source of truth for what a VERDICT block
# must contain. It's expressed as a plain dict (zero external deps — lope
# is stdlib-only) and enforced by `validate_verdict_dict`. Validators may
# now emit a JSON object INSIDE the `---VERDICT---...---END---` block; the
# parser tries JSON first and falls back to the YAML regex for
# backward-compat with every opencode run that came before this sprint.

VERDICT_SCHEMA = {
    "type": "object",
    "required": ["status", "confidence", "rationale"],
    "properties": {
        "status": {
            "type": "string",
            "enum": ["PASS", "NEEDS_FIX", "FAIL"],
            "description": "Verdict outcome. Low-confidence PASS is "
            "demoted to NEEDS_FIX by the parser.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Validator's subjective confidence. PASS with "
            "confidence < 0.7 is reclassified as NEEDS_FIX.",
        },
        "rationale": {
            "type": "string",
            "minLength": 1,
            "description": "One paragraph explaining the decision.",
        },
        "required_fixes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete actionable fixes. Empty list on PASS.",
        },
        "nice_to_have": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional improvements — not blocking.",
        },
    },
    "additionalProperties": True,  # allow validators to add metadata
}


class VerdictSchemaError(Exception):
    """Raised by validate_verdict_dict on schema violation (tests use this).

    The parser never raises — it wraps errors as INFRA_ERROR PhaseVerdict.
    This exception exists for direct schema validation in tests and for
    validators that want to validate their own output before sending it.
    """

    pass


def validate_verdict_dict(d: dict) -> None:
    """Enforce VERDICT_SCHEMA against a parsed dict.

    Pure stdlib — no jsonschema dep. Raises VerdictSchemaError with a
    specific message on the first violation. Call paths that never raise
    (the parser) wrap this in try/except and convert to INFRA_ERROR.
    """
    if not isinstance(d, dict):
        raise VerdictSchemaError(f"VERDICT must be an object, got {type(d).__name__}")

    for field in VERDICT_SCHEMA["required"]:
        if field not in d:
            raise VerdictSchemaError(f"VERDICT missing required field: {field!r}")

    status = d["status"]
    if not isinstance(status, str):
        raise VerdictSchemaError(
            f"VERDICT.status must be string, got {type(status).__name__}"
        )
    if status.upper() not in ("PASS", "NEEDS_FIX", "FAIL"):
        raise VerdictSchemaError(
            f"VERDICT.status must be PASS|NEEDS_FIX|FAIL, got {status!r}"
        )

    confidence = d["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise VerdictSchemaError(
            f"VERDICT.confidence must be number, got {type(confidence).__name__}"
        )
    if not (0.0 <= float(confidence) <= 1.0):
        raise VerdictSchemaError(
            f"VERDICT.confidence must be in [0.0, 1.0], got {confidence}"
        )

    rationale = d["rationale"]
    if not isinstance(rationale, str):
        raise VerdictSchemaError(
            f"VERDICT.rationale must be string, got {type(rationale).__name__}"
        )
    if not rationale.strip():
        raise VerdictSchemaError("VERDICT.rationale must not be empty")

    for key in ("required_fixes", "nice_to_have"):
        if key in d:
            val = d[key]
            if not isinstance(val, list):
                raise VerdictSchemaError(
                    f"VERDICT.{key} must be array, got {type(val).__name__}"
                )
            for i, item in enumerate(val):
                if not isinstance(item, str):
                    raise VerdictSchemaError(
                        f"VERDICT.{key}[{i}] must be string, got {type(item).__name__}"
                    )


# ─── Abstract base ──────────────────────────────────────────────


class Validator(ABC):
    """Contract every validator backend must implement.

    Implementations should NEVER raise from validate(). Return a
    ValidatorResult with an error string and INFRA_ERROR verdict instead.
    This keeps the ValidatorPool fallback logic simple.

    A validator MAY also implement `.generate(prompt)` to act as a drafter
    (used by the Negotiator to produce initial sprint proposals). Drafting
    and reviewing are the same CLI, different prompts — the whole lope
    premise is that any CLI can implement AND any CLI can validate.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and pool routing."""

    @abstractmethod
    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        """Send a review prompt, return a parsed ValidatorResult. Never raises."""

    def available(self) -> bool:
        """Whether this validator can be invoked right now.

        Override in subclasses that need to check binaries or credentials.
        Default is True so StubValidator / tests don't need to care.
        """
        return True

    def generate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> str:
        """Run the underlying CLI with a raw prompt, return raw text.

        Used by the Negotiator when this validator is chosen as the
        drafter (the "implementer" in the any-CLI-implements philosophy).
        Unlike `validate()`, no VERDICT wrapper is added and no VERDICT
        block is parsed out — callers get raw stdout.

        Raises NotImplementedError if this validator doesn't yet support
        drafting. Raises RuntimeError on subprocess failure (non-zero
        exit, timeout, empty output). Never returns an INFRA_ERROR
        sentinel — callers should try/except.
        """
        raise NotImplementedError(
            f"{self.name} does not support .generate() yet — it can only "
            f"review (validate), not draft. Pick a different primary in "
            f"~/.lope/config.json (claude, opencode, gemini-cli, codex, or "
            f"aider all support drafting), or set LOPE_LLM_URL to fall back "
            f"to a hosted endpoint."
        )


# ─── Opencode validator — the real thing ───────────────────────


class OpencodeValidator(Validator):
    """Wraps `opencode run --format json` for real phase validation.

    The prompt is sent on stdin. We parse the JSON event stream, extract
    text events, concatenate, then search for an opencode-formatted
    VERDICT block.
    """

    def __init__(
        self,
        binary: str = DEFAULT_OPENCODE_BIN,
        workdir: Optional[str] = None,
    ):
        self._binary = binary
        self._workdir = workdir or os.environ.get("LOPE_WORKDIR", os.getcwd())

    @property
    def name(self) -> str:
        return "opencode"

    def available(self) -> bool:
        import shutil
        return shutil.which(self._binary) is not None

    def generate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> str:
        if not self.available():
            raise RuntimeError(f"opencode binary not found at {self._binary}")
        try:
            proc = subprocess.run(
                [self._binary, "run", "--format", "json"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._workdir,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"opencode run timed out after {timeout}s")
        except OSError as e:
            raise RuntimeError(f"opencode failed to launch: {e}")
        if proc.returncode != 0:
            raise RuntimeError(
                f"opencode exited {proc.returncode}: {(proc.stderr or '')[:500]}"
            )
        text = _extract_text_from_json_stream(proc.stdout)
        if not text:
            raise RuntimeError(
                f"opencode returned no text events; stdout head: {proc.stdout[:300]}"
            )
        return text

    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        if not self.available():
            return _infra_error(
                self.name,
                f"opencode binary not found at {self._binary}",
            )

        started = time.time()
        try:
            proc = subprocess.run(
                [self._binary, "run", "--format", "json"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._workdir,
            )
        except subprocess.TimeoutExpired:
            return _infra_error(
                self.name,
                f"opencode run timed out after {timeout}s",
                duration=time.time() - started,
            )
        except OSError as e:
            return _infra_error(
                self.name,
                f"opencode failed to launch: {e}",
                duration=time.time() - started,
            )

        duration = time.time() - started

        if proc.returncode != 0:
            return _infra_error(
                self.name,
                f"opencode exited with code {proc.returncode}; "
                f"stderr head: {(proc.stderr or '')[:500]}",
                duration=duration,
            )

        text = _extract_text_from_json_stream(proc.stdout)
        if not text:
            return _infra_error(
                self.name,
                f"opencode returned no text events; stdout head: {proc.stdout[:300]}",
                duration=duration,
            )

        verdict = parse_opencode_verdict(
            text,
            validator_name=self.name,
            fallback_duration=duration,
        )
        return ValidatorResult(
            validator_name=self.name,
            verdict=verdict,
            raw_response=text,
            error="",
        )


def _extract_text_from_json_stream(stdout: str) -> str:
    """Concatenate all `type=text` events from opencode's JSON stream.

    Opencode's `--format json` emits one JSON object per line; the
    assistant's prose comes in objects where `type == "text"` and
    `part.text` has the chunk. Unknown / malformed lines are ignored.
    """
    parts: List[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            part = event.get("part") or {}
            text = part.get("text") or ""
            if text:
                parts.append(text)
    return "".join(parts)


# ─── Opencode VERDICT block parser ─────────────────────────────


_OPENCODE_VERDICT_BLOCK_RE = re.compile(
    r"---VERDICT---\s*\n(.+?)\n---END---",
    re.DOTALL,
)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_json_verdict(block_body: str) -> Optional[dict]:
    """Attempt to parse the VERDICT block body as a JSON object.

    Returns the validated dict on success, None on any failure (so the
    caller falls back to the YAML regex parser). Never raises.

    Supports three framings:
      1. Pure JSON body: `{"status": "PASS", ...}`
      2. JSON wrapped in triple-backtick code fence: ```json\n{...}\n```
      3. JSON embedded in prose (greedy first-{-to-last-} match)
    """
    stripped = block_body.strip()
    if not stripped:
        return None

    candidates = []

    # Framing 1: whole body is JSON
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    # Framing 2: ```json\n{...}\n``` code fence
    fence_match = re.search(
        r"```(?:json)?\s*\n(\{.*?\})\s*\n```",
        block_body,
        re.DOTALL,
    )
    if fence_match:
        candidates.append(fence_match.group(1))

    # Framing 3: greedy first-object match anywhere in the body
    obj_match = _JSON_OBJECT_RE.search(block_body)
    if obj_match:
        candidates.append(obj_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, json.JSONDecodeError):
            continue
        try:
            validate_verdict_dict(parsed)
        except VerdictSchemaError:
            continue
        return parsed

    return None


_OPENCODE_STATUS_RE = re.compile(r"^\s*status:\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_OPENCODE_CONFIDENCE_RE = re.compile(
    r"^\s*confidence:\s*([0-9.]+)", re.IGNORECASE | re.MULTILINE
)
_OPENCODE_RATIONALE_RE = re.compile(
    r"^\s*rationale:\s*(.+?)(?=^\s*(?:required_fixes|nice_to_have|status|confidence):|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_OPENCODE_FIXES_RE = re.compile(
    # Match `required_fixes:` line, then capture everything until either
    # the next recognized field header OR end of string. The `(?:\n|\Z)`
    # terminator on each body line handles the edge case where the last
    # fix line has no trailing newline (parser must work on the final
    # field in the block, not just middle fields).
    r"^\s*required_fixes:\s*$\n"
    r"(?P<body>(?:.*(?:\n|\Z))*?)"
    r"(?=^\s*(?:nice_to_have|status|confidence):|\Z)",
    re.IGNORECASE | re.MULTILINE,
)
_OPENCODE_NICE_RE = re.compile(
    r"^\s*nice_to_have:\s*$\n"
    r"(?P<body>(?:.*(?:\n|\Z))*?)"
    r"(?=^\s*(?:required_fixes|status|confidence):|\Z)",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_bullet_list(body: str) -> List[str]:
    """Extract bullet-list items from a regex body match. Empty placeholder
    items ('(empty)' etc.) are filtered out. Shared between required_fixes
    and nice_to_have so both fields are parsed consistently."""
    items: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if item and "empty" not in item.lower():
                items.append(item)
        elif stripped.startswith("* "):
            item = stripped[2:].strip()
            if item:
                items.append(item)
    return items


# ─── Evidence gate (verification-before-completion) ────────────

# Matches `path/to/file.py:42` or `src/foo.rs:123-456`.
_FILE_LINE_RE = re.compile(
    r"(?:[A-Za-z0-9_./-]+/)?[A-Za-z0-9_.-]+\.[A-Za-z0-9]+:\d+"
)
# Matches `$ cmd`, `> cmd`, or `# cmd` command lines. The `$`/`>` can appear
# at start of string or after a newline, with optional leading whitespace.
_SHELL_CMD_RE = re.compile(r"(?:^|\n)[ \t]*[\$>#][ \t]+\S")
# Fenced code block.
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
# Word-bounded evidence phrases. Case-insensitive but each must be a real
# word boundary hit, not a substring match inside another word.
_EVIDENCE_PATTERNS = [
    re.compile(r"\btest(?:s)?\s+passed\b", re.IGNORECASE),
    re.compile(r"\ball\s+tests?\s+pass(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\bverified\b", re.IGNORECASE),
    re.compile(r"\bconfirmed\b", re.IGNORECASE),
    re.compile(r"\bexit\s+(?:code\s+)?0\b", re.IGNORECASE),
    re.compile(r"\breturn\s*code\s+0\b", re.IGNORECASE),
    re.compile(r"\brun\s+\d+\s+tests?\b", re.IGNORECASE),
    re.compile(r"\btest_\w+\b"),  # pytest-style test function names
]


def _evidence_present(text: str) -> bool:
    """Heuristic: does the text contain verification evidence?

    Any of the following counts:
      - a file:line reference (path/file.ext:N)
      - a fenced code block
      - a shell command line (^$, ^>, or ^# followed by a command)
      - an explicit verification phrase ("test passed", "verified",
        "exit code 0", etc. — word-boundary matched so substrings don't
        false-positive)
      - a pytest-style test function name (`test_foo`)

    Returns False on empty text or bare prose like "looks good to me".
    """
    if not text or not text.strip():
        return False
    if _CODE_FENCE_RE.search(text):
        return True
    if _FILE_LINE_RE.search(text):
        return True
    if _SHELL_CMD_RE.search(text):
        return True
    for pat in _EVIDENCE_PATTERNS:
        if pat.search(text):
            return True
    return False


def _apply_evidence_gate(
    status: VerdictStatus,
    rationale: str,
    raw_response: str,
    required_fixes: List[str],
) -> tuple:
    """Downgrade PASS to NEEDS_FIX if rationale lacks evidence.

    Returns (new_status, new_required_fixes, triggered).
    No-op unless status is PASS and LOPE_EVIDENCE_GATE is not set to off.
    """
    if os.environ.get("LOPE_EVIDENCE_GATE", "").lower() == "off":
        return status, required_fixes, False
    if status != VerdictStatus.PASS:
        return status, required_fixes, False
    if _evidence_present(rationale) or _evidence_present(raw_response):
        return status, required_fixes, False
    new_fixes = list(required_fixes) if required_fixes else []
    new_fixes.insert(
        0,
        "provide verification evidence in rationale (file:line reference, "
        "command output, test result, or fenced code block) — no PASS "
        "without evidence",
    )
    return VerdictStatus.NEEDS_FIX, new_fixes, True


def parse_opencode_verdict(
    text: str,
    validator_name: str = "opencode",
    fallback_duration: float = 0.0,
) -> PhaseVerdict:
    """Extract a PhaseVerdict from opencode's `---VERDICT---...---END---` block.

    Never raises. Returns INFRA_ERROR if the block can't be found or the
    status token is unrecognized.
    """
    if not text:
        return PhaseVerdict(
            status=VerdictStatus.INFRA_ERROR,
            rationale="opencode response was empty",
            duration_seconds=fallback_duration,
            validator_name=validator_name,
        )

    block_match = _OPENCODE_VERDICT_BLOCK_RE.search(text)
    if not block_match:
        return PhaseVerdict(
            status=VerdictStatus.INFRA_ERROR,
            rationale="no ---VERDICT---...---END--- block found in opencode response",
            duration_seconds=fallback_duration,
            validator_name=validator_name,
        )

    block = block_match.group(1)

    # JSON-first parse path (schema-validated). If the block body is a
    # JSON object matching VERDICT_SCHEMA, use it verbatim. Otherwise fall
    # through to the legacy YAML-ish regex parser below.
    json_verdict = _try_parse_json_verdict(block)
    if json_verdict is not None:
        status_str = str(json_verdict["status"]).upper()
        status = VerdictStatus(status_str)
        confidence = float(json_verdict["confidence"])
        rationale = str(json_verdict["rationale"]).strip()
        required_fixes = [
            s
            for s in json_verdict.get("required_fixes", [])
            if isinstance(s, str) and s.strip() and "empty" not in s.lower()
        ]
        nice_to_have = [
            s
            for s in json_verdict.get("nice_to_have", [])
            if isinstance(s, str) and s.strip() and "empty" not in s.lower()
        ]
        if status == VerdictStatus.PASS and confidence < MIN_CONFIDENCE_FOR_PASS:
            status = VerdictStatus.NEEDS_FIX
            if not required_fixes:
                required_fixes.append(
                    f"confidence {confidence:.2f} below threshold {MIN_CONFIDENCE_FOR_PASS}"
                )
        # Evidence gate: downgrade PASS without evidence to NEEDS_FIX
        status, required_fixes, evidence_triggered = _apply_evidence_gate(
            status, rationale, text, required_fixes
        )
        return PhaseVerdict(
            status=status,
            confidence=confidence,
            rationale=rationale,
            required_fixes=required_fixes,
            nice_to_have=nice_to_have,
            duration_seconds=fallback_duration,
            validator_name=validator_name,
            evidence_gate_triggered=evidence_triggered,
        )

    # Status
    status_match = _OPENCODE_STATUS_RE.search(block)
    status_raw = (status_match.group(1) if status_match else "").upper()
    if status_raw in ("PASS", "NEEDS_FIX", "FAIL"):
        status = VerdictStatus(status_raw)
    else:
        return PhaseVerdict(
            status=VerdictStatus.INFRA_ERROR,
            rationale=f"unknown status token: {status_raw!r}",
            duration_seconds=fallback_duration,
            validator_name=validator_name,
        )

    # Confidence
    conf_match = _OPENCODE_CONFIDENCE_RE.search(block)
    try:
        confidence = float(conf_match.group(1)) if conf_match else 0.0
    except ValueError:
        confidence = 0.0

    # Rationale
    rationale = ""
    rat_match = _OPENCODE_RATIONALE_RE.search(block)
    if rat_match:
        rationale = "\n".join(
            l.rstrip() for l in rat_match.group(1).splitlines()
        ).strip()

    # Required fixes
    required_fixes: List[str] = []
    fix_match = _OPENCODE_FIXES_RE.search(block)
    if fix_match:
        required_fixes = _parse_bullet_list(fix_match.group("body"))

    # Nice-to-have (optional field, extracted for schema consistency)
    nice_to_have: List[str] = []
    nice_match = _OPENCODE_NICE_RE.search(block)
    if nice_match:
        nice_to_have = _parse_bullet_list(nice_match.group("body"))

    # Confidence gate: low-confidence PASS is reclassified as NEEDS_FIX
    if status == VerdictStatus.PASS and confidence < MIN_CONFIDENCE_FOR_PASS:
        status = VerdictStatus.NEEDS_FIX
        if not required_fixes:
            required_fixes.append(
                f"confidence {confidence:.2f} below threshold {MIN_CONFIDENCE_FOR_PASS}"
            )

    # Evidence gate: downgrade PASS without evidence to NEEDS_FIX
    status, required_fixes, evidence_triggered = _apply_evidence_gate(
        status, rationale, text, required_fixes
    )

    return PhaseVerdict(
        status=status,
        confidence=confidence,
        rationale=rationale,
        required_fixes=required_fixes,
        nice_to_have=nice_to_have,
        duration_seconds=fallback_duration,
        validator_name=validator_name,
        evidence_gate_triggered=evidence_triggered,
    )


# ─── Stub validator (for tests) ────────────────────────────────


class StubValidator(Validator):
    """Deterministic validator for unit tests.

    Constructed with a canned ValidatorResult that is returned from
    validate(). Optionally can be scripted with a list of responses so
    tests can simulate multi-round behavior.
    """

    def __init__(
        self,
        name: str = "stub",
        response: Optional[ValidatorResult] = None,
        responses: Optional[List[ValidatorResult]] = None,
    ):
        self._name = name
        if responses is not None:
            self._responses = list(responses)
            self._cursor = 0
        elif response is not None:
            self._responses = [response]
            self._cursor = 0
        else:
            # Default: always PASS with high confidence
            self._responses = [
                ValidatorResult(
                    validator_name=name,
                    verdict=PhaseVerdict(
                        status=VerdictStatus.PASS,
                        confidence=0.95,
                        rationale="stub default PASS",
                        duration_seconds=0.1,
                        validator_name=name,
                    ),
                )
            ]
            self._cursor = 0
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        self.calls += 1
        if not self._responses:
            return _infra_error(self._name, "stub exhausted: no responses left")
        if self._cursor < len(self._responses):
            result = self._responses[self._cursor]
            self._cursor += 1
        else:
            result = self._responses[-1]  # last-response sticky when scripted short
        return result


# ─── Gemini CLI validator — v1.1 — NOW WIRED ─────────────────────


class GeminiCliValidator(Validator):
    """Wraps `gemini --prompt ... --output-format json` for validated review.

    Uses the same ---VERDICT---...---END--- block format as OpencodeValidator,
    enabling identical parsing. Gemini CLI is invoked headlessly with a
    paper-review prompt; the assistant's text response is parsed for the
    VERDICT block.
    """

    def __init__(self, binary: str = None):
        import shutil

        self._binary = binary or shutil.which("gemini") or "gemini"

    @property
    def name(self) -> str:
        return "gemini-cli"

    def available(self) -> bool:
        import shutil

        return shutil.which(self._binary) is not None

    def generate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> str:
        if not self.available():
            raise RuntimeError(f"gemini binary not found: {self._binary}")
        try:
            proc = subprocess.run(
                [self._binary, "--prompt", prompt, "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("LOPE_WORKDIR", os.getcwd()),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"gemini-cli timed out after {timeout}s")
        except OSError as e:
            raise RuntimeError(f"gemini-cli failed to launch: {e}")
        if proc.returncode != 0:
            raise RuntimeError(
                f"gemini-cli exited {proc.returncode}: {(proc.stderr or '')[:500]}"
            )
        try:
            result = json.loads(proc.stdout)
            text = result.get("response", "") or ""
        except json.JSONDecodeError:
            raise RuntimeError(
                f"gemini-cli returned non-JSON output; stdout head: {proc.stdout[:300]}"
            )
        if not text:
            raise RuntimeError("gemini-cli returned empty response")
        return text

    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        started = time.time()

        # Build the full review prompt (same contract as OpencodeValidator)
        full_prompt = (
            "You are a senior AI researcher reviewing a research paper draft. "
            "Give honest, specific, actionable feedback. Return a VERDICT block.\n\n"
            + prompt
        )

        try:
            proc = subprocess.run(
                [self._binary, "--prompt", full_prompt, "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("LOPE_WORKDIR", os.getcwd()),
            )
        except subprocess.TimeoutExpired:
            return _infra_error(
                self.name,
                f"gemini-cli timed out after {timeout}s",
                duration=time.time() - started,
            )
        except OSError as e:
            return _infra_error(
                self.name,
                f"gemini-cli failed to launch: {e}",
                duration=time.time() - started,
            )

        duration = time.time() - started

        if proc.returncode != 0:
            return _infra_error(
                self.name,
                f"gemini-cli exited with code {proc.returncode}; "
                f"stderr: {(proc.stderr or '')[:300]}",
                duration=duration,
            )

        # Parse JSON output: { "response": "...", "session_id": "...", ... }
        text = ""
        try:
            result = json.loads(proc.stdout)
            text = result.get("response", "") or ""
        except json.JSONDecodeError:
            return _infra_error(
                self.name,
                f"gemini-cli returned non-JSON output; stdout head: {proc.stdout[:300]}",
                duration=duration,
            )

        if not text:
            return _infra_error(
                self.name,
                "gemini-cli returned empty response",
                duration=duration,
            )

        verdict = parse_opencode_verdict(
            text,
            validator_name=self.name,
            fallback_duration=duration,
        )
        return ValidatorResult(
            validator_name=self.name,
            verdict=verdict,
            raw_response=text,
            error="",
        )


# ─── ValidatorPool — fallback chain ────────────────────────────


class ValidatorPool:
    """Chain of validators. Primary first; fallback on infra error.

    PASS, NEEDS_FIX, and FAIL all halt the chain — they are decisions by
    the primary validator that should not be second-guessed. Only infra
    errors (subprocess died, timeout, unparseable response, validator
    unavailable) cause fallback to the next validator.
    """

    def __init__(self, validators: List[Validator], primary: Optional[str] = None):
        if not validators:
            raise ValueError("ValidatorPool needs at least one validator")
        self._all = list(validators)
        if primary is not None:
            self._ordered = _reorder_primary_first(self._all, primary)
        else:
            self._ordered = list(self._all)

    def names(self) -> List[str]:
        return [v.name for v in self._ordered]

    def primary_validator(self) -> Validator:
        """Return the primary validator — the one used as the drafter."""
        return self._ordered[0]

    def reviewers(self) -> List[Validator]:
        """Return the non-primary validators, used to vote on drafts."""
        return list(self._ordered[1:])

    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        last_error = ""
        attempts: List[str] = []
        for validator in self._ordered:
            if not validator.available():
                attempts.append(f"{validator.name}:unavailable")
                continue
            attempts.append(validator.name)
            log.info(f"[pool] trying validator: {validator.name}")
            result = validator.validate(prompt, timeout=timeout)
            # PASS / NEEDS_FIX / FAIL halt the chain. INFRA_ERROR falls through.
            if result.verdict.status != VerdictStatus.INFRA_ERROR:
                log.info(
                    f"[pool] {validator.name} returned {result.verdict.status.value}"
                )
                return result
            last_error = result.error or result.verdict.rationale
            log.warning(
                f"[pool] {validator.name} infra error → fallback: {last_error[:200]}"
            )

        # Every validator failed with infra error
        return _infra_error(
            "pool",
            f"all validators exhausted ({','.join(attempts) or 'none'}). "
            f"Last error: {last_error[:300]}",
        )


def _reorder_primary_first(
    validators: List[Validator], primary: str
) -> List[Validator]:
    """Move the named validator to the front of the list if present."""
    by_name = {v.name: v for v in validators}
    if primary not in by_name:
        raise ValueError(
            f"ValidatorPool: primary {primary!r} not in validator list: "
            f"{sorted(by_name.keys())}"
        )
    ordered = [by_name[primary]]
    for v in validators:
        if v.name != primary:
            ordered.append(v)
    return ordered


# ─── Infra error helper ────────────────────────────────────────


# ─── Flag-error detection (v0.4.0 self-heal primitive) ─────────

# Patterns that indicate a CLI vendor changed its flag surface.
# When we see one of these in stderr from a non-zero exit, the subprocess
# failure is most likely a flag break, not a network/content problem.
# The healer can respond by running `<cli> --help`, asking a reviewer for
# the corrected invocation, smoke-testing it, and persisting the learned
# adapter to ~/.lope/config.json.
_FLAG_ERROR_PATTERNS = [
    re.compile(r"\bunrecognized\s+arguments?\b", re.IGNORECASE),
    re.compile(r"\bunknown\s+(?:option|argument|flag)\b", re.IGNORECASE),
    re.compile(r"\bunexpected\s+argument\b", re.IGNORECASE),
    re.compile(r"\bno\s+such\s+option\b", re.IGNORECASE),
    re.compile(r"\binvalid\s+(?:option|flag|argument)\b", re.IGNORECASE),
    re.compile(r"^usage:\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\berror:\s+(?:unrecognized|unknown)\b", re.IGNORECASE),
]


def _is_flag_error(stderr: str) -> bool:
    """True if stderr looks like a CLI flag-surface change.

    Matches a short whitelist of patterns common to argparse, Cobra, Click,
    clap, and most other CLI libraries. Does NOT match network errors,
    rate-limit responses, or genuine content failures.
    """
    if not stderr:
        return False
    head = stderr[:2000]  # enough for usage banners without parsing the world
    return any(rx.search(head) for rx in _FLAG_ERROR_PATTERNS)


class AdapterFlagError(Exception):
    """Raised or returned when a validator fails with a flag-surface error.

    Carries the context SelfHealer needs to propose + test a corrected
    invocation: the CLI name, the old argv template, and the raw stderr.
    The executor/negotiator catches this at the pool boundary and decides
    whether to attempt a heal (LOPE_SELF_HEAL=1) or escalate.
    """

    def __init__(self, cli_name: str, old_argv: List[str], stderr: str):
        self.cli_name = cli_name
        self.old_argv = list(old_argv)
        self.stderr = stderr or ""
        super().__init__(
            f"{cli_name} flag break: {self.stderr[:200]!r}"
        )


def _infra_error(
    validator_name: str,
    message: str,
    duration: float = 0.0,
) -> ValidatorResult:
    """Build a ValidatorResult representing an infra-layer failure.

    v0.4.0: if `message` matches a flag-break pattern, attach a
    `flag_error_hint` so the pool boundary can route the failure through
    the self-heal machinery instead of a plain escalation.
    """
    flag_hint = ""
    if _is_flag_error(message):
        flag_hint = message[:2000]
    return ValidatorResult(
        validator_name=validator_name,
        verdict=PhaseVerdict(
            status=VerdictStatus.INFRA_ERROR,
            rationale=message,
            duration_seconds=duration,
            validator_name=validator_name,
        ),
        raw_response="",
        error=message,
        flag_error_hint=flag_hint,
    )


# ─── Claude Code validator ───────────────────────────────────


class ClaudeCodeValidator(Validator):
    """Wraps `claude --print "<prompt>"` for validated review.

    Uses the same ---VERDICT---...---END--- block format as OpencodeValidator.
    """

    def __init__(self, binary: str = None):
        import shutil

        self._binary = binary or shutil.which("claude") or "claude"

    @property
    def name(self) -> str:
        return "claude"

    def available(self) -> bool:
        import shutil

        return shutil.which(self._binary) is not None

    def _build_prompt(self, prompt: str) -> str:
        return (
            "You are a senior AI researcher reviewing a research paper draft. "
            "Give honest, specific, actionable feedback. Return a VERDICT block.\n\n"
            + prompt
        )

    def generate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> str:
        if not self.available():
            raise RuntimeError(f"claude binary not found: {self._binary}")
        try:
            proc = subprocess.run(
                [self._binary, "--print", prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("LOPE_WORKDIR", os.getcwd()),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude timed out after {timeout}s")
        except OSError as e:
            raise RuntimeError(f"claude failed to launch: {e}")
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude exited {proc.returncode}: {(proc.stderr or '')[:500]}"
            )
        if not proc.stdout:
            raise RuntimeError("claude returned empty output")
        return proc.stdout

    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        if not self.available():
            return _infra_error(self.name, f"claude binary not found: {self._binary}")

        started = time.time()
        try:
            proc = subprocess.run(
                [self._binary, "--print", self._build_prompt(prompt)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("LOPE_WORKDIR", os.getcwd()),
            )
        except subprocess.TimeoutExpired:
            return _infra_error(
                self.name,
                f"claude timed out after {timeout}s",
                duration=time.time() - started,
            )
        except OSError as e:
            return _infra_error(
                self.name,
                f"claude failed to launch: {e}",
                duration=time.time() - started,
            )

        duration = time.time() - started

        if proc.returncode != 0:
            return _infra_error(
                self.name,
                f"claude exited with code {proc.returncode}; "
                f"stderr: {(proc.stderr or '')[:500]}",
                duration=duration,
            )

        text = proc.stdout
        if not text:
            return _infra_error(
                self.name,
                "claude returned empty output",
                duration=duration,
            )

        verdict = parse_opencode_verdict(
            text,
            validator_name=self.name,
            fallback_duration=duration,
        )
        return ValidatorResult(
            validator_name=self.name,
            verdict=verdict,
            raw_response=text,
            error="",
        )


# ─── Codex validator ───────────────────────────────────────────


class CodexValidator(Validator):
    """Wraps `codex exec --quiet "<prompt>"` for validated review."""

    def __init__(self, binary: str = None):
        import shutil

        self._binary = binary or shutil.which("codex") or "codex"

    @property
    def name(self) -> str:
        return "codex"

    def available(self) -> bool:
        import shutil

        return shutil.which(self._binary) is not None

    def _build_prompt(self, prompt: str) -> str:
        return (
            "You are a senior AI researcher reviewing a research paper draft. "
            "Give honest, specific, actionable feedback. Return a VERDICT block.\n\n"
            + prompt
        )

    def generate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> str:
        if not self.available():
            raise RuntimeError(f"codex binary not found: {self._binary}")
        try:
            proc = subprocess.run(
                [self._binary, "exec", "--quiet", prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("LOPE_WORKDIR", os.getcwd()),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"codex timed out after {timeout}s")
        except OSError as e:
            raise RuntimeError(f"codex failed to launch: {e}")
        if proc.returncode != 0:
            raise RuntimeError(
                f"codex exited {proc.returncode}: {(proc.stderr or '')[:500]}"
            )
        if not proc.stdout:
            raise RuntimeError("codex returned empty output")
        return proc.stdout

    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        if not self.available():
            return _infra_error(self.name, f"codex binary not found: {self._binary}")

        started = time.time()
        try:
            proc = subprocess.run(
                [self._binary, "exec", "--quiet", self._build_prompt(prompt)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("LOPE_WORKDIR", os.getcwd()),
            )
        except subprocess.TimeoutExpired:
            return _infra_error(
                self.name,
                f"codex timed out after {timeout}s",
                duration=time.time() - started,
            )
        except OSError as e:
            return _infra_error(
                self.name,
                f"codex failed to launch: {e}",
                duration=time.time() - started,
            )

        duration = time.time() - started

        if proc.returncode != 0:
            return _infra_error(
                self.name,
                f"codex exited with code {proc.returncode}; "
                f"stderr: {(proc.stderr or '')[:500]}",
                duration=duration,
            )

        text = proc.stdout
        if not text:
            return _infra_error(
                self.name,
                "codex returned empty output",
                duration=duration,
            )

        verdict = parse_opencode_verdict(
            text,
            validator_name=self.name,
            fallback_duration=duration,
        )
        return ValidatorResult(
            validator_name=self.name,
            verdict=verdict,
            raw_response=text,
            error="",
        )


# ─── Aider validator ──────────────────────────────────────────


class AiderValidator(Validator):
    """Wraps `aider --message "<prompt>" --no-git --no-auto-commits --yes`."""

    def __init__(self, binary: str = None):
        import shutil

        self._binary = binary or shutil.which("aider") or "aider"

    @property
    def name(self) -> str:
        return "aider"

    def available(self) -> bool:
        import shutil

        return shutil.which(self._binary) is not None

    def _build_prompt(self, prompt: str) -> str:
        return (
            "You are a senior AI researcher reviewing a research paper draft. "
            "Give honest, specific, actionable feedback. Return a VERDICT block.\n\n"
            + prompt
        )

    def generate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> str:
        if not self.available():
            raise RuntimeError(f"aider binary not found: {self._binary}")
        try:
            proc = subprocess.run(
                [self._binary, "--message", prompt, "--no-git",
                 "--no-auto-commits", "--yes"],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("LOPE_WORKDIR", os.getcwd()),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"aider timed out after {timeout}s")
        except OSError as e:
            raise RuntimeError(f"aider failed to launch: {e}")
        if proc.returncode != 0:
            raise RuntimeError(
                f"aider exited {proc.returncode}: {(proc.stderr or '')[:500]}"
            )
        if not proc.stdout:
            raise RuntimeError("aider returned empty output")
        return proc.stdout

    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        if not self.available():
            return _infra_error(self.name, f"aider binary not found: {self._binary}")

        started = time.time()
        try:
            proc = subprocess.run(
                [
                    self._binary,
                    "--message",
                    self._build_prompt(prompt),
                    "--no-git",
                    "--no-auto-commits",
                    "--yes",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("LOPE_WORKDIR", os.getcwd()),
            )
        except subprocess.TimeoutExpired:
            return _infra_error(
                self.name,
                f"aider timed out after {timeout}s",
                duration=time.time() - started,
            )
        except OSError as e:
            return _infra_error(
                self.name,
                f"aider failed to launch: {e}",
                duration=time.time() - started,
            )

        duration = time.time() - started

        if proc.returncode != 0:
            return _infra_error(
                self.name,
                f"aider exited with code {proc.returncode}; "
                f"stderr: {(proc.stderr or '')[:500]}",
                duration=duration,
            )

        text = proc.stdout
        if not text:
            return _infra_error(
                self.name,
                "aider returned empty output",
                duration=duration,
            )

        verdict = parse_opencode_verdict(
            text,
            validator_name=self.name,
            fallback_duration=duration,
        )
        return ValidatorResult(
            validator_name=self.name,
            verdict=verdict,
            raw_response=text,
            error="",
        )


# ─── EnsemblePool — parallel ensemble with majority-vote synthesis ───────────


class EnsemblePool:
    """Run all validators concurrently, synthesize a majority-vote verdict.

    Unlike ValidatorPool (which is a fallback chain), EnsemblePool fires all
    validators in parallel threads and synthesizes a single result using:
      - PASS/NEEDS_FIX/FAIL majority vote
      - Any FAIL is a veto (synthesized result is FAIL)
      - Tie on PASS vs NEEDS_FIX → NEEDS_FIX (conservative)
      - Confidence is the mean of decisive results
      - required_fixes is the union of all NEEDS_FIX fix lists, deduplicated
    """

    def __init__(
        self,
        validators: List[Validator],
        primary: Optional[str] = None,
        max_workers: int = 5,
    ):
        if not validators:
            raise ValueError("EnsemblePool needs at least one validator")
        self._validators = list(validators)
        self._primary = primary
        self._max_workers = max_workers

    def names(self) -> List[str]:
        return [v.name for v in self._validators]

    def primary_validator(self) -> Validator:
        """Return the primary validator — the one used as the drafter."""
        if self._primary:
            for v in self._validators:
                if v.name == self._primary:
                    return v
        return self._validators[0]

    def reviewers(self) -> List[Validator]:
        """Return the non-primary validators, used to vote on drafts."""
        primary = self.primary_validator()
        return [v for v in self._validators if v is not primary]

    def validate(
        self, prompt: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> ValidatorResult:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        available = [v for v in self._validators if v.available()]
        if not available:
            return _infra_error(
                "ensemble",
                f"no validators available in pool: {[v.name for v in self._validators]}",
            )

        results: List[ValidatorResult] = []
        with ThreadPoolExecutor(
            max_workers=min(len(available), self._max_workers)
        ) as executor:
            futures = {
                executor.submit(v.validate, prompt, timeout): v for v in available
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    v = futures[future]
                    results.append(
                        ValidatorResult(
                            validator_name=v.name,
                            verdict=PhaseVerdict(
                                status=VerdictStatus.INFRA_ERROR,
                                rationale=f"thread raised: {e}",
                                validator_name=v.name,
                            ),
                            error=str(e),
                        )
                    )

        return _synthesize(results, primary=self._primary)


def _synthesize(
    results: List[ValidatorResult], primary: Optional[str] = None
) -> ValidatorResult:
    decisive = [r for r in results if r.verdict.status != VerdictStatus.INFRA_ERROR]
    infra_errors = [r for r in results if r.verdict.status == VerdictStatus.INFRA_ERROR]

    if not decisive:
        last_err = infra_errors[-1].error if infra_errors else "all validators failed"
        return ValidatorResult(
            validator_name="ensemble",
            verdict=PhaseVerdict(
                status=VerdictStatus.INFRA_ERROR,
                rationale=f"all validators infra error: {last_err[:300]}",
                validator_name="ensemble",
            ),
            error=last_err,
        )

    vote: dict[VerdictStatus, int] = {
        VerdictStatus.PASS: 0,
        VerdictStatus.NEEDS_FIX: 0,
        VerdictStatus.FAIL: 0,
    }
    for r in decisive:
        vote[r.verdict.status] += 1

    if vote[VerdictStatus.FAIL] > 0:
        final_status = VerdictStatus.FAIL
    elif vote[VerdictStatus.PASS] > vote[VerdictStatus.NEEDS_FIX]:
        final_status = VerdictStatus.PASS
    elif vote[VerdictStatus.PASS] == vote[VerdictStatus.NEEDS_FIX]:
        final_status = VerdictStatus.NEEDS_FIX
    else:
        final_status = VerdictStatus.NEEDS_FIX

    confidence_vals = [
        r.verdict.confidence for r in decisive if r.verdict.confidence > 0
    ]
    confidence = sum(confidence_vals) / len(confidence_vals) if confidence_vals else 0.0

    all_fixes: List[str] = []
    seen: set[str] = set()
    for r in decisive:
        if r.verdict.status == VerdictStatus.NEEDS_FIX:
            for fix in r.verdict.required_fixes:
                if fix not in seen:
                    seen.add(fix)
                    all_fixes.append(fix)

    primary_rationale = ""
    if primary:
        for r in decisive:
            if r.validator_name == primary and r.verdict.rationale:
                primary_rationale = f" Primary ({primary}): {r.verdict.rationale[:200]}"
                break

    vote_summary = f"PASS={vote[VerdictStatus.PASS]} NEEDS_FIX={vote[VerdictStatus.NEEDS_FIX]} FAIL={vote[VerdictStatus.FAIL]}"

    return ValidatorResult(
        validator_name="ensemble",
        verdict=PhaseVerdict(
            status=final_status,
            confidence=confidence,
            rationale=f"Ensemble ({len(decisive)} validators): {vote_summary}.{primary_rationale}",
            required_fixes=all_fixes,
            validator_name="ensemble",
        ),
        raw_response="",
        error="",
    )


# ─── Config-driven pool builder ─────────────────────────────


def build_validator_pool(cfg: "LopeCfg") -> "ValidatorPool":
    """Build a ValidatorPool or EnsemblePool from config.

    Resolution order:
      1. Hardcoded validators (claude, opencode, gemini, codex, aider) — take priority on name collision
      2. Generic providers from cfg.providers — subprocess or http, user-defined
      3. Auto-detected CLIs from cli_discovery KNOWN_CLIS (ollama, goose, etc.) — use generic subprocess
    """
    from .config import LopeCfg
    from .cli_discovery import KNOWN_CLIS

    validator_map = {
        "claude": ClaudeCodeValidator,
        "opencode": OpencodeValidator,
        "gemini": GeminiCliValidator,
        "codex": CodexValidator,
        "aider": AiderValidator,
    }

    # Build a map of generic providers by name
    generic_map = {}
    providers = getattr(cfg, "providers", []) or []
    if providers:
        from .generic_validators import build_provider, ConfigError
        for entry in providers:
            try:
                pname = entry.get("name") if isinstance(entry, dict) else None
                if not pname:
                    continue
                generic_map[pname] = entry
            except Exception as e:
                log.warning(f"Skipping malformed provider: {e}")

    # Auto-provisioned CLIs from KNOWN_CLIS with generic_command
    auto_map = {}
    for cli in KNOWN_CLIS:
        if cli.generic_command:
            auto_map[cli.name] = {
                "name": cli.name,
                "type": "subprocess",
                "command": list(cli.generic_command),
            }

    validators = []
    primary = None
    for name in cfg.validators:
        v = None
        # Hardcoded first
        cls = validator_map.get(name)
        if cls is not None:
            v = cls()
        # User-defined generic providers second
        elif name in generic_map:
            try:
                from .generic_validators import build_provider
                v = build_provider(generic_map[name])
            except Exception as e:
                log.warning(f"Failed to build generic provider {name!r}: {e}")
                continue
        # Auto-provisioned from KNOWN_CLIS third
        elif name in auto_map:
            try:
                from .generic_validators import build_provider
                v = build_provider(auto_map[name])
            except Exception as e:
                log.warning(f"Failed to auto-provision {name!r}: {e}")
                continue
        else:
            log.warning(f"Unknown validator: {name}")
            continue
        validators.append(v)
        if name == cfg.primary:
            primary = v

    if not validators:
        raise ValueError("No valid validators configured. Run: lope configure")

    if primary is None:
        primary = validators[0]

    if cfg.parallel and len(validators) > 1:
        return EnsemblePool(validators=validators, primary=primary.name)
    else:
        fallbacks = [v for v in validators if v is not primary]
        return ValidatorPool(validators=[primary] + fallbacks, primary=primary.name)
