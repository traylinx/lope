"""
SelfHealer — lope's adapter resilience primitive (v0.4.0).

When a validator subprocess fails with a flag-surface error (upstream CLI
vendor renamed a flag), the healer:

  1. Runs `<cli_binary> --help` and captures the current help output.
  2. Asks the primary reviewer in the pool to propose a corrected argv
     template and stdin/stdout parsing tweaks, given the old argv, the
     stderr, and the help output.
  3. Smoke-tests the proposed invocation with a deterministic prompt
     ("reply with the single word OK and nothing else").
  4. On smoke-test pass, persists a LearnedAdapter to ~/.lope/config.json
     under `learned_adapters.<cli_name>` via the Phase 2 atomic + locked
     save. Lope will use the learned invocation for all future calls to
     that CLI.
  5. On smoke-test fail, logs the failed attempt and returns None. The
     pool boundary escalates the original failure.

Opt-in via LOPE_SELF_HEAL=1 for v0.4.0. Will flip to default-on in a
later release once we have telemetry confidence.

No state is modified unless the smoke test passes. One heal attempt
per CLI per session (tracked in a process-local set) — prevents
infinite heal loops.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import LearnedAdapter, LopeCfg, default_path, load, save
from .journal import append_event

log = logging.getLogger("lope.healer")


# Default smoke prompt — every heal attempt runs this through the proposed
# invocation. Simple enough that any coherent LLM can produce it.
SMOKE_PROMPT = "Reply with the single word OK and nothing else."
SMOKE_EXPECTED = "OK"
SMOKE_TIMEOUT_SECONDS = 60
HELP_TIMEOUT_SECONDS = 10


# Learned adapters expire after 90 days, at which point lope re-verifies
# via the same smoke test. Keeps stale learned state from silently rotting.
LEARNED_ADAPTER_TTL_SECONDS = 90 * 24 * 3600


class SelfHealer:
    """Per-session self-heal coordinator.

    Tracks which CLIs have already been healed in the current process
    (avoid infinite heal loops), gates heal attempts behind the
    LOPE_SELF_HEAL env var and the reviewer pool's availability.
    """

    def __init__(self) -> None:
        self._healed_this_session: set[str] = set()

    def should_attempt(self, cli_name: str, reviewer_available: bool) -> bool:
        """Gate: can we safely attempt a heal for this CLI right now?

        Returns False if:
          - LOPE_SELF_HEAL is not set to "1" / "true" / "yes"
          - cli_name has already been healed (attempted) this session
          - no reviewer is available to propose the fix
          - cli_name is empty (defensive)
        """
        if not cli_name:
            return False
        env = os.environ.get("LOPE_SELF_HEAL", "").strip().lower()
        if env not in ("1", "true", "yes", "on"):
            return False
        if cli_name in self._healed_this_session:
            return False
        if not reviewer_available:
            return False
        return True

    def mark_attempted(self, cli_name: str) -> None:
        self._healed_this_session.add(cli_name)

    def attempt(
        self,
        cli_name: str,
        cli_binary: str,
        old_argv: List[str],
        stderr: str,
        reviewer: Any,  # any object with .generate(prompt, timeout) -> str
    ) -> Optional[LearnedAdapter]:
        """Run the full heal sequence.

        Returns a persisted LearnedAdapter on success, None on any failure.
        Always marks the CLI as attempted (success OR failure) so we don't
        loop. All outcomes are journaled.
        """
        self.mark_attempted(cli_name)

        started = time.time()
        append_event(
            "heal_attempt",
            {
                "cli": cli_name,
                "old_argv": old_argv,
                "stderr_head": (stderr or "")[:500],
                "reviewer": getattr(reviewer, "name", "?"),
            },
        )

        # Step 1: capture --help output
        help_text = self._capture_help(cli_binary)
        if not help_text:
            log.warning(
                "self-heal: could not capture `%s --help` output; aborting",
                cli_binary,
            )
            append_event(
                "heal_failure",
                {"cli": cli_name, "reason": "help_capture_failed"},
            )
            return None

        # Step 2: ask reviewer for a proposed invocation
        proposal = self._ask_reviewer(
            cli_name, old_argv, stderr, help_text, reviewer
        )
        if proposal is None:
            log.warning(
                "self-heal: reviewer (%s) did not return a parseable proposal",
                getattr(reviewer, "name", "?"),
            )
            append_event(
                "heal_failure",
                {"cli": cli_name, "reason": "reviewer_no_proposal"},
            )
            return None

        # Step 3: smoke test the proposed invocation
        ok, smoke_output = self._smoke_test(cli_binary, proposal)
        if not ok:
            log.warning(
                "self-heal: smoke test failed for %s, new argv %s",
                cli_name,
                proposal.argv_template,
            )
            append_event(
                "heal_failure",
                {
                    "cli": cli_name,
                    "reason": "smoke_test_failed",
                    "proposed_argv": proposal.argv_template,
                    "smoke_output_head": (smoke_output or "")[:300],
                },
            )
            return None

        # Step 4: persist to global config
        proposal.timestamp = time.time()
        proposal.source_cli = getattr(reviewer, "name", "unknown")
        self._persist(cli_name, proposal)

        duration = time.time() - started
        log.info(
            "self-heal: healed %s in %.1fs (confidence %.2f)",
            cli_name,
            duration,
            proposal.confidence,
        )
        append_event(
            "heal_success",
            {
                "cli": cli_name,
                "new_argv": proposal.argv_template,
                "confidence": proposal.confidence,
                "duration_seconds": duration,
            },
        )
        return proposal

    def _capture_help(self, cli_binary: str) -> Optional[str]:
        """Run `<cli_binary> --help` with a timeout, return stdout or None."""
        try:
            proc = subprocess.run(
                [cli_binary, "--help"],
                capture_output=True,
                text=True,
                timeout=HELP_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        # Many CLIs print help to stdout, some to stderr. Prefer stdout,
        # fall back to stderr if stdout is empty.
        text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return text or None

    def _ask_reviewer(
        self,
        cli_name: str,
        old_argv: List[str],
        stderr: str,
        help_text: str,
        reviewer: Any,
    ) -> Optional[LearnedAdapter]:
        """Build a heal prompt and ask the reviewer for a corrected invocation."""
        prompt = _build_heal_prompt(cli_name, old_argv, stderr, help_text)
        try:
            response = reviewer.generate(prompt, timeout=120)
        except Exception as e:
            log.warning("self-heal: reviewer.generate raised %s", e)
            return None
        return _parse_heal_response(response)

    def _smoke_test(
        self,
        cli_binary: str,
        proposal: LearnedAdapter,
    ) -> tuple[bool, str]:
        """Run the proposed invocation against SMOKE_PROMPT, return (ok, output)."""
        argv = [_fill_template(t, SMOKE_PROMPT, cli_binary) for t in proposal.argv_template]
        stdin_data: Optional[str] = None
        if proposal.stdin_mode == "pipe":
            stdin_data = SMOKE_PROMPT
        try:
            proc = subprocess.run(
                argv,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=SMOKE_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, f"smoke-test failed to launch: {e}"
        if proc.returncode != 0:
            return False, f"smoke-test exit {proc.returncode}: {(proc.stderr or '')[:300]}"
        output = (proc.stdout or "")
        if proposal.stdout_parser.startswith("json:"):
            path = proposal.stdout_parser[5:]
            output = _extract_json_path(output, path) or ""
        return (SMOKE_EXPECTED in output.upper()), output

    def _persist(self, cli_name: str, adapter: LearnedAdapter) -> None:
        """Atomic-locked write of the learned adapter to ~/.lope/config.json."""
        path = default_path()
        cfg = load(path)
        if cfg is None:
            # No global config yet — create a minimal one to hold the learned
            # adapter. Users can run `lope configure` to fill in validators.
            cfg = LopeCfg(
                validators=[],
                primary="",
                timeout=480,
                parallel=True,
            )
        cfg.learned_adapters[cli_name] = adapter
        save(cfg, path)


def _build_heal_prompt(
    cli_name: str,
    old_argv: List[str],
    stderr: str,
    help_text: str,
) -> str:
    """Compose the reviewer prompt that proposes a new invocation."""
    return f"""You are helping lope repair a broken AI CLI adapter.

Lope invoked the `{cli_name}` CLI with this argv and it failed:

```
{old_argv}
```

The stderr from the failure (truncated):

```
{(stderr or '')[:1500]}
```

The current `{cli_name} --help` output is:

```
{help_text[:4000]}
```

Lope needs this CLI to accept a single prompt string on the command line or
on stdin, and respond with plaintext on stdout containing the model's answer.

Propose a corrected invocation and return ONLY a JSON object wrapped in a
triple-backtick json code fence. No prose before or after. Schema:

```json
{{
  "argv_template": ["<binary>", "<flag>", "{{prompt}}"],
  "stdin_mode": "none",
  "stdout_parser": "plaintext",
  "confidence": 0.85,
  "rationale": "one sentence"
}}
```

Rules:
- `argv_template` uses `{{prompt}}` as the placeholder where lope injects
  the prompt string. If the CLI reads prompts from stdin, set `stdin_mode`
  to `"pipe"` and omit `{{prompt}}` from argv.
- `stdout_parser` is `"plaintext"` if the CLI prints raw text, or
  `"json:path.to.field"` if it prints JSON and the response is at that path.
- `confidence` is your own estimate, 0.0 to 1.0. Be honest.
- `rationale` is one sentence on what you changed and why.

Return only the JSON. No other text."""


def _parse_heal_response(response: str) -> Optional[LearnedAdapter]:
    """Extract the JSON proposal from a reviewer's response.

    Tolerates: pure JSON, JSON in a ```json fence, JSON embedded in prose.
    """
    if not response:
        return None

    candidates: List[str] = []

    # Try ```json fence first
    fence_rx = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
    for m in fence_rx.finditer(response):
        candidates.append(m.group(1))

    # Fall back to greedy {...} match
    if not candidates:
        greedy = re.search(r"\{.*\}", response, re.DOTALL)
        if greedy:
            candidates.append(greedy.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        argv = data.get("argv_template")
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            continue
        return LearnedAdapter(
            argv_template=argv,
            stdin_mode=str(data.get("stdin_mode", "none")),
            stdout_parser=str(data.get("stdout_parser", "plaintext")),
            confidence=float(data.get("confidence", 0.0)),
        )
    return None


def _fill_template(template: str, prompt: str, cli_binary: str) -> str:
    """Expand `{prompt}` and `{binary}` placeholders in an argv template."""
    return template.replace("{prompt}", prompt).replace("{binary}", cli_binary)


def _extract_json_path(raw: str, path: str) -> Optional[str]:
    """Dot-path lookup into JSON stdout, used for json:<path> stdout_parser."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    current: Any = data
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None
    return str(current) if current is not None else None


def is_adapter_expired(adapter: LearnedAdapter, now: Optional[float] = None) -> bool:
    """True if a learned adapter is older than the 90-day TTL."""
    if now is None:
        now = time.time()
    if adapter.timestamp <= 0:
        return False
    return (now - adapter.timestamp) > LEARNED_ADAPTER_TTL_SECONDS
