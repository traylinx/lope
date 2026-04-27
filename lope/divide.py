"""Divide + role plumbing for ``lope review`` (v0.7 phase 8).

Three responsibilities:

* :func:`split_files` walks a directory or single file and yields
  reviewable :class:`FileChunk` records, skipping binary content and
  splitting oversized files at line boundaries so each chunk stays
  inside the validator context budget.
* :func:`split_diff_hunks` parses a unified-diff blob into
  :class:`HunkChunk` records that point at the *post-change* line
  range so SARIF / merge views can attach findings to the new file.
* The role lens — :data:`ROLE_LENSES`, :func:`assign_roles`, and
  :func:`build_role_prompt` — turns a single artifact into N
  role-tinted reviews so a security-vs-perf-vs-tests divergence
  shows up cleanly in the consensus output.

Stdlib only; no IO beyond the directory walk in :func:`split_files`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .redaction import redact_text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_MAX_CHARS = 16_000
"""Soft per-chunk character budget. Files larger than this are split on
line boundaries so each piece fits comfortably inside a validator's
context window without truncating mid-statement."""

DEFAULT_MAX_FILE_BYTES = 256_000
"""Hard upper bound. Files larger than this are skipped entirely with a
``too_large`` reason so multi-MB binaries don't accidentally enter the
review pipeline."""

# File extensions that are obviously not reviewable text. We additionally
# probe the content for a NUL byte to catch unknown formats, but the
# extension shortcut keeps the walk fast on big trees.
_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".so", ".dylib", ".dll", ".a", ".o", ".obj", ".exe",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".mkv", ".wav", ".flac",
    ".class", ".jar", ".whl", ".pyc",
    ".db", ".sqlite", ".sqlite3", ".lock",
    ".bin", ".dat",
})

# Directory names the walker should never descend into.
_SKIP_DIRECTORIES = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    ".DS_Store",
})


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class FileChunk:
    """One reviewable slice of one file.

    ``label`` is what gets stamped into the validator prompt + the
    consensus report so a finding always traces back to the original
    source line range, even when a giant file was split into pieces.
    """

    path: str
    content: str
    start_line: int = 1
    end_line: int = 1
    chunk_index: int = 0
    chunk_total: int = 1

    @property
    def label(self) -> str:
        if self.chunk_total <= 1:
            return self.path
        return (
            f"{self.path} (chunk {self.chunk_index + 1}/{self.chunk_total}: "
            f"lines {self.start_line}-{self.end_line})"
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "chunk_index": self.chunk_index,
            "chunk_total": self.chunk_total,
            "content_chars": len(self.content),
        }


@dataclass
class SkippedFile:
    """File the walker chose not to deliver to the review pipeline."""

    path: str
    reason: str  # binary | too_large | unreadable
    detail: str = ""


@dataclass
class HunkChunk:
    """One ``@@`` hunk lifted from a unified diff."""

    path: str
    hunk_index: int
    content: str
    new_start: int
    new_lines: int
    old_start: int = 0
    old_lines: int = 0

    @property
    def new_end(self) -> int:
        return max(self.new_start, self.new_start + self.new_lines - 1)

    @property
    def label(self) -> str:
        return (
            f"{self.path} (hunk {self.hunk_index + 1}: "
            f"new lines {self.new_start}-{self.new_end})"
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "hunk_index": self.hunk_index,
            "new_start": self.new_start,
            "new_lines": self.new_lines,
            "new_end": self.new_end,
            "old_start": self.old_start,
            "old_lines": self.old_lines,
            "content_chars": len(self.content),
        }


# ---------------------------------------------------------------------------
# File walker
# ---------------------------------------------------------------------------


def split_files(
    target: Path,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    skip_dirs: Optional[Iterable[str]] = None,
    extra_binary_extensions: Optional[Iterable[str]] = None,
) -> Tuple[List[FileChunk], List[SkippedFile]]:
    """Walk ``target`` and return ``(chunks, skipped)``.

    ``target`` may be a single file or a directory. Directories are
    walked deterministically (sorted alphabetically) so the same input
    tree always produces the same chunk ordering. Binary files,
    oversized files, and unreadable files end up in ``skipped`` with a
    machine-readable ``reason`` so callers can surface them.
    """

    target = Path(target)
    if not target.exists():
        raise FileNotFoundError(target)

    binary_exts = set(_BINARY_EXTENSIONS)
    if extra_binary_extensions:
        binary_exts.update(ext.lower() for ext in extra_binary_extensions)

    skip = set(_SKIP_DIRECTORIES)
    if skip_dirs:
        skip.update(skip_dirs)

    if target.is_file():
        files = [target]
    else:
        files = sorted(_iter_files(target, skip, original_root=target))

    chunks: List[FileChunk] = []
    skipped: List[SkippedFile] = []

    for path in files:
        rel = _safe_relpath(path, target)
        try:
            stat = path.stat()
        except OSError as exc:
            skipped.append(SkippedFile(path=rel, reason="unreadable", detail=str(exc)))
            continue

        if stat.st_size > max_file_bytes:
            skipped.append(
                SkippedFile(
                    path=rel,
                    reason="too_large",
                    detail=f"{stat.st_size} bytes > {max_file_bytes}",
                )
            )
            continue

        if path.suffix.lower() in binary_exts:
            skipped.append(SkippedFile(path=rel, reason="binary", detail=path.suffix))
            continue

        try:
            raw = path.read_bytes()
        except OSError as exc:
            skipped.append(SkippedFile(path=rel, reason="unreadable", detail=str(exc)))
            continue

        if b"\x00" in raw:
            skipped.append(SkippedFile(path=rel, reason="binary", detail="NUL byte"))
            continue

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("latin-1")
            except UnicodeDecodeError as exc:  # pragma: no cover — extremely rare
                skipped.append(SkippedFile(path=rel, reason="unreadable", detail=str(exc)))
                continue

        chunks.extend(_chunk_text(rel, text, max_chars=max_chars))

    return chunks, skipped


def _iter_files(
    root: Path,
    skip_dirs: set,
    *,
    original_root: Optional[Path] = None,
) -> Iterator[Path]:
    """Recursive deterministic file walker.

    ``original_root`` is the *top-level* directory the user asked us to
    walk. It is threaded through every recursive call so symlink
    containment is always checked against the user's requested tree —
    not the subdirectory we happen to be inside. Without this, a
    symlink at depth 2 that points to a sibling directory inside the
    same top-level tree would incorrectly fail containment.
    """

    boundary = (original_root or root).resolve()
    for entry in sorted(root.iterdir()):
        if entry.name in skip_dirs:
            continue
        if entry.is_symlink():
            try:
                resolved = entry.resolve()
            except OSError:
                continue
            try:
                resolved.relative_to(boundary)
            except ValueError:
                continue
            if resolved.is_file():
                yield resolved
            elif resolved.is_dir():
                yield from _iter_files(
                    resolved, skip_dirs, original_root=boundary
                )
            continue
        if entry.is_dir():
            yield from _iter_files(entry, skip_dirs, original_root=boundary)
        elif entry.is_file():
            yield entry


def _safe_relpath(path: Path, root: Path) -> str:
    try:
        if root.is_dir():
            return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        pass
    return str(path)


def _chunk_text(path: str, text: str, *, max_chars: int) -> List[FileChunk]:
    """Split ``text`` into FileChunks of ≤ ``max_chars`` characters.

    We split on line boundaries whenever possible so a single chunk
    never bisects a function declaration. Each chunk records its
    1-based source line range so consensus findings can point back to
    the original file even after the split.
    """

    if max_chars <= 0:
        max_chars = DEFAULT_MAX_CHARS

    lines = text.splitlines(keepends=True)
    if not lines:
        # Empty file — emit a single empty chunk so callers see the file
        # but have nothing to review (the validator will say "empty").
        return [FileChunk(path=path, content="", start_line=1, end_line=1, chunk_total=1)]

    chunks_acc: List[List[str]] = []
    current: List[str] = []
    current_size = 0

    for line in lines:
        if current and current_size + len(line) > max_chars:
            chunks_acc.append(current)
            current = []
            current_size = 0
        current.append(line)
        current_size += len(line)
    if current:
        chunks_acc.append(current)

    out: List[FileChunk] = []
    line_cursor = 1
    total = len(chunks_acc)
    for index, lines_in_chunk in enumerate(chunks_acc):
        content = "".join(lines_in_chunk)
        end_line = line_cursor + len(lines_in_chunk) - 1
        out.append(
            FileChunk(
                path=path,
                content=content,
                start_line=line_cursor,
                end_line=end_line,
                chunk_index=index,
                chunk_total=total,
            )
        )
        line_cursor = end_line + 1
    return out


# ---------------------------------------------------------------------------
# Diff hunk parser
# ---------------------------------------------------------------------------


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<a>\S+)\s+b/(?P<b>\S+)\s*$")
_NEW_FILE_RE = re.compile(r"^\+\+\+\s+b/(?P<path>.+?)\s*$")
_OLD_FILE_RE = re.compile(r"^---\s+a/(?P<path>.+?)\s*$")
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_lines>\d+))?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))?\s+@@"
)


def split_diff_hunks(text: str) -> List[HunkChunk]:
    """Parse a unified-diff blob into per-hunk :class:`HunkChunk` records.

    The parser is forgiving — a missing ``diff --git`` header is OK as
    long as a ``+++ b/<path>`` line shows up before the first hunk.
    Hunk bodies preserve the ``+``/``-``/space prefix so reviewers see
    the change context exactly as git would render it.
    """

    if not text or not text.strip():
        return []

    lines = text.splitlines()
    out: List[HunkChunk] = []
    current_path: Optional[str] = None
    hunk_index_per_file: Dict[str, int] = {}
    in_hunk = False
    hunk_lines: List[str] = []
    hunk_meta: Dict[str, int] = {}

    def _flush() -> None:
        nonlocal hunk_lines, in_hunk, hunk_meta
        if not in_hunk or not current_path:
            hunk_lines = []
            hunk_meta = {}
            in_hunk = False
            return
        idx = hunk_index_per_file.get(current_path, 0)
        out.append(
            HunkChunk(
                path=current_path,
                hunk_index=idx,
                content="\n".join(hunk_lines).rstrip("\n") + "\n",
                new_start=hunk_meta.get("new_start", 0),
                new_lines=hunk_meta.get("new_lines", 1),
                old_start=hunk_meta.get("old_start", 0),
                old_lines=hunk_meta.get("old_lines", 0),
            )
        )
        hunk_index_per_file[current_path] = idx + 1
        hunk_lines = []
        hunk_meta = {}
        in_hunk = False

    for line in lines:
        diff_match = _DIFF_HEADER_RE.match(line)
        if diff_match:
            _flush()
            current_path = diff_match.group("b")
            continue

        new_match = _NEW_FILE_RE.match(line)
        if new_match:
            _flush()
            current_path = new_match.group("path")
            continue

        if _OLD_FILE_RE.match(line):
            # ``---`` lines are noise once we have ``+++``; they would
            # only matter for delete-only patches, which we still report
            # under whatever ``current_path`` is in scope.
            continue

        hunk_match = _HUNK_HEADER_RE.match(line)
        if hunk_match:
            _flush()
            hunk_meta = {
                "old_start": int(hunk_match.group("old_start")),
                "old_lines": int(hunk_match.group("old_lines") or 1),
                "new_start": int(hunk_match.group("new_start")),
                "new_lines": int(hunk_match.group("new_lines") or 1),
            }
            hunk_lines = [line]
            in_hunk = True
            continue

        if in_hunk:
            hunk_lines.append(line)

    _flush()
    return out


# ---------------------------------------------------------------------------
# Role lens
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleLens:
    """One review perspective."""

    name: str
    title: str
    description: str
    prompt_prefix: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
        }


ROLE_LENSES: Dict[str, RoleLens] = {
    "security": RoleLens(
        name="security",
        title="Security review",
        description="Auth, input validation, secrets, OWASP-class issues.",
        prompt_prefix=(
            "You are reviewing strictly through a SECURITY lens. Focus on "
            "auth bypass, input validation, secret handling, injection, "
            "authorization gaps, and unsafe deserialization. Skip style, "
            "perf, and pure-readability concerns — other reviewers cover "
            "those. Tag findings with `[security]` so the consensus tooling "
            "can group them."
        ),
    ),
    "correctness": RoleLens(
        name="correctness",
        title="Correctness review",
        description="Logic bugs, edge cases, off-by-one, data integrity.",
        prompt_prefix=(
            "You are reviewing strictly through a CORRECTNESS lens. Hunt "
            "for logic bugs, edge cases, off-by-one errors, broken "
            "invariants, and silent data corruption. Skip security/perf — "
            "other reviewers own those. Tag findings with `[correctness]`."
        ),
    ),
    "performance": RoleLens(
        name="performance",
        title="Performance review",
        description="Hot loops, allocations, IO, indexes, caching.",
        prompt_prefix=(
            "You are reviewing strictly through a PERFORMANCE lens. Look "
            "for hot loops, unnecessary allocations, blocking IO on critical "
            "paths, missing indexes, cache misses, and quadratic algorithms. "
            "Tag findings with `[perf]`."
        ),
    ),
    "tests": RoleLens(
        name="tests",
        title="Test coverage review",
        description="Missing tests, brittle tests, mock-real divergence.",
        prompt_prefix=(
            "You are reviewing strictly through a TESTS lens. Identify "
            "missing test coverage, brittle assertions, mock-vs-prod "
            "divergence, and untested error paths. Tag findings with "
            "`[tests]`."
        ),
    ),
    "api-design": RoleLens(
        name="api-design",
        title="API design review",
        description="Surface ergonomics, breaking change risk, contracts.",
        prompt_prefix=(
            "You are reviewing strictly through an API-DESIGN lens. "
            "Evaluate naming, parameter ordering, breaking-change risk, "
            "back-compat, and consumer ergonomics. Tag findings with "
            "`[api-design]`."
        ),
    ),
    "docs": RoleLens(
        name="docs",
        title="Docs review",
        description="Missing docstrings, stale comments, README drift.",
        prompt_prefix=(
            "You are reviewing strictly through a DOCS lens. Flag missing "
            "docstrings, stale comments, drift between README and behavior, "
            "and any contract that is not documented. Tag findings with "
            "`[docs]`."
        ),
    ),
    "ops": RoleLens(
        name="ops",
        title="Ops / operability review",
        description="Logging, metrics, error handling, deployability.",
        prompt_prefix=(
            "You are reviewing strictly through an OPS lens. Look at "
            "structured logging, metrics, alerting hooks, error surfacing, "
            "rollback behavior, and on-call ergonomics. Tag findings with "
            "`[ops]`."
        ),
    ),
    "ux": RoleLens(
        name="ux",
        title="UX review",
        description="User-visible affordances, error messages, defaults.",
        prompt_prefix=(
            "You are reviewing strictly through a UX lens. Evaluate "
            "user-visible affordances, error messages, sensible defaults, "
            "and surprising behavior. Tag findings with `[ux]`."
        ),
    ),
}


# Accepted shorthand → canonical role name. Mirrors the categories
# normalized by ``lope/findings.py`` so a user can write ``--roles perf``
# even though the canonical lens is ``performance``.
_ROLE_ALIASES: Dict[str, str] = {
    "perf": "performance",
    "speed": "performance",
    "test": "tests",
    "testing": "tests",
    "doc": "docs",
    "documentation": "docs",
    "op": "ops",
    "operations": "ops",
    "infra": "ops",
    "ci": "ops",
    "sec": "security",
    "design": "ux",
    "api": "api-design",
}


def list_roles() -> List[str]:
    return sorted(ROLE_LENSES.keys())


def get_role(name: str) -> RoleLens:
    key = (name or "").strip().lower()
    canonical = _ROLE_ALIASES.get(key, key)
    if canonical not in ROLE_LENSES:
        raise KeyError(
            f"Unknown role lens: {name!r}. "
            f"Available: {', '.join(list_roles())}"
        )
    return ROLE_LENSES[canonical]


def parse_roles(spec: str) -> List[RoleLens]:
    """Turn a comma-separated role list into validated :class:`RoleLens`."""

    if not spec:
        return []
    raw = [bit.strip() for bit in spec.split(",") if bit.strip()]
    seen: List[str] = []
    out: List[RoleLens] = []
    for name in raw:
        lens = get_role(name)
        if lens.name in seen:
            continue
        seen.append(lens.name)
        out.append(lens)
    return out


def assign_roles(
    validators: Sequence[str],
    roles: Sequence[RoleLens],
) -> Dict[str, RoleLens]:
    """Round-robin map each validator to a role.

    The mapping is stable: passing the same ``validators`` and ``roles``
    twice yields the same dict so consensus sort and downstream caching
    stay deterministic. Raises :class:`ValueError` when either side is
    empty so callers fail loudly instead of silently producing nothing.
    """

    if not validators:
        raise ValueError("assign_roles requires at least one validator")
    if not roles:
        raise ValueError("assign_roles requires at least one role")
    out: Dict[str, RoleLens] = {}
    for index, validator in enumerate(validators):
        out[validator] = roles[index % len(roles)]
    return out


def build_role_prompt(role: RoleLens, base_prompt: str) -> str:
    """Prepend the role lens's prompt prefix to ``base_prompt``.

    Both the role prefix and the base prompt pass through
    :func:`lope.redaction.redact_text` so a custom :class:`RoleLens`
    that happens to carry a secret-shaped string (or a base prompt
    that does) cannot leak into the validator request. The built-in
    lenses contain no secrets, so the call is a cheap idempotent pass
    in the common case.
    """

    safe_prefix = redact_text(role.prompt_prefix or "").strip()
    safe_base = redact_text(base_prompt or "").rstrip()
    return f"{safe_prefix}\n\n{safe_base}\n"


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_FILE_BYTES",
    "FileChunk",
    "HunkChunk",
    "ROLE_LENSES",
    "RoleLens",
    "SkippedFile",
    "assign_roles",
    "build_role_prompt",
    "get_role",
    "list_roles",
    "parse_roles",
    "split_diff_hunks",
    "split_files",
]
