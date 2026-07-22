"""Single-writer implementation attempts with bounded sequential failover."""

from __future__ import annotations

import hashlib
import os
import stat
import struct
import subprocess
import time
from typing import Callable, Iterable, List, Optional, Sequence

from .executor import ImplementationResult
from .provider_errors import ProviderInfrastructureError
from .runtime import BudgetExhausted


def ordered_writers(pool, preferred_names: Sequence[str]) -> List:
    """Return available pool members in explicit single-writer attempt order."""

    validators = list(pool.validators())
    by_name = {validator.name: validator for validator in validators}
    primary = pool.primary_validator()
    names = [primary.name, *preferred_names]
    ordered = []
    seen = set()
    for name in names:
        validator = by_name.get(name)
        if validator is None or name in seen:
            continue
        seen.add(name)
        ordered.append(validator)
    unsupported = [
        writer.name
        for writer in ordered
        if not getattr(writer, "supports_safe_implementation_failover", False)
    ]
    if len(ordered) > 1 and unsupported:
        raise ValueError(
            "multi-writer failover requires typed infrastructure support; "
            f"unsupported: {', '.join(unsupported)}"
        )
    return ordered


def _attempt_prompt(base_prompt: str, writer_name: str, prior_failures: Iterable[str]) -> str:
    failures = list(prior_failures)
    parts = [
        base_prompt,
        "",
        "## Active writer attempt",
        f"- You are the sole active writer for this attempt: {writer_name}.",
        "- Inspect current workspace state before editing; a failed earlier writer may have left useful partial work.",
        "- Preserve correct existing work and never reset or revert unrelated changes.",
        "- Do not invoke Lope, another AI CLI, an MCP agent, or a nested model process.",
        "- The outer Lope process owns fallback, escalation, and validation.",
    ]
    if failures:
        parts.extend(["- Prior infrastructure failures:", *[f"  - {item}" for item in failures]])
    return "\n".join(parts)


def _record(digest, label: bytes, value: bytes) -> None:
    digest.update(struct.pack(">Q", len(label)))
    digest.update(label)
    digest.update(struct.pack(">Q", len(value)))
    digest.update(value)


def _git_output(root: str, args: Sequence[str]) -> Optional[bytes]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _path_fingerprint(root: str, raw_path: bytes) -> Optional[bytes]:
    path = os.path.join(root, os.fsdecode(raw_path))
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return b"missing"
    except OSError:
        return None
    if stat.S_ISLNK(before.st_mode):
        try:
            return b"symlink\0" + os.readlink(path).encode("utf-8", errors="surrogateescape")
        except OSError:
            return None
    if not stat.S_ISREG(before.st_mode):
        return None
    content = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                content.update(chunk)
        after = os.lstat(path)
    except OSError:
        return None
    if (before.st_size, before.st_mtime_ns, before.st_mode) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_mode,
    ):
        return None
    metadata = struct.pack(">QQ", stat.S_IMODE(after.st_mode), after.st_size)
    return b"file\0" + metadata + content.digest()


def workspace_snapshot(cwd: Optional[str] = None) -> Optional[bytes]:
    """Fingerprint HEAD, index, flags, and all workspace file content."""

    root = cwd or os.environ.get("LOPE_WORKDIR") or os.getcwd()
    head = _git_output(root, ["rev-parse", "--verify", "HEAD"])
    index = _git_output(root, ["ls-files", "--stage", "-z"])
    flags = _git_output(root, ["ls-files", "-v", "-z"])
    visible_paths = _git_output(
        root,
        ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    )
    ignored_paths = _git_output(
        root,
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
    )
    if any(
        value is None
        for value in (head, index, flags, visible_paths, ignored_paths)
    ):
        return None
    digest = hashlib.sha256()
    _record(digest, b"HEAD", head or b"")
    _record(digest, b"INDEX", index or b"")
    _record(digest, b"FLAGS", flags or b"")
    paths = set((visible_paths or b"").split(b"\0"))
    paths.update((ignored_paths or b"").split(b"\0"))
    for raw_path in sorted(paths):
        if not raw_path:
            continue
        value = _path_fingerprint(root, raw_path)
        if value is None:
            return None
        _record(digest, b"PATH\0" + raw_path, value)
    return digest.digest()


def invoke_writer_failover(
    writers: Sequence,
    base_prompt: str,
    timeout: int,
    *,
    phase_index: int,
    context=None,
    print_fn: Callable[..., None] = print,
) -> ImplementationResult:
    """Invoke one writer at a time and fail over only on infrastructure errors."""

    from .invocation import invoke_generate

    failures: List[str] = []
    stage_deadline = time.monotonic() + float(timeout)
    for attempt, writer in enumerate(writers, start=1):
        if not writer.available():
            failures.append(f"{writer.name}: unavailable")
            continue
        remaining = stage_deadline - time.monotonic()
        if remaining <= 0:
            failures.append("implementation stage deadline exhausted")
            break
        attempt_timeout = min(float(timeout), remaining)
        print_fn(
            f">>> Delegating to {writer.name} ({attempt_timeout:.1f}s timeout, attempt {attempt}/{len(writers)})..."
        )
        prompt = _attempt_prompt(base_prompt, writer.name, failures)
        before = workspace_snapshot()
        try:
            output = invoke_generate(
                writer,
                prompt,
                attempt_timeout,
                context=context,
                stage="implementation",
                metadata={
                    "implementation": True,
                    "writer": writer.name,
                    "writer_attempt": attempt,
                },
                max_retries=0,
            )
        except NotImplementedError:
            detail = f"{writer.name}: generate not supported"
            return ImplementationResult(ok=False, summary=detail, error=detail)
        except BudgetExhausted as exc:
            detail = f"{writer.name}: {type(exc).__name__}: {exc}"
            return ImplementationResult(ok=False, summary=detail, error=detail)
        except ProviderInfrastructureError as exc:
            detail = f"{writer.name}: {type(exc).__name__}: {exc}"
            after = workspace_snapshot()
            if before is None or after is None:
                blocked = f"{detail}; fallback blocked: workspace state unavailable"
                return ImplementationResult(ok=False, summary=blocked, error=blocked)
            if before != after:
                blocked = f"{detail}; fallback blocked: failed writer changed workspace"
                return ImplementationResult(ok=False, summary=blocked, error=blocked)
            failures.append(detail)
            print_fn(f">>> {detail}; workspace unchanged; trying next writer if configured")
            continue
        except Exception as exc:
            detail = f"{writer.name}: {type(exc).__name__}: {exc}"
            return ImplementationResult(ok=False, summary=detail, error=detail)

        summary = (output or "").strip()[:2000]
        if not summary:
            summary = f"{writer.name} completed phase {phase_index} (no stdout summary)"
        print_fn(f">>> {writer.name} returned {len(output or '')} chars")
        return ImplementationResult(ok=True, summary=summary)

    detail = "; ".join(failures) or "no implementation writers configured"
    return ImplementationResult(
        ok=False,
        summary=f"all implementation writers failed: {detail}",
        error=detail,
    )
